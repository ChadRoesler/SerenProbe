"""
seren_probe.regrade_live
========================
Container-based regrade: sweep SCC fusion knobs against the LIVE SCC container
over HTTP - POST /configure to retune, POST /search to measure, grade the fused
packet, keep the best per set. NO seren_corpus_callosum import: the container
carries SCC (installed from PyPI); SerenProbe just drives it. This is the
config-driven path behind the Eval-tab ⚙ Regrades button.

Design vs the CLI capture-replay (regrade.py):
- CLI: capture the backing stores ONCE, replay 2500 combos in-process (needs SCC
  on the host). Cheap, but host-coupled.
- HERE: reconfigure the LIVE container per combo and re-search. No host SCC, uses
  the real container fusion - at the cost of an HTTP round-trip per combo, so the
  sets are kept COMPACT (a set's grid is the product of ONLY the knobs it names;
  unnamed knobs are RESET TO THE CAPTURED BASELINE before every combo - NOT left
  wherever the previous combo happened to leave them. /configure is cumulative, and
  a sweep whose combos inherit each other is not a sweep, it's a drift).

SAFETY: the container's config (k, n_results, per-store weight/floor - everything
GET /stores exposes) is captured before the sweep and RESTORED after, so a regrade
never leaves the container mistuned for the next /eval/run. The default sets sweep
only those readable knobs, so restore is exact.

CONCURRENCY: corpora run STRICTLY SERIALLY, and so do the combos within one
corpus's sweep. This used to run corpora in parallel on the reasoning that "each
is a separate SCC container, so nothing contends" -- which is exactly wrong, and
was wrong in live_eval for the same reason. An SCC container holds NO DATA. It
fans into member stores that other corpora also fan into (Characters-scc and
All-scc share every character's loci and memory; All-scc alone fans 22). Two
regrades in flight are two N-store fans hammering an overlapping set of
containers, and a regrade is worse than an eval here because it also POSTs
/configure between combos.

There is no safe width. The sharing is STRUCTURAL -- reaching into stores that
belong to someone else is the entire job description of a corpus -- so it does not
go away with a smaller pool, only with serialization. And a contended sweep does
not fail loudly; it returns degraded packets that grade as bad knob settings,
which is the worst possible outcome for a tool whose only job is to tell good knob
settings from bad ones.

max_parallel_corpora is honoured only as a floor of 1; anything higher is ignored
with a warning rather than silently obeyed. See run_live_regrade.
"""
from __future__ import annotations

import itertools
import json
import logging

from ..core.metrics import compute_metrics_batch, normalize_text

logger = logging.getLogger(__name__)

_METRICS = ("ndcg", "docket_coverage", "docket_density", "recall",
            "mrr", "hit_rate", "iou", "prec_omega")

# our CorpusRegrades knob name -> how it lands on SCC's /configure. Federation
# knobs go top-level; loci_weight/loci_floor are PER-STORE overrides on the loci
# store. (fusion_mode/authority_margin/min_per_store/fetch_multiplier are settable
# but NOT read back by GET /stores - the default sets avoid them so restore stays
# exact; a custom set using them gets a best-effort restore + a note.)
_FED_KNOB = {"rrf_k": "k", "n_results": "n_results", "fetch_multiplier": "fetch_multiplier",
             "authority_margin": "authority_margin", "min_per_store": "min_per_store",
             "fusion_mode": "fusion_mode",
             # Multi-hop. MUST be mapped here or configure_payload silently DROPS it,
             # sends an empty body, and every combo scores identically - the exact
             # "inert knob that reads as a ceiling" this harness exists to prevent.
             "hops": "hops", "hop_terms": "hop_terms", "hop_budget": "hop_budget"}
_STORE_KNOB = {"loci_weight": "weight", "loci_floor": "floor"}
_READBACK = {"rrf_k", "n_results", "loci_weight", "loci_floor",
             "hops", "hop_terms", "hop_budget"}   # GET /stores exposes these


def _post(url, path, body, timeout=30.0):
    # THE INTERLOCK -- see write_guard. /search is a POST but a READ and passes
    # freely; /configure mutates the SCC and is only permitted against a container
    # this topology owns.
    import httpx
    from .write_guard import assert_write_allowed
    assert_write_allowed(url, path, "POST")
    r = httpx.post(f"{url}{path}", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json() if r.content else {}


def _get(url, path, timeout=15.0):
    import httpx
    r = httpx.get(f"{url}{path}", timeout=timeout)
    return r.json() if (r.status_code == 200 and r.content) else {}


def compact_combos(overrides: dict) -> list[dict]:
    """A set's grid = the product over ONLY the knobs it names (unnamed knobs stay
    at the container's current value). Empty overrides -> a single no-op combo
    (measure the current config). Empty knob lists are dropped."""
    keys = [k for k in (overrides or {}) if overrides.get(k)]
    if not keys:
        return [{}]
    return [dict(zip(keys, vals)) for vals in itertools.product(*(overrides[k] for k in keys))]


def configure_payload(combo: dict, loci_name: str | None) -> dict:
    """Map one combo of our knobs onto an SCC /configure body.

    NOTE: this returns ONLY the combo's own knobs. Do not POST it directly in a
    sweep -- /configure is CUMULATIVE, so a set that names only `n_results` would
    inherit whatever the previous set's last combo left behind. Use
    full_config_body(), which lays the combo over the captured baseline. This
    function is kept as the pure knob->field mapping (and is what the grid tests
    exercise).
    """
    body: dict = {}
    store_ov: dict = {}
    for knob, val in combo.items():
        if knob in _FED_KNOB:
            body[_FED_KNOB[knob]] = val
        elif knob in _STORE_KNOB:
            store_ov[_STORE_KNOB[knob]] = val
    if store_ov and loci_name:
        body["stores"] = [{"name": loci_name, **store_ov}]
    return body


def full_config_body(combo: dict, loci_name: str | None, baseline: dict) -> dict:
    """The body to POST for ONE combo: the captured BASELINE config, with this
    combo's knobs laid over it. ALWAYS non-empty, so every combo is a full reset.

    This exists because /configure is CUMULATIVE and a set only names the knobs it
    sweeps -- so set N silently inherited set N-1's LAST combo. Observed live, and it
    is not theoretical: weight-sweep ended on loci_weight=10, and the very next set
    (packet-sweep, which names only n_results) measured EVERY row at weight=10 and
    reported the weight win as a packet win. Its n_results=10 row -- which IS the
    baseline config -- came back with a non-zero delta AGAINST ITSELF. That is the
    tell: a combo identical to baseline must have a delta of exactly zero, and when
    it doesn't, the state is leaking.

    It hid for as long as it did because weight-sweep used to end on 1.0 (the
    baseline value), so the leak restored the baseline by luck. Widening that list
    to 10.0 is what exposed it.

    A sweep is only a sweep if every combo starts from the same place.
    """
    body: dict = {k: v for k, v in baseline.items() if k != "stores"}
    stores = [dict(s) for s in baseline.get("stores", [])]
    store_ov: dict = {}
    for knob, val in combo.items():
        if knob in _FED_KNOB:
            body[_FED_KNOB[knob]] = val
        elif knob in _STORE_KNOB:
            store_ov[_STORE_KNOB[knob]] = val
    if store_ov and loci_name:
        for s in stores:
            if s.get("name") == loci_name:
                s.update(store_ov)
    if stores:
        body["stores"] = stores
    return body


def grade_corpus(hits: list, expected_content: list, k: int):
    """(retrieved, relevant, coverage, density) for one SCC packet vs a corpus
    question - content coverage, same shape as live_eval's SCC grading."""
    retrieved = [(h.get("id"), h.get("score", 0.0)) for h in hits]
    relevant: set = set()
    exp = [e for e in expected_content if normalize_text(e)]
    items_found = 0
    for e in exp:
        ne = normalize_text(e)
        for h in hits:
            if ne in normalize_text(str(h.get("content", ""))):
                relevant.add(h.get("id")); items_found += 1; break
    dens = 0
    for h in hits[:k]:
        c = normalize_text(str(h.get("content", "")))
        if any(normalize_text(e) in c for e in exp):
            dens += 1
    coverage = items_found / len(exp) if exp else 0.0
    density = dens / min(k, len(retrieved)) if retrieved else 0.0
    return retrieved, relevant, coverage, density


def _measure(scc_url: str, corpus_qs: list, n_results: int, k: int) -> dict:
    """Search every corpus question at the container's CURRENT config and grade."""
    results, covs, dens = [], [], []
    for q in corpus_qs:
        resp = _post(scc_url, "/search", {"query": q["query"], "n_results": n_results})
        hits = resp.get("hits", []) if isinstance(resp, dict) else []
        retr, rel, cov, den = grade_corpus(hits, q["expected_content"], k)
        results.append((retr, rel)); covs.append(cov); dens.append(den)
    agg = compute_metrics_batch(results, k=k).aggregate()
    agg["docket_coverage"] = sum(covs) / len(covs) if covs else 0.0
    agg["docket_density"] = sum(dens) / len(dens) if dens else 0.0
    return agg


def sets_for_corpus(corpus, base_regrades: list) -> list:
    """Which RegradeSets this corpus actually sweeps. The four rules:

      base only, corpus absent   -> base            (the common case)
      no base, corpus has own    -> corpus's own    (opt IN a few, sweep nothing else)
      neither                    -> []              (nothing to do)
      base AND corpus's own      -> both, DE-DUPLICATED by set name
      corpus explicitly []       -> []              (opt OUT of an existing base)

    ABSENT IS NOT EMPTY. A corpus with no `CorpusRegrades` key inherits the base;
    a corpus with an empty one is saying "not me". Collapsing those two makes it
    impossible to skip a single corpus once a base exists -- and skipping is the
    entire point, because corpora run serially and a sweep is minutes-to-hours
    each. Fourteen corpora swept to get at three is an afternoon spent on nothing.

    DE-DUPLICATION IS BY NAME, and the corpus wins. A corpus naming a set that
    already exists at the top level is OVERRIDING it, not asking for it twice --
    same shape as the per-node Seed/Questions overrides everywhere else in the
    config. Two sets with the same name in one result would also make the output
    rows ambiguous, since `name` is what the table keys on.
    """
    own = getattr(corpus, "regrades", None)
    if own is None:
        return list(base_regrades)
    if not own:                       # explicit [] -> opt out
        return []
    own_names = {r.name for r in own}
    # Corpus sets first: they are the specific intent, and a stable order keeps the
    # result table comparable between runs.
    return list(own) + [r for r in base_regrades if r.name not in own_names]


def _regrade_one_corpus(corpus, scc_url: str, corpus_qs: list, regrades: list,
                        all_override_keys: list, flag_map: dict, k: int,
                        sort_by: str, on_combo=None, on_partial=None) -> dict:
    """One corpus's full regrade: capture its current SCC config, force the
    baseline, sweep every CorpusRegrades set's combos, restore the baseline.
    Fully self-contained (its own scc_url, its own baseline/restore).

    Returns the same {"corpus", "flavor", "baseline", "sets"} shape the old
    inline loop body produced. Raises on failure -- the caller is responsible for
    catching per-corpus so one bad SCC doesn't sink the others.

    on_combo()  -- fired after EVERY measured combo, including the baseline. One
                   combo is one /configure plus a full pass over every corpus
                   question, so it is the only honest unit of progress here.
    on_partial(rows) -- fired after each SET finishes, with the sets measured so
                   far. A sweep is minutes-to-hours per corpus and corpora are
                   serial, so without this the whole thing is a black box that
                   either returns or doesn't.
    """
    # Computed UP FRONT rather than at the return, so a partial published
    # mid-sweep is the same shape as the final result and the viewer needs no
    # special case for "still running".
    flavor = ("vector" if any("vector" in flag_map.get(s.name, []) for s in corpus.stores)
              else "lexical")

    info = _get(scc_url, "/stores")
    # Refuse to sweep a knob this SCC can't do - an ignored knob yields
    # identical rows, which reads as a ceiling. Loud beats misleading.
    from ..core.knob_caps import assert_knobs_supported
    assert_knobs_supported(info, all_override_keys)
    cur_k = info.get("k", 60)
    cur_n = info.get("n_results", 10)
    # Capture the hop config too, or a hops-sweep would LEAVE the SCC on the
    # last value it tried - a silent state leak into everything that queries
    # it afterwards. Restore is only honest if it captures every knob it sweeps.
    cur_hops = info.get("hops")
    cur_hop_terms = info.get("hop_terms")
    cur_hop_budget = info.get("hop_budget")
    rows = info.get("stores", []) or []
    loci_row = next((s for s in rows if s.get("type") == "seren_loci"), None)
    loci_name = loci_row.get("name") if loci_row else None
    captured = [{"name": s.get("name"), "weight": s.get("weight", 1.0),
                 "floor": s.get("floor", 0.0)} for s in rows if s.get("name")]
    # The one config every combo is reset to, and the one we restore at the end.
    # Built ONCE so "what we reset to" and "what we restore to" cannot drift apart.
    baseline_cfg: dict = {"k": cur_k, "n_results": cur_n}
    if cur_hops is not None:
        baseline_cfg["hops"] = cur_hops
    if cur_hop_terms is not None:
        baseline_cfg["hop_terms"] = cur_hop_terms
    if cur_hop_budget is not None:
        baseline_cfg["hop_budget"] = cur_hop_budget
    if captured:
        baseline_cfg["stores"] = captured
    try:
        # Force the baseline before measuring it, so `current` is the config we
        # SAY it is rather than whatever the container drifted to.
        _post(scc_url, "/configure", baseline_cfg)
        combo_cache: dict[str, dict] = {}
        base = _measure(scc_url, corpus_qs, cur_n, k)
        if on_combo:
            on_combo()
        set_rows = [{"name": "current",
                     "metrics": {m: base.get(m, 0.0) for m in _METRICS},
                     "params": {"k": cur_k, "n_results": cur_n,
                                **({"hops": cur_hops} if cur_hops is not None else {})},
                     "delta": {m: 0.0 for m in ("ndcg", "docket_coverage", "recall", "mrr")}}]
        # Publish the baseline immediately. It is the row every delta is measured
        # against, so it is the single most useful number to see early -- and it is
        # available before a single knob has been turned.
        if on_partial:
            on_partial(list(set_rows))
        for rset in regrades:
            best = None
            combo_rows: list[dict] = []
            for combo in compact_combos(rset.overrides):
                # MEASURE EACH DISTINCT COMBO ONCE.
                #
                # A base set and a corpus set that both sweep loci_weight will overlap
                # on the values they share, and re-measuring an identical config is a
                # full pass over every corpus question for a number we already have.
                # The row is REUSED rather than skipped, so each set still reports its
                # own complete curve -- "show the curve" is not negotiable, but paying
                # twice for the same point is.
                ck = json.dumps(combo, sort_keys=True, default=str)
                cached = combo_cache.get(ck)
                if cached is not None:
                    row = dict(cached)
                else:
                    # Reset-then-override, EVERY combo. Never post just the combo's own
                    # knobs: /configure is cumulative and sets would inherit each other.
                    _post(scc_url, "/configure",
                          full_config_body(combo, loci_name, baseline_cfg))
                    n_for = combo.get("n_results", cur_n)
                    agg = _measure(scc_url, corpus_qs, n_for, k)
                    row = {"metrics": {m: agg.get(m, 0.0) for m in _METRICS}, "params": combo}
                    row["delta"] = {m: round(row["metrics"][m] - base.get(m, 0.0), 4)
                                    for m in ("ndcg", "docket_coverage", "recall", "mrr")}
                    combo_cache[ck] = row
                    if on_combo:
                        on_combo()
                combo_rows.append(row)
                if best is None or row["metrics"].get(sort_by, 0) > best["metrics"].get(sort_by, 0):
                    best = row
            if best:
                # COPY before attaching combos: `best` IS one of the dicts inside
                # combo_rows, so assigning combo_rows onto it in place would make
                # the structure self-referential and blow up json encoding.
                best = dict(best)
                best["name"] = rset.name
                # EVERY combo we measured, not just the winner. A sweep that reports
                # only max() is unfalsifiable: you cannot tell "hops=2 did nothing"
                # from "hops=2 did something the sort metric couldn't see." The whole
                # point of a sweep is the CURVE. Show the curve.
                best["combos"] = combo_rows
                set_rows.append(best)
                if on_partial:
                    on_partial(list(set_rows))
    finally:
        try:
            _post(scc_url, "/configure", baseline_cfg)
        except Exception:
            pass
    return {"corpus": corpus.name, "flavor": flavor, "baseline": "current", "sets": set_rows}


def run_live_regrade(topology, url_of: dict, questions, *, k: int = 10,
                     sort_by: str = "docket_coverage",
                     max_parallel_corpora: int = 8,
                     report_progress: bool = False) -> dict:
    """Roll every CorpusRegrades set against every (non-catch-all) corpus by
    reconfiguring the LIVE SCC container and re-searching. Returns per-corpus
    best-per-set + EVERY combo it measured + the delta vs the container's CURRENT
    config (the 'current' baseline row). Captures + restores the config per corpus.

    sort_by defaults to docket_coverage, NOT ndcg. Coverage is the SCC's mission
    metric: it divides matched docket items by the number the question ASKED for,
    so it answers "did the assembled briefing carry the ground" rather than "was
    the ranking pretty". A knob sweep on a fusion layer should be selected on
    completeness of the packet, which is the thing SCC exists to assemble.

    (This used to be justified differently, and that justification is now WRONG --
    it survived a fix to the thing it described. metrics._ndcg once returned 1.0
    when `relevant` was empty, so a combo that retrieved NOTHING scored a perfect
    ndcg and won the sweep. _ndcg now returns 0.0 in that case, deliberately, so
    unscorable questions score like HR and recall instead of inflating. The
    free-1.0 hazard is gone; the reason to prefer coverage is the one above.

    Note the flip side, because it bites: with _ndcg honest, a corpus question set
    carrying NO expect_content grades every combo at 0.000 in EVERY column --
    `relevant` on a corpus is derived from content matches, so no expect_content
    means no relevant, means a uniform zero floor and a sweep with nothing to
    sort. A flat regrade is the symptom of an unfed docket, not of inert knobs.)

    SERIAL ACROSS CORPORA, SERIAL WITHIN ONE. This used to fan corpora out in a
    pool on the reasoning that each is an independent SCC container. It is not:
    an SCC holds no data and reaches into MEMBER stores that other corpora reach
    into too. See the module docstring -- a contended sweep does not fail loudly,
    it grades the contention as a knob result, which is the worst outcome
    available to a tool whose only job is telling good settings from bad.

    A failing corpus does NOT abort the run: a corpus that fails to regrade (SCC
    down, knob unsupported) is read-only and corrupts nothing, so the rest still
    finish and report and the failed one shows up as an "error" entry.

    max_parallel_corpora is accepted for compatibility and ignored above 1, out
    loud, via a logged warning.

    report_progress publishes to runtime.progress: an X/Y combo counter per corpus
    (declared for EVERY corpus before the first knob turns, so the table renders
    complete and empty rather than growing a row at a time) plus partial result
    rows as each set lands. False by default so tests and non-UI callers don't pay
    for a registry nobody is polling.
    """
    corpus_qs = [{"query": q.query, "expected_content": list(q.expect_content)}
                 for q in questions
                 if getattr(q, "asks", "") == "corpus" and not getattr(q, "expect_empty", False)]
    regrades = list(topology.corpus_regrades or [])
    # Declare what we own this run -- /configure mutates the SCC, so the guard must
    # know which containers are ours before the first knob is turned.
    from .write_guard import allow_targets
    allow_targets(url_of.values())
    if not corpus_qs:
        return {"corpora": [], "note": "No corpus questions to regrade."}
    # NO early-out on an empty top-level `regrades`. That guard predates per-corpus
    # sets and would kill rule 2 outright -- "no base, a few corpora define their own"
    # is the whole opt-in workflow, and it arrives here with base_regrades == [].
    # Whether there is anything to do is decided per corpus by sets_for_corpus, below.

    flag_map = {n.name: (n.flags or []) for n in topology.loci}
    # all_override_keys is no longer computed globally: knob-capability must be
    # checked against the knobs a corpus ACTUALLY sweeps. See the per-corpus
    # `own_keys` below.

    tasks = []
    for corpus in topology.corpus:
        if getattr(corpus, "is_catchall", False) or not corpus.stores:
            continue
        scc_url = url_of.get(corpus.name)
        if not scc_url:
            continue
        # RESOLVE PER CORPUS, and drop the ones with nothing to sweep BEFORE they
        # become tasks. A corpus that opted out must not appear in the progress
        # table at all -- a row sitting at 0/0 forever is indistinguishable from a
        # hung one, and this whole feature exists so you can tell those apart.
        sets = sets_for_corpus(corpus, regrades)
        if not sets:
            logger.info("regrade: skipping %s (no CorpusRegrades sets apply)", corpus.name)
            continue
        tasks.append((corpus, scc_url, sets))
    if not tasks:
        return {"corpora": [], "note": (
            "No corpus has any CorpusRegrades sets that apply -- every corpus either "
            "opted out with an empty CorpusRegrades or there is no top-level set to "
            "inherit.")}

    corpora_out: list[dict] = []
    # SERIAL, ALWAYS. See the module docstring: corpora overlap on member stores, so
    # two sweeps in flight contend by construction and a contended sweep does not fail
    # loudly -- it returns degraded packets that grade as BAD KNOB SETTINGS. A regrade
    # harness that mistakes contention for tuning is worse than no harness.
    #
    # The knob is ignored rather than removed, and ignored OUT LOUD. A config value that
    # silently does nothing is how the next person spends an afternoon wondering why
    # their width setting changed no timings.
    _requested = int(max_parallel_corpora or 1)
    if _requested > 1 and len(tasks) > 1:
        logger.warning(
            "regrade: ignoring max_parallel_corpora=%d -- corpora share member "
            "containers, so parallel sweeps contend and grade the contention as a "
            "knob result. Running %d corpora serially.", _requested, len(tasks))

    # PRELOAD EVERY ROW BEFORE THE FIRST KNOB TURNS.
    #
    # Declaring all totals up front means the table renders COMPLETE and empty --
    # every corpus at 0/N with a real denominator -- instead of growing a row each
    # time one finishes. With corpora serialized, a fourteen-corpus sweep otherwise
    # shows one row for the first several minutes and gives no way to tell "working"
    # from "hung", which is the exact black box this is here to remove.
    #
    # The unit is a COMBO, not a question: one combo is a /configure plus a full
    # pass over every corpus question, so it is the coarsest unit that actually
    # advances at a steady rate. +1 for the baseline measurement, which is a real
    # measured pass and would otherwise make the bar start at 1/N having done nothing.
    _progress = None
    if report_progress:
        from . import progress as _progress
        for corpus, _u, _sets in tasks:
            # Per-corpus total, NOT one shared number. Sets differ per corpus now, so
            # a single figure would be wrong for every corpus that overrides. The
            # dedup cache can only make a sweep finish EARLY (under its total), never
            # overrun -- an honest ceiling beats a bar that jumps past 100%.
            total = 1 + sum(len(compact_combos(rs.overrides)) for rs in _sets)
            _progress.start(corpus.name, "regrade", total)

    for corpus, scc_url, sets in tasks:
        def _bump(_name=corpus.name):
            if _progress:
                _progress.bump(_name, 1)

        def _partial(rows, _name=corpus.name, _corpus=corpus):
            # Same shape as the final per-corpus result, so the viewer renders a
            # half-finished sweep through the same code path as a finished one.
            # flavor is computed up front inside _regrade_one_corpus for this reason.
            if _progress:
                _progress.publish(_name, {
                    "corpus": _name,
                    "flavor": ("vector" if any("vector" in flag_map.get(s.name, [])
                                              for s in _corpus.stores) else "lexical"),
                    "baseline": "current", "sets": rows, "running": True})

        try:
            # Knob-capability is checked against the knobs THIS corpus actually
            # sweeps, not the union across the whole config. A corpus inheriting a
            # plain rrf_k sweep must not be hard-errored because some OTHER corpus
            # opted into a hops set its SCC cannot do.
            own_keys = [ok for rs in sets for ok in rs.overrides]
            out = _regrade_one_corpus(
                corpus, scc_url, corpus_qs, sets, own_keys,
                flag_map, k, sort_by,
                on_combo=_bump if _progress else None,
                on_partial=_partial if _progress else None)
            corpora_out.append(out)
            if _progress:
                _progress.publish(corpus.name, out)
        except Exception as exc:  # noqa: BLE001 - one bad SCC must not sink the run
            logger.error("Regrade failed for corpus %s: %s", corpus.name, exc)
            err = {"corpus": corpus.name, "error": f"{type(exc).__name__}: {exc}"}
            corpora_out.append(err)
            # Publish failures too, and immediately. A corpus that died on knob 3 of
            # 40 should be visible NOW, not after the remaining thirteen corpora
            # finish -- an unsupported knob is exactly the thing you want to fix
            # before the rest of the sweep burns an hour on it.
            if _progress:
                _progress.publish(corpus.name, err)
        finally:
            if _progress:
                _progress.finish(corpus.name)

    return {"corpora": corpora_out, "sort_by": sort_by, "eval_k": k,
            # Set names are the UNION across corpora now, not one global list -- with
            # per-corpus overrides no single corpus necessarily ran all of them. Each
            # corpus's own `sets` is authoritative for what IT actually swept.
            "set_names": ["current"] + sorted(
                {rs.name for _c, _u, _s in tasks for rs in _s}),
            "question_count": len(corpus_qs)}
