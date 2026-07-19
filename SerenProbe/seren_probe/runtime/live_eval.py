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

CONCURRENCY: stores run IN PARALLEL (each is a separate container - loci, memory,
and corpus columns share nothing but the read-only ref_to_id/gt_notes lookups and
the lock-protected write_guard allowlist), and WITHIN one store, its own questions'
/search calls also fan out (bounded separately, since that's concurrent traffic
against the SAME container). Grading itself stays single-threaded per store - only
the network fetch is parallelized, so nothing needs to lock the per-store
accumulators. A failing store or a failing question's search does NOT abort the
run: eval is read-only, so isolating the failure and reporting it inline (an
"error" snapshot for a store, a scored miss for a question) beats losing every
other column over one flaky container. See run_topology_evaluation's docstring.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

import httpx

from ..core.metrics import compute_metrics_batch, normalize_text

logger = logging.getLogger(__name__)

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


# Fact resolution is a different animal from search: individually trivial, but the
# COUNT scales with members x keys. On a cross-everything corpus, qualified keys
# ('Cewellric-loci:combat/weapon') are deliberately distinct cache entries -- that
# is the whole point, one lookup per tenant instead of one shared answer for all of
# them -- so a column that used to resolve ~10 keys now resolves ~110, fired at loci
# containers already serving a 22-way fan. A bare 15s with no retry killed a
# 54-minute run on the last store in the topology. Idempotent GET, so retrying is free.
_FACT_TIMEOUT = float(os.environ.get("SEREN_PROBE_FACT_TIMEOUT", "45"))
_FACT_RETRIES = int(os.environ.get("SEREN_PROBE_FACT_RETRIES", "2"))


def _get_params(url: str, path: str, params: dict) -> dict:
    last: Exception | None = None
    for attempt in range(_FACT_RETRIES + 1):
        try:
            resp = httpx.get(f"{url}{path}", params=params, timeout=_FACT_TIMEOUT)
            return resp.json() if (resp.status_code == 200 and resp.content) else {}
        except httpx.TimeoutException as exc:
            # A contended store, not a broken one. Back off and ask again rather than
            # taking down an hour of work over one slow read.
            last = exc
            if attempt < _FACT_RETRIES:
                time.sleep(0.5 * (attempt + 1))
    # Out of retries. Return empty rather than raise: an unresolved key already has an
    # honest downstream story (gt_notes records it and the row scores as unresolved),
    # whereas an exception here aborts the ENTIRE topology eval over one fact lookup.
    # Losing one question beats losing every column.
    print(f"  WARN: /fact timed out after {_FACT_RETRIES + 1} attempts "
          f"({url} {params}): {last}", file=sys.stderr)
    return {}


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
                            seed: bool = True, max_parallel_stores: int = 8,
                            max_parallel_questions: int = 8) -> dict:
    """Eval every store in a compiled topology as a dynamic column against the
    uploaded questions. Seeds via seed_from_plan first (unless a seed_result is
    passed or seed=False). Transport is injectable for testing; defaults hit the
    real services over httpx.

    CONCURRENCY -- mirrors seeding's template (bounded ThreadPoolExecutor,
    width==1 runs thread-free, parallel-across / serial-within):

      max_parallel_stores: stores are independent containers, so they run
        concurrently up to this width. A store that raises does NOT sink the
        run -- it's read-only, so the failure is isolated and reported as an
        "error" snapshot for that column while every other store finishes and
        scores normally.

      max_parallel_questions: WITHIN one store, that store's own /search calls
        also fan out up to this width (bounded separately -- it's concurrent
        traffic against the SAME container, not independent containers). A
        question whose search blows up is isolated the same way: scored as a
        miss (empty hits) rather than losing the rest of that store's column.
        Grading stays single-threaded per store; only the network fetch is
        parallelized, so the accumulators below never need a lock.

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

    def fact_urls_for(name: str, kind: str) -> list[tuple[str, str]]:
        # (store_name, url) pairs, NOT bare urls. The name is what lets an
        # expect_key say WHICH tenant it means in a multi-tenant corpus -- see
        # resolve_key below. A list of anonymous urls cannot answer that.
        if kind == "seren_loci":
            return [(name, url_of[name])]
        if kind == "corpus":
            c = next((c for c in topology.corpus if c.name == name), None)
            return [(s.name, _loci_urls[s.name])
                    for s in (c.stores if c else []) if s.name in _loci_urls]
        return []          # memory has no /fact; expect_key on a memory question is already warned

    def eval_store(name, url, kind, qs, fact_urls, max_parallel_questions=8):
        _key_cache: dict[str, str] = {}
        _fact_by_store = dict(fact_urls)
        _ambiguous_keys: set[str] = set()

        def _split_qualified(pk):
            """'Edricmer-loci:stats/race' -> ('Edricmer-loci', 'stats/race').
            Anything else -> (None, pk). Only splits when the head names a store
            this column actually fans, so a stray colon in a key can't be
            mistaken for a qualifier."""
            if ":" in pk:
                head, rest = pk.split(":", 1)
                if head in _fact_by_store:
                    return head, rest
            return None, pk

        def _fetch_fact_id(furl, path_key):
            project, key = path_key.split("/", 1) if "/" in path_key else ("*", path_key)
            data = get_params(furl, "/fact", {"project": project, "key": key})
            return data.get("id", "") if isinstance(data, dict) else ""

        def resolve_key(pk):
            """expect_key -> the canonical Loci id, optionally store-qualified.

            WHY THE QUALIFIER EXISTS. Loci keys are CATEGORY-scoped since the
            category restructure -- 'stats/race', 'combat/weapon'. That is
            unambiguous while one store holds one tenant, which is true of every
            per-entity store. It stops being true the moment a corpus fans six
            characters: there are then six rows answering to 'stats/race', this
            loop used to take the FIRST member that replied, and _key_cache then
            pinned that id for every later question in the column. One question
            got the right answer key and five got another character's -- scoring
            as a false miss when the wrong row didn't rank and a false HIT when it
            did. Noise whose direction depends on member ordering, which is worse
            than a bug that fails honestly.

            So a question may name its tenant: 'Edricmer-loci:stats/race'. The
            shape deliberately mirrors resolve_ref, which has always tried
            '{store}:{ref}' before the bare handle.

            Bare keys keep the old loop, so nothing single-tenant changes.
            """
            if pk in _key_cache:
                return _key_cache[pk]
            store, bare = _split_qualified(pk)
            rid = ""
            if store is not None:
                rid = _fetch_fact_id(_fact_by_store[store], bare)
            else:
                if len(fact_urls) > 1:
                    _ambiguous_keys.add(pk)
                for _sname, furl in fact_urls:
                    rid = _fetch_fact_id(furl, bare)
                    if rid:
                        break
            _key_cache[pk] = rid
            return rid

        def holds_key(pk):
            """Does THIS column hold that exact fact? QUALIFIED ONLY when it could
            be ambiguous -- never the first-member fallback.

            Exactly the distinction holds_ref draws, and for exactly the same
            reason. resolve_key's fallback answers 'can anyone here resolve this
            key', which in a six-tenant corpus is true for 'stats/race' no matter
            whose race the question meant. Used as a leak test that marks EVERY
            quiet question carrying a common key as a leak, in every cross corpus,
            for a document none of them was asked about.

            'Can this key be resolved by someone' and 'does this store contain the
            specific row the question means' are different questions. Only the
            second one is a leak. When the key is bare and the column fans more
            than one loci, we cannot tell them apart -- so we decline to claim a
            leak and record the ambiguity instead. Refusing to answer beats
            answering confidently wrong.
            """
            store, bare = _split_qualified(pk)
            if store is not None:
                return bool(_fetch_fact_id(_fact_by_store[store], bare))
            if len(fact_urls) > 1:
                _ambiguous_keys.add(pk)
                return False
            return bool(resolve_key(pk))

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
        _search_lock = threading.Lock()
        def _search(query: str) -> list:
            ck = (query, k)
            with _search_lock:
                cached = _search_cache.get(ck)
            if cached is not None:
                return cached
            try:
                resp = post(url, "/search", _search_payload(kind, query, k))
                hits = resp.get("hits", []) if isinstance(resp, dict) else []
            except Exception as exc:
                # Isolated per-question failure: a flaky search scores as a miss
                # (empty hits), not a lost column -- eval is read-only.
                logger.warning("search failed for %r on %s: %s", query, name, exc)
                hits = []
            with _search_lock:
                _search_cache[ck] = hits
            return hits

        def _search_many(qlist) -> list:
            """Fan out /search across this store's own questions, bounded by
            max_parallel_questions. Order-preserving: results come back aligned
            with qlist regardless of completion order."""
            if len(qlist) <= 1 or max_parallel_questions <= 1:
                return [_search(q.query) for q in qlist]
            from concurrent.futures import ThreadPoolExecutor
            width = min(max_parallel_questions, len(qlist))
            with ThreadPoolExecutor(max_workers=width) as ex:
                return list(ex.map(lambda q: _search(q.query), qlist))

        normal_hits = _search_many(normal_qs)
        for q, hits in zip(normal_qs, normal_hits):
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
            empty_hits = _search_many(empty_qs)
            for hits in empty_hits:
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
            quiet_hits = _search_many(quiet_qs)
            for q, hits in zip(quiet_qs, quiet_hits):
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
                    leaked = (any(holds_key(pk) for pk in q.expect_key)
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

        # AMBIGUOUS GROUND TRUTH, SURFACED. A bare expect_key in a column that fans
        # more than one Loci cannot name which tenant it means. resolve_key still
        # falls back to first-member-wins so nothing single-tenant regresses, but
        # silence here is how a multi-tenant column reports confident noise. If this
        # list is non-empty, qualify those keys in the question set.
        if _ambiguous_keys:
            snap["ambiguous_keys"] = sorted(_ambiguous_keys)[:20]
            snap["ambiguous_key_count"] = len(_ambiguous_keys)
        return snap

    report: dict[str, dict] = {}

    # STORE-LEVEL FAN-OUT. Each entry is an independent container; build the full
    # job list first (store name, kind, node, eval callable) so the merge order
    # stays deterministic (loci, then memory, then corpus, each in topology order)
    # regardless of which job finishes first.
    jobs: list[tuple] = []
    for n in topology.loci:
        jobs.append((n.name, n, "loci",
                     lambda n=n: eval_store(n.name, url_of[n.name], "seren_loci",
                                            qs_for(n.name, "loci"), fact_urls_for(n.name, "seren_loci"),
                                            max_parallel_questions)))
    for n in topology.memory:
        jobs.append((n.name, n, "memory",
                     lambda n=n: eval_store(n.name, url_of[n.name], "seren_memory",
                                            qs_for(n.name, "memory"), fact_urls_for(n.name, "seren_memory"),
                                            max_parallel_questions)))
    for c in topology.corpus:
        jobs.append((c.name, c, "corpus",
                     lambda c=c: eval_store(c.name, url_of[c.name], "corpus",
                                            qs_for(c.name, "corpus"), fact_urls_for(c.name, "corpus"),
                                            max_parallel_questions)))

    def _run_job(job):
        name, node, kind, fn = job
        try:
            snap = fn()
            error = None
        except Exception as exc:
            logger.warning("eval failed for store %r: %s", name, exc)
            snap = {"kind": {"loci": "seren_loci", "memory": "seren_memory",
                              "corpus": "corpus"}[kind], "question_count": 0, "k": k}
            error = str(exc)
        if error is not None:
            snap["error"] = error
        snap["flags"] = node.flags
        if kind == "corpus":
            snap["is_catchall"] = node.is_catchall
        else:
            snap["negative_test"] = node.negative_test
        return name, snap

    if len(jobs) <= 1 or max_parallel_stores <= 1:
        for job in jobs:
            name, snap = _run_job(job)
            report[name] = snap
    else:
        from concurrent.futures import ThreadPoolExecutor
        width = min(max_parallel_stores, len(jobs))
        with ThreadPoolExecutor(max_workers=width) as ex:
            for name, snap in ex.map(_run_job, jobs):
                report[name] = snap

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
