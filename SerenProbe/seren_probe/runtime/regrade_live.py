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
"""
from __future__ import annotations

import itertools

from ..core.metrics import compute_metrics_batch, normalize_text

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


def run_live_regrade(topology, url_of: dict, questions, *, k: int = 10,
                     sort_by: str = "docket_coverage") -> dict:
    """Roll every CorpusRegrades set against every (non-catch-all) corpus by
    reconfiguring the LIVE SCC container and re-searching. Returns per-corpus
    best-per-set + EVERY combo it measured + the delta vs the container's CURRENT
    config (the 'current' baseline row). Captures + restores the config per corpus.

    sort_by defaults to docket_coverage, NOT ndcg. This is not a preference, it is a
    correctness fix. On a corpus store, ground truth is derived FROM THE HITS
    (grade_corpus only marks a hit relevant if that hit's content matched), so
    `relevant` is a subset of `retrieved` by construction -- and metrics._ndcg
    returns **1.0 when `relevant` is empty**. A combo that retrieves NOTHING
    therefore scores a PERFECT ndcg and wins the sweep. (Look at any negative-test
    decoy row on the dashboard: HR 0.000, everything 0.000, ndcg 1.000.) Sorting a
    knob sweep by ndcg selects for the combo that failed most gracefully.
    docket_coverage divides by the ground-truth count, so it is the only metric here
    that can actually see a miss.
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
    if not regrades:
        return {"corpora": [], "note": "No CorpusRegrades sets in the active ProbeConfig."}

    flag_map = {n.name: (n.flags or []) for n in topology.loci}
    corpora_out: list[dict] = []
    for corpus in topology.corpus:
        if getattr(corpus, "is_catchall", False) or not corpus.stores:
            continue
        scc_url = url_of.get(corpus.name)
        if not scc_url:
            continue
        info = _get(scc_url, "/stores")
        # Refuse to sweep a knob this SCC can't do - an ignored knob yields
        # identical rows, which reads as a ceiling. Loud beats misleading.
        from ..core.knob_caps import assert_knobs_supported
        assert_knobs_supported(info, [k for rs in topology.corpus_regrades
                                      for k in rs.overrides])
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
            base = _measure(scc_url, corpus_qs, cur_n, k)
            set_rows = [{"name": "current",
                         "metrics": {m: base.get(m, 0.0) for m in _METRICS},
                         "params": {"k": cur_k, "n_results": cur_n,
                                    **({"hops": cur_hops} if cur_hops is not None else {})},
                         "delta": {m: 0.0 for m in ("ndcg", "docket_coverage", "recall", "mrr")}}]
            for rset in regrades:
                best = None
                combo_rows: list[dict] = []
                for combo in compact_combos(rset.overrides):
                    # Reset-then-override, EVERY combo. Never post just the combo's own
                    # knobs: /configure is cumulative and sets would inherit each other.
                    _post(scc_url, "/configure",
                          full_config_body(combo, loci_name, baseline_cfg))
                    n_for = combo.get("n_results", cur_n)
                    agg = _measure(scc_url, corpus_qs, n_for, k)
                    row = {"metrics": {m: agg.get(m, 0.0) for m in _METRICS}, "params": combo}
                    row["delta"] = {m: round(row["metrics"][m] - base.get(m, 0.0), 4)
                                    for m in ("ndcg", "docket_coverage", "recall", "mrr")}
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
        finally:
            try:
                _post(scc_url, "/configure", baseline_cfg)
            except Exception:
                pass
        flavor = ("vector" if any("vector" in flag_map.get(s.name, []) for s in corpus.stores)
                  else "lexical")
        corpora_out.append({"corpus": corpus.name, "flavor": flavor,
                            "baseline": "current", "sets": set_rows})
    return {"corpora": corpora_out, "sort_by": sort_by, "eval_k": k,
            "set_names": ["current"] + [r.name for r in regrades],
            "question_count": len(corpus_qs)}
