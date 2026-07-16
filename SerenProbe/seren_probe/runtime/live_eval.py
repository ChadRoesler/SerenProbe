"""
seren_probe.live_eval - topology-driven evaluation against live HTTP stores.
════════════════════════════════════════════════════════════════════════════

Evals EVERY store in a compiled topology as its own dynamic column, scoring the
uploaded questions with honest ground truth:

    expect_key     -> Loci canonical id via GET /fact  (retrieval-INDEPENDENT:
                      a deterministic key can be re-resolved by anyone, forever)
    expect_ref     -> Memory minted id via the SeedResult, or rebuilt from the
                      live store by exact-text match if the pod was adopted
    expect_content -> substring on hits. RELATIVE ground truth -- it can only
                      mark hits you already got, so it can never see a miss.
                      Measure coverage with it; never conclude a store is broken.

WHAT USED TO LIVE HERE, AND WHY IT DOESN'T ANYMORE
──────────────────────────────────────────────────
This module also carried a legacy "hardcoded five stores" evaluator:
`main()`, `run_live_evaluation()`, `seed_loci/seed_memory/seed_scc`, the
`run_*_queries` trio, module-level constants pointing at

    MEMORY_URL      = "http://localhost:7420"     <- the operator's REAL memory
    LOCI_VEC_URL    = "http://localhost:7421"     <- the operator's REAL loci
    ...

and a `from .dataset import ...` at module scope, which pulled the SYNTHETIC
corpus into the import graph of every single code path that touched an eval.
`run_live_evaluation` would SEED those live stores if it found them empty, and
`routes/eval.py` fell through to it whenever no topology was up.

That is how a synthetic corpus ends up inside a real, in-use SerenMemory. It
is not hypothetical; it happened, and the cleanup took hours.

All of it is superseded by `run_topology_evaluation`, which only ever touches
containers SerenProbe spun up itself. The legacy code is in `_attic/`, the route
now returns a 400 instead of reaching for the default ports, and `write_guard`
refuses the write at the transport even if someone finds a fifth way in.

Do not reintroduce a module-level default that names a live port. There is no
safe way to hold that.
"""
from __future__ import annotations

import os

import httpx

from ..core.metrics import compute_metrics_batch, normalize_text

# A /search on a cold vector store runs ~6s; a CORPUS search fans N stores and does N of
# them, so cross-everything (6+ stores) can legitimately exceed 30s on a first hit. 30s
# was fine for a 5-store topology and became the thing that killed a 62-minute run one
# search short of the finish. This is the READ ceiling for a single retrieval, not a
# budget for the whole eval -- generous on purpose, override with SEREN_PROBE_SEARCH_TIMEOUT.
_SEARCH_TIMEOUT = float(os.environ.get("SEREN_PROBE_SEARCH_TIMEOUT", "120"))


# ── Transport ─────────────────────────────────────────────────────────────

def post(url: str, path: str, body: dict) -> dict:
    # THE INTERLOCK. Every mutating request in SerenProbe passes through here or
    # regrade_live._post, and both refuse any store the running topology does not
    # own. /search is a POST but a READ, so it passes; everything else must be a
    # container SerenProbe spun up itself. See write_guard for why this is an
    # invariant rather than a rule.
    from .write_guard import assert_write_allowed
    assert_write_allowed(url, path, "POST")
    # /search is a fan-out read on a corpus and can take minutes on a cold cross-store
    # SCC; a write is quick. Give the slow read its own budget, keep writes tight.
    timeout = _SEARCH_TIMEOUT if path.rstrip("/").endswith("/search") or path == "/search" else 30.0
    resp = httpx.post(f"{url}{path}", json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def get(url: str, path: str) -> dict:
    resp = httpx.get(f"{url}{path}", timeout=30.0)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _delete(url: str, path: str) -> None:
    from .write_guard import assert_write_allowed
    assert_write_allowed(url, path, "DELETE")
    httpx.delete(f"{url}{path}", timeout=10.0)


def _get_params(url: str, path: str, params: dict) -> dict:
    resp = httpx.get(f"{url}{path}", params=params, timeout=15.0)
    return resp.json() if (resp.status_code == 200 and resp.content) else {}


def _search_payload(kind: str, query: str, k: int) -> dict:
    if kind == "seren_loci":
        # No `project`, on purpose. One store = one tenant here, so an unscoped search of
        # this store returns exactly that tenant's facts plus fundamentals ('*'). Isolation
        # is the STORE BOUNDARY, not a query parameter -- a scope you have to remember to
        # send is a scope you can forget to send, and forgetting it looks like a good score.
        # See the long note in seed_dataset.py next to Question.
        return {"query": query, "n_results": k,
                "include_fundamentals": True, "include_superseded": False}
    if kind == "seren_memory":
        return {"query": query, "n_results": k, "include_short": True,
                "include_near": True, "include_long": True, "include_superseded": False}
    return {"query": query, "n_results": k}   # corpus: the FAN is the scope


def _loci_haystack(h: dict) -> str:
    return " ".join([str(h.get("value", "")), str(h.get("why", "") or ""), str(h.get("key", ""))])


def _grade(hits, q, kind, resolve_key, resolve_ref, k):
    """(retrieved, relevant, coverage, density) for one query vs one store.
    Honest ground truth from expect_key (loci canonical id) + expect_ref (memory
    minted id); content-match (relative) adds matched hit ids on top."""
    retrieved = [(h["id"], h.get("score", 0.0)) for h in hits]
    relevant: set[str] = set()
    for pk in q.expect_key:
        rid = resolve_key(pk)
        if rid:
            relevant.add(rid)
    for ref in q.expect_ref:
        rid = resolve_ref(ref)
        if rid:
            relevant.add(rid)
    items_found = 0
    n_exp = len(q.expect_content)
    for exp in q.expect_content:
        ne = normalize_text(exp)
        if not ne:
            continue
        for h in hits:
            hay = _loci_haystack(h) if kind == "seren_loci" else str(h.get("content", ""))
            if ne in normalize_text(hay):
                relevant.add(h["id"])
                items_found += 1
                break
    density_hits = 0
    if q.expect_content:
        for h in hits[:k]:
            hay = _loci_haystack(h) if kind == "seren_loci" else str(h.get("content", ""))
            nh = normalize_text(hay)
            if any(normalize_text(e) and normalize_text(e) in nh for e in q.expect_content):
                density_hits += 1
    coverage = items_found / n_exp if n_exp > 0 else 0.0
    density = density_hits / min(k, len(retrieved)) if retrieved else 0.0
    return retrieved, relevant, coverage, density


def run_topology_evaluation(topology, url_of, questions, *, seed_by_store=None,
                            seed_result=None, questions_by_store=None,
                            k: int = 10, post=post, delete=_delete, get_params=_get_params,
                            seed: bool = True) -> dict:
    """Eval every store in a compiled topology as a dynamic column against the
    uploaded questions. Seeds via seed_from_plan first (unless a seed_result is
    passed or seed=False). Transport is injectable for testing; defaults hit the
    real services over httpx.

    Returns {stores: {name: snapshot+kind+flags}, question_count, topology, k, date}.
    """
    from datetime import datetime
    from ..core.seed_dataset import seed_from_plan, rehydrate_ref_map
    from .write_guard import allow_targets

    # Declare what we own THIS RUN. url_of holds exactly the containers the
    # topology spun up -- nothing else is writable from here on. Everything that
    # mutates a store goes through live_eval.post/_delete, which refuse anything
    # not in this set. An empty topology therefore writes to NOTHING, which is the
    # correct default for a tool whose job is to generate fake data.
    allow_targets(url_of.values())

    if seed and seed_result is None and seed_by_store is not None:
        seed_result = seed_from_plan(topology, seed_by_store, url_of, post=post, delete=delete)
    ref_to_id = seed_result.ref_to_id if seed_result else {}
    gt_notes: list[str] = []

    # GROUND-TRUTH GUARD. expect_ref resolves through ref_to_id, which is minted at
    # SEED time and lives only in the seeding process's RAM. But a pod is flagged
    # seeded=True after its FIRST eval and never re-seeded after -- so from run #2
    # onward (and on every ADOPTED pod) this map was {} and resolve_ref returned ""
    # for every ref. `relevant` came back empty and the memory column reported a
    # PERFECTLY HEALTHY store as dead: HR 0.083 while that store answered the query
    # at rank 1 with score 0.649. It did this SILENTLY, for a full day.
    #
    # Loci was immune the whole time because expect_key re-resolves LIVE via GET
    # /fact. Ground truth that lives in a process is not ground truth, it's a receipt.
    # So: rebuild the map from the live store, and if we still can't, REFUSE TO SCORE.
    # A missing answer key is a broken harness -- never a failing store.
    needed_refs = {r for q in questions for r in q.expect_ref}

    def _ref_resolves(ref: str) -> bool:
        """MIRROR resolve_ref EXACTLY. seed_from_plan records each ref twice -- once
        namespaced ("store:ref") and once bare -- and resolve_ref tries the namespaced
        form FIRST. A guard that only checked the bare key called a perfectly
        resolvable ref missing. A guard that doesn't agree with the thing it guards is
        worse than no guard: it fails the healthy case loudly and lets nothing through.
        """
        if ref_to_id.get(ref):
            return True
        suffix = f":{ref}"
        return any(key.endswith(suffix) and val for key, val in ref_to_id.items())

    if needed_refs and not ref_to_id and seed_by_store:
        ref_to_id, unresolved = rehydrate_ref_map(topology, seed_by_store, url_of, post)
        if ref_to_id:
            gt_notes.append(
                f"stores were already seeded, so the ref->id map was rebuilt from the live "
                f"store by exact-text match ({len(ref_to_id) // 2 or len(ref_to_id)} refs bound). "
                f"expect_ref ground truth is intact.")
        if unresolved:
            gt_notes.append(
                f"WARNING: {len(unresolved)} seeded ref(s) could NOT be bound to a live row "
                f"({', '.join(unresolved[:6])}{'...' if len(unresolved) > 6 else ''}). Questions "
                f"expecting them are being scored against a MISSING answer key - treat those "
                f"rows as unscored, not as misses.")
    if needed_refs:
        missing = sorted(r for r in needed_refs if not _ref_resolves(r))
        if len(missing) == len(needed_refs):
            raise RuntimeError(
                f"GROUND TRUTH MISSING: {len(needed_refs)} question(s) score via expect_ref, but "
                f"NOT ONE ref resolves to a live row. Every memory question would be graded "
                f"against an empty answer key and a healthy store would report as dead. "
                f"Refusing to score. (Seed a fresh pod, or check that the memory seed items "
                f"still carry their `ref:` handles.)")
        if missing:
            gt_notes.append(f"WARNING: {len(missing)} of {len(needed_refs)} expect_ref handles "
                            f"do not resolve: {', '.join(missing[:6])}")

    # Live import: any node with a LiveStoreUrl gets its REAL data copied into the
    # container store (read-only on the live source - only GETs). Runs alongside
    # synthetic seeding; a live node was excluded from seed_by_store by the resolver,
    # so this is the ONLY thing that populates it.
    live_import_report: dict = {}
    if seed and any(getattr(n, "live_url", None) for n in topology.loci + topology.memory):
        from .live_import import import_live_stores
        live_import_report = import_live_stores(topology, url_of)

    by_kind = {"loci": [], "memory": [], "corpus": []}
    for q in questions:
        if q.asks in by_kind:
            by_kind[q.asks].append(q)

    def qs_for(name: str, kind_key: str) -> list:
        """The set THIS store is scored on.

        The resolver decides it (own Questions -> that set, whole; no Questions -> the
        DefaultQuestions filtered by `asks`, exactly as before). We do not re-derive it
        here, because the corpus case is not derivable from `questions` alone: a corpus's
        set is its own cross-store questions PLUS everything its members answer, and only
        the resolver knows which members declared a set.

        No map at all (a direct caller, an uploaded question list) -> fall back to `asks`,
        which is the pre-scoping behaviour, unchanged.
        """
        if questions_by_store is not None and name in questions_by_store:
            return questions_by_store[name]
        return by_kind[kind_key]

    # WHERE expect_key CAN BE RE-RESOLVED, per store.
    #
    # expect_key is honest ground truth precisely because it re-resolves LIVE: GET /fact
    # with a deterministic (project, key) can be looked up by anyone, forever. But the
    # lookup has to go to a store that HAS /fact -- and an SCC does not. It fuses; it does
    # not serve facts.
    #
    # This never mattered while corpus questions used expect_content. It matters enormously
    # now: a corpus INHERITS its members' questions (that is the dilution measure), and the
    # Loci half of those score via expect_key. Point them at the SCC's own url and every
    # one resolves to "", `relevant` comes back empty, and the fusion reads as a total
    # collapse -- the exact ghost the ref-map bug wore for a full day.
    #
    # So a corpus resolves keys against the LOCI IT FANS. The ids line up because
    # FusedHitOut carries the store-native id straight through the merge.
    _loci_urls = {n.name: url_of[n.name] for n in topology.loci if n.name in url_of}

    def fact_urls_for(name: str, kind: str) -> list[str]:
        if kind == "seren_loci":
            return [url_of[name]]
        if kind == "corpus":
            c = next((c for c in topology.corpus if c.name == name), None)
            return [_loci_urls[s.name] for s in (c.stores if c else []) if s.name in _loci_urls]
        return []          # memory has no /fact; expect_key on a memory question is already warned

    def eval_store(name, url, kind, qs, fact_urls):
        _key_cache: dict[str, str] = {}
        def resolve_key(pk):
            if pk in _key_cache:
                return _key_cache[pk]
            project, key = pk.split("/", 1) if "/" in pk else ("*", pk)
            rid = ""
            for furl in fact_urls:
                data = get_params(furl, "/fact", {"project": project, "key": key})
                rid = data.get("id", "") if isinstance(data, dict) else ""
                if rid:
                    break
            _key_cache[pk] = rid
            return rid
        def resolve_ref(ref):
            return ref_to_id.get(f"{name}:{ref}") or ref_to_id.get(ref) or ""

        def holds_ref(ref):
            """Does THIS store hold that ref? NAMESPACED ONLY -- never the bare fallback.

            resolve_ref falls back to the bare key on purpose (any store may answer a
            question, and the bare handle is how a shared ref binds). For a LEAK check that
            fallback is poison: mem-hermit asking for `evt-047` would hit the bare key,
            resolve it to MEM-GRISHNAK's minted id, and report the hermit as leaking a
            document he has never held. The quiet test would fail on every store in the
            topology, for a fact none of them has.

            "Can this ref be resolved by anyone" and "does this store contain it" are
            different questions. Only the second one is a leak.
            """
            return bool(ref_to_id.get(f"{name}:{ref}"))

        def _quiet_targets(q):
            from ..core.seed_dataset import quiet_targets_for
            return quiet_targets_for(q, name)

        # A question naming THIS store in quiet_in is EXCLUDED from normal_qs. It must be,
        # or the hermit gets graded twice on the same query: once as a hit_rate MISS (he
        # doesn't have the tavern brawl, correctly) and once as a quiet PASS (he didn't
        # surface it, correctly). The same right answer would show up as a failure in the
        # headline column and a success in the small one. A correct silence never touches
        # hit_rate -- that is the whole reason the quiet column exists.
        normal_qs = [q for q in qs
                     if not getattr(q, "expect_empty", False) and not _quiet_targets(q)]
        empty_qs = [q for q in qs if getattr(q, "expect_empty", False)]
        # Quiet questions are drawn from ALL questions, not from `qs`. `qs` is filtered by
        # `asks` (the KIND a question is scored against), but a quiet target is named
        # EXPLICITLY by store -- a memory-asks question can perfectly well name a corpus as
        # a store that should stay out of it.
        quiet_qs = [q for q in questions if _quiet_targets(q)]

        results, coverages, densities = [], [], []
        pos_tops: list[float] = []      # this store's top score on questions it SHOULD answer
        # PER-STORE SEARCH CACHE. A store gets searched for the same query text more than
        # once across a run -- a normal question and a quiet question can share a query, and
        # on the cross corpora many quiet questions ARE the same query. Each /search on a
        # fanning SCC costs seconds; issuing it twice is pure waste. Key on (query, k) --
        # the only two things that change the result for a given store.
        _search_cache: dict[tuple, list] = {}
        def _search(query: str) -> list:
            ck = (query, k)
            if ck in _search_cache:
                return _search_cache[ck]
            resp = post(url, "/search", _search_payload(kind, query, k))
            hits = resp.get("hits", []) if isinstance(resp, dict) else []
            _search_cache[ck] = hits
            return hits

        for q in normal_qs:
            hits = _search(q.query)
            if hits:
                pos_tops.append(float(hits[0].get("score", 0.0) or 0.0))
            retrieved, relevant, cov, den = _grade(hits, q, kind, resolve_key, resolve_ref, k)
            results.append((retrieved, relevant)); coverages.append(cov); densities.append(den)
        m = compute_metrics_batch(results, k=k)
        if kind == "corpus":
            m.docket_coverages = coverages
            m.docket_densities = densities
        snap = m.snapshot()
        snap["kind"] = kind; snap["question_count"] = len(normal_qs); snap["k"] = k

        # expect_empty (no-answer) questions: PASS = the store stays quiet (0 hits).
        # Scored SEPARATELY so a correct silence doesn't drag down hit_rate. A store
        # that always returns k (raw vector) can't abstain -> scores 0 here; that's
        # the signal. Lexical Loci / a floored SCC can return nothing -> can pass.
        if empty_qs:
            passes = 0
            for q in empty_qs:
                hits = _search(q.query)
                if not hits:
                    passes += 1
            snap["empty_count"] = len(empty_qs)
            snap["empty_passes"] = passes
            snap["empty_pass_rate"] = passes / len(empty_qs)

        # quiet_in (NON-LEAKAGE) questions: PASS = this store does not SURFACE the answer.
        #
        # This is NOT expect_empty, and the difference is the whole point. expect_empty
        # grades on `if not hits` -- zero rows -- which a vector store can never produce,
        # because Chroma always hands back the k nearest neighbours no matter how far away
        # they are. Ask the hermit in the next town about a tavern he has never heard of and
        # he WILL return five rows. About his goats. That is the correct answer, and under
        # `if not hits` it is a failure.
        #
        # So we grade on CONTENT, at inverted polarity, against the SAME expect_* ground
        # truth the positive question already carries: the phrase mem-grishnak must FIND is
        # exactly the phrase mem-hermit must NOT surface. The quiet test needs no forbidden-
        # phrase list of its own; expect_content already is one.
        if quiet_qs:
            import statistics
            base = statistics.median(pos_tops) if pos_tops else None
            passes = 0
            margins: list[float] = []
            leaked_queries: list[str] = []
            for q in quiet_qs:
                hits = _search(q.query)
                leaked = False
                for h in hits[:k]:
                    hay = _loci_haystack(h) if kind == "seren_loci" else str(h.get("content", ""))
                    nh = normalize_text(hay)
                    if any(normalize_text(e) and normalize_text(e) in nh for e in q.expect_content):
                        leaked = True
                        break
                # HARD leak: the expected document literally lives in this store. Caught even
                # if retrieval failed to surface it this time -- a store that HOLDS the answer
                # has already lost the quiet test; whether it happened to rank it is luck.
                if not leaked:
                    leaked = (any(resolve_key(pk) for pk in q.expect_key)
                              or any(holds_ref(r) for r in q.expect_ref))
                if leaked:
                    leaked_queries.append(q.query)
                else:
                    passes += 1
                # quiet_margin: SELF-CALIBRATED, never an absolute score floor. A fixed
                # threshold is embedder-bound and paper-thin (the same reason a static floor
                # was rejected in SCC fusion), so instead we compare this store's confidence on
                # a thing it should NOT know against its own median confidence on things it
                # SHOULD. Positive margin = visibly less sure about the tavern than about its
                # goats. It separates two outcomes that both pass: "returned junk, junk scores"
                # (healthy) from "returned junk, CONFIDENT scores" (the embedder is lying and
                # you want to know before you trust it anywhere else).
                if base is not None and hits:
                    margins.append(base - float(hits[0].get("score", 0.0) or 0.0))
            snap["quiet_count"] = len(quiet_qs)
            snap["quiet_passes"] = passes
            snap["quiet_rate"] = passes / len(quiet_qs)
            if leaked_queries:
                snap["quiet_leaks"] = leaked_queries[:10]
            if margins:
                snap["quiet_margin"] = sum(margins) / len(margins)
        return snap

    report: dict[str, dict] = {}
    for n in topology.loci:
        report[n.name] = eval_store(n.name, url_of[n.name], "seren_loci",
                                    qs_for(n.name, "loci"), fact_urls_for(n.name, "seren_loci"))
        report[n.name]["flags"] = n.flags
        report[n.name]["negative_test"] = n.negative_test
    for n in topology.memory:
        report[n.name] = eval_store(n.name, url_of[n.name], "seren_memory",
                                    qs_for(n.name, "memory"), fact_urls_for(n.name, "seren_memory"))
        report[n.name]["flags"] = n.flags
        report[n.name]["negative_test"] = n.negative_test
    for c in topology.corpus:
        report[c.name] = eval_store(c.name, url_of[c.name], "corpus",
                                    qs_for(c.name, "corpus"), fact_urls_for(c.name, "corpus"))
        report[c.name]["flags"] = c.flags
        report[c.name]["is_catchall"] = c.is_catchall

    result = {"stores": report, "question_count": len(questions),
              "topology": {"loci": [n.name for n in topology.loci],
                           "memory": [n.name for n in topology.memory],
                           "corpus": [c.name for c in topology.corpus]},
              "k": k, "date": datetime.utcnow().isoformat()}
    if gt_notes:
        result["ground_truth"] = gt_notes
    if live_import_report:
        result["live_import"] = live_import_report
    # Pair the SCC columns and attach the with/without-edges docket delta
    # (the 'did the briefing still have all the relevant info without the
    # semantic edges' view). Read flavor authoritatively off the topology.
    from ..core.docket import docket_comparison
    result["docket"] = docket_comparison(result, topology)
    return result
