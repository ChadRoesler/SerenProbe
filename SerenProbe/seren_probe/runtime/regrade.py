"""
seren_probe.regrade
=================
Capture-once / regrade-many SCC tuning.

The fusion knobs - RRF k, per-store weight, per-store floor, authority_margin,
min_per_store, fusion_mode - ALL operate on the candidates the stores already
returned. The stores hand back the same hits no matter how you fuse them. So we
query each store ONCE per query, freeze the raw responses (the "trace"), and then
replay the REAL Federation against that frozen capture for every knob combo,
in-process. No service restarts, no re-queries. A 300-point grid that used to be
300 service bounces becomes a sub-second in-process sweep.

This is ds4-eval's regrade-trace pattern: capture the store outputs once, re-score
as the knobs twist. It reuses SCC's OWN transport seam - the capture is a
CapturingTransport wrapping real httpx; the replay is a ReplayTransport feeding
the SAME Federation/fusion code that runs in production (the seam the SCC unit
tests already use). The only thing a regrade can NOT cover is a change to what the
stores RETURN - the embedder, Loci-hybrid on/off, include-flags - and that's a
fresh capture, which is exactly the no-vector-vs-vector (NV/V) axis.

══ SAFETY (read this) ════════════════════════════════════════════════════════
- READ-ONLY: this harness issues ONLY POST /search. It NEVER seeds, writes,
  supersedes, or mutates a store. (The seeding lives in seren_probe.runner, NOT
  here.) Capture is a pure read.
- NO PROD DEFAULTS: you must pass --memory-url and --loci-url explicitly, or run
  fully offline with --load-capture. Point it at DEV stores, never a live brain -
  pulling a synthetic eval corpus through your real continuity layer is exactly
  what we don't want.
- OFFLINE MODE: --save-capture writes the frozen trace to JSON; --load-capture
  re-sweeps that trace with zero store contact. Capture once on the dev rig,
  then tune all day with nothing live attached.

══ EDGES NOTE ════════════════════════════════════════════════════════════════
Topic-edges are packet-coupled: their /by_topic call depends on the regraded
packet's center topics AND its ids, so they can't be swept orthogonally from a
single /search capture. v1 sweeps the CORE fusion knobs with edges OFF - which is
faithful for top-k metrics anyway, since edges ride the tail PAST n_results and
never enter top-k when k == n_results. Edge tuning wants its own capture (capture
/by_topic with exclude=[] + the union of candidate topics, then recompute overlap
per requested subset in replay); that's tune_edges, filed as the next step.

══ CONCURRENCY ════════════════════════════════════════════════════════════════
run_config_regrade captures corpora ONE AT A TIME. It used to fan out via
asyncio.gather bounded by max_parallel_corpora, on the premise that "each corpus
captures its own backing stores, so nothing contends." That premise is false:
corpora OVERLAP on member stores (Characters-scc and All-scc both capture
Cewellric-loci; All-scc captures all 22). Concurrent captures are concurrent
/search against the SAME containers.

This path fails WORSE than the live one when it happens. There the contention
spoils one combo; here the capture is FROZEN and every combo in that corpus's
sweep is graded against it -- so one contended capture silently poisons an entire
sweep, and the sweep afterwards looks perfectly clean because it is replaying
from memory with no network in sight.

Combos within one corpus's sweep are serial and always were. A corpus that fails
to capture/sweep does not abort the others - read-only means a failure here can't
corrupt anything, so the rest still finish and the failed corpus shows up as an
"error" entry.

Usage::
    # capture on the dev rig + sweep, save the trace for later
    python -m serenprobe.regrade \\
        --memory-url http://127.0.0.1:7420 \\
        --loci-nv-url http://127.0.0.1:7422 \\
        --loci-v-url  http://127.0.0.1:7421 \\
        --save-capture /tmp/scc_capture.json

    # later, re-sweep the frozen trace with NOTHING live attached
    python -m seren_probe.regrade --load-capture /tmp/scc_capture.json
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

# SerenCorpusCallosum is the heavy backend regrade replays. Import it LAZILY so
# that `import serenprobe` (and the store-path runner / longmemeval, which need no
# SCC) works on a box without SCC installed - same graceful-degrade move as
# seren-memory[mcp]. The names resolve to None when SCC is absent; the
# value-using entry points call _require_scc() and fail LOUDLY with a fix hint
# instead of a cryptic ModuleNotFoundError at package import. `from __future__
# import annotations` makes the StoreConfig/FederationConfig type hints strings,
# so the None-valued names are never evaluated for annotations.
try:
    from seren_corpus_callosum.adapters import build_adapter
    from seren_corpus_callosum.config import FederationConfig, StoreConfig
    from seren_corpus_callosum.federation import Federation
    _SCC_AVAILABLE = True
    _SCC_IMPORT_ERROR: Optional[Exception] = None
except ImportError as exc:  # SCC not installed in this environment
    build_adapter = None       # type: ignore[assignment]
    FederationConfig = None     # type: ignore[assignment]
    StoreConfig = None          # type: ignore[assignment]
    Federation = None           # type: ignore[assignment]
    _SCC_AVAILABLE = False
    _SCC_IMPORT_ERROR = exc

from ..core.metrics import compute_metrics_batch, grade_against_content


def _require_scc() -> None:
    """Guard for the SCC-backed entry points. Regrade replays the REAL Federation,
    so a sweep/capture genuinely needs seren_corpus_callosum present. Raise a clear,
    actionable error instead of letting a None name explode mid-call."""
    if not _SCC_AVAILABLE:
        raise RuntimeError(
            "seren_probe.regrade needs the 'seren_corpus_callosum' package (the SCC "
            "Federation it replays), which isn't importable here. Install SCC into "
            "this environment to run captures/sweeps. "
            f"(original import error: {_SCC_IMPORT_ERROR!r})")


# ── the knob grid (in-process, so a wider grid is ~free) ─────────────────────
RRF_KS            = [30, 60, 100]
LOCI_WEIGHTS      = [0.3, 0.5, 0.7, 1.0]
LOCI_FLOORS       = [0.0, 0.1, 0.3]
AUTHORITY_MARGINS = [0.0, 0.035, 0.1]   # 0.0 = authority promotion off
MIN_PER_STORES    = [0, 1, 2]           # 0 = pure top-n (no diversity floor)
FUSION_MODES      = ["rrf"]             # add "rrf_pct"/"percentile" to sweep N-store modes
# Packet-size + candidate-pool: the ONLY levers that move docket_coverage. The
# reshaping knobs above can just trim/reorder a FIXED packet (proven inert for
# coverage); these change what's IN it. n_results = briefing size; fetch_mult =
# how deep into each store's tail the fusion reaches before trimming.
N_RESULTS_GRID    = [10, 15, 20, 30]
FETCH_MULTIPLIERS = [2, 3]

CAPTURE_N = 50      # freeze the FULL candidate pool per store (Memory caps n_results at 50),
                    # so any fetch_n in the grid replays faithfully
EVAL_K    = 10      # metric depth (top-k) - FIXED across the n_results sweep so ranking
                    # metrics stay comparable. coverage is depth-independent (counts
                    # expected facts ANYWHERE in the packet), so it's the one that moves.

# The default sweep grid as a dict - the reference values every knob takes when a
# CorpusRegrades set doesn't override it. build_grid() merges a set's overrides
# over this; sweep() takes the merged grid. Mirror of the constants above (and of
# REGRADE_KNOBS in topology.py - keep the three in sync).
DEFAULT_GRID: dict[str, list] = {
    "rrf_k": RRF_KS, "loci_weight": LOCI_WEIGHTS, "loci_floor": LOCI_FLOORS,
    "authority_margin": AUTHORITY_MARGINS, "min_per_store": MIN_PER_STORES,
    "fusion_mode": FUSION_MODES, "n_results": N_RESULTS_GRID,
    "fetch_multiplier": FETCH_MULTIPLIERS,
}
# the product order sweep() unpacks - MUST match the tuple unpacking in its loop
_GRID_ORDER = ["rrf_k", "loci_weight", "loci_floor", "authority_margin",
               "min_per_store", "fusion_mode", "n_results", "fetch_multiplier"]


def build_grid(overrides: Optional[dict]) -> dict[str, list]:
    """Merge a CorpusRegrades set's knob overrides over DEFAULT_GRID. Any knob the
    set doesn't name keeps its full default sweep; an empty override list is
    ignored (falls back to default). Unknown keys are dropped - the topology
    compiler already warned on them at Set-Active time. Never mutates DEFAULT_GRID."""
    grid = {k: list(v) for k, v in DEFAULT_GRID.items()}
    for k, v in (overrides or {}).items():
        if k in grid and v:
            grid[k] = list(v)
    return grid


# ── transports: real for capture, recording, replay for regrade ──────────────
class RealTransport:
    """Async httpx POST. Used only for the one-time capture pass."""

    def __init__(self, timeout: float = 30.0):
        import httpx
        self._client = httpx.AsyncClient(timeout=timeout)

    async def post_json(self, url: str, payload: dict[str, Any],
                        headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
        r = await self._client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._client.aclose()


class CapturingTransport:
    """Wraps a transport, records every (base_url, query) -> raw response. We key
    off the request payload's `query` + the URL's base (path stripped) so the
    ReplayTransport can answer the exact calls the adapters make."""

    def __init__(self, inner):
        self._inner = inner
        self.recorded: dict[str, dict[str, Any]] = {}  # query -> {base_url -> response}

    async def post_json(self, url, payload, headers=None):
        resp = await self._inner.post_json(url, payload, headers=headers)
        base = url.rsplit("/", 1)[0]
        query = payload.get("query", "")
        self.recorded.setdefault(query, {})[base] = resp
        return resp


class ReplayTransport:
    """Replays a frozen capture. Keyed by (base_url, query); trims hits to the n
    the caller asks for, so replay is faithful to any n <= capture_n. A genuine
    miss (url+query never captured) returns empty AND is counted - so the harness
    flags capture gaps instead of silently skewing the metrics."""

    def __init__(self, capture: dict[str, dict[str, Any]]):
        self.capture = capture
        self.misses = 0

    async def post_json(self, url, payload, headers=None):
        base = url.rsplit("/", 1)[0]
        query = payload.get("query", "")
        per_q = self.capture.get(query)
        resp = per_q.get(base) if per_q else None
        if resp is None:
            self.misses += 1
            return {"hits": []}
        n = payload.get("n_results")
        hits = resp.get("hits", [])
        if isinstance(n, int) and n >= 0:
            hits = hits[:n]
        return {**resp, "hits": hits}


# ── capture (READ-ONLY: /search only) ────────────────────────────────────────
async def capture_stores(base_stores: list[StoreConfig], queries: list,
                         base_transport, capture_n: int = CAPTURE_N
                         ) -> dict[str, dict[str, Any]]:
    """Run each store's /search ONCE per query through the real adapters (so the
    request payloads are exactly what production sends) and record the raw
    responses. The mapped Hits are discarded - we only want the frozen JSON.
    This is the ONLY place that touches a live store, and it only reads."""
    _require_scc()
    cap = CapturingTransport(base_transport)
    adapters = [build_adapter(sc, cap) for sc in base_stores]
    for q in queries:
        for ad in adapters:
            try:
                await ad.search(q.query, capture_n)   # records via cap
            except Exception as e:  # noqa: BLE001 - one bad store/query shouldn't sink the capture
                print(f"  [capture warn] {ad.name} q={q.query!r}: {type(e).__name__}: {e}")
    return cap.recorded


# ── regrade (in-process, real Federation over the frozen capture) ────────────
def build_fed_config(base_stores: list[StoreConfig], *, rrf_k: int,
                     loci_weight: float, loci_floor: float, authority: float,
                     min_per_store: int, fusion_mode: str,
                     n_results: int, fetch_multiplier: int) -> FederationConfig:
    """A FederationConfig for one grid point. Memory stays weight 1.0 / floor 0.0
    (the reference); the loci knobs are what we sweep. edges OFF - the core sweep
    is faithful only when nothing depends on the regraded packet (see module
    docstring)."""
    stores: list[StoreConfig] = []
    for sc in base_stores:
        is_mem = sc.type == "seren_memory"
        stores.append(StoreConfig(
            name=sc.name, type=sc.type, url=sc.url,
            weight=1.0 if is_mem else loci_weight,
            floor=0.0 if is_mem else loci_floor,
        ))
    return FederationConfig(
        stores=stores, k=rrf_k, n_results=n_results,
        fetch_multiplier=fetch_multiplier,
        fusion_mode=fusion_mode, authority_margin=authority,
        min_per_store=min_per_store, edges_enabled=False,
    )


def _fused_to_dicts(fused) -> list[dict]:
    """FusedHit -> the {id, content, score} shape the grader expects - the same
    flattening SCC's /search route does (score = rrf_score)."""
    return [{"id": f.hit.id, "content": f.hit.content, "score": f.rrf_score}
            for f in fused]


async def regrade_one(capture, fed_config: FederationConfig, queries,
                      eval_k: int = EVAL_K) -> tuple[dict, int]:
    """One grid point: run the REAL Federation over the ReplayTransport for every
    query, grade, aggregate. Returns (aggregate_metrics, capture_misses)."""
    replay = ReplayTransport(capture)
    fed = Federation(fed_config, replay)
    results: list[tuple[list[tuple[str, float]], set[str]]] = []
    coverages: list[float] = []
    densities: list[float] = []
    for q in queries:
        fused = await fed.search(q.query)
        hits = _fused_to_dicts(fused)
        retrieved, relevant, cov, den = grade_against_content(
            hits, q.expected_content, q.expected_ids, k=eval_k)
        results.append((retrieved, relevant))
        coverages.append(cov)
        densities.append(den)
    agg = compute_metrics_batch(results, k=eval_k).aggregate()
    agg["docket_coverage"] = sum(coverages) / len(coverages) if coverages else 0.0
    agg["docket_density"] = sum(densities) / len(densities) if densities else 0.0
    return agg, replay.misses


async def sweep(capture, base_stores: list[StoreConfig], queries,
                eval_k: int = EVAL_K, sort_by: str = "ndcg",
                grid: Optional[dict] = None) -> tuple[dict, list[dict]]:
    """Full in-process grid sweep over the frozen capture. `grid` is a knob->values
    mapping (defaults to DEFAULT_GRID); a CorpusRegrades set narrows it via
    build_grid(). Returns (best, rows)."""
    _require_scc()
    g = grid or DEFAULT_GRID
    combos = list(itertools.product(*(g[k] for k in _GRID_ORDER)))
    rows: list[dict] = []
    total_misses = 0
    for rrf_k, w, fl, auth, mps, mode, n_res, fmult in combos:
        cfg = build_fed_config(
            base_stores, rrf_k=rrf_k, loci_weight=w, loci_floor=fl,
            authority=auth, min_per_store=mps, fusion_mode=mode,
            n_results=n_res, fetch_multiplier=fmult)
        agg, misses = await regrade_one(capture, cfg, queries, eval_k)
        total_misses += misses
        agg["params"] = {"rrf_k": rrf_k, "weight": w, "floor": fl,
                         "authority_margin": auth, "min_per_store": mps,
                         "fusion_mode": mode, "n_results": n_res,
                         "fetch_mult": fmult}
        rows.append(agg)
    if total_misses:
        print(f"  [!] {total_misses} capture misses across the sweep "
              f"(a query/store pair wasn't captured) - results may be skewed.")

    def _key(r):
        # objective first, then prefer the SMALLEST packet that achieves it
        # (a briefing, not a dump), then better top-k rank quality.
        return (r.get(sort_by, 0),
                -r.get("params", {}).get("n_results", 0),
                r.get("ndcg", 0))

    rows.sort(key=_key, reverse=True)
    best = rows[0] if rows else {}
    return best, rows


# ── config-driven regrade: roll CorpusRegrades sets against the topology ─────
class _RegradeQuery:
    """The minimal query shape sweep()/grade_against_content need. Corpus questions
    carry only expect_content (no keys/refs), so expected_ids stays empty."""
    __slots__ = ("query", "expected_content", "expected_ids")

    def __init__(self, query, expected_content, expected_ids=()):
        self.query = query
        self.expected_content = list(expected_content)
        self.expected_ids = list(expected_ids)


def corpus_question_hash(questions) -> str:
    """Fingerprint of the corpus-question set a capture answers. A capture is keyed
    by (query, store) -- change the queries and it simply has no answer for the new
    ones, and ReplayTransport's misses grade as retrieval failures that never
    happened. Sorted, so question ORDER never invalidates a capture; only content."""
    import hashlib
    qs = sorted(q.query for q in questions
                if getattr(q, "asks", "") == "corpus" and not getattr(q, "expect_empty", False))
    return hashlib.sha256(json.dumps(qs).encode()).hexdigest()[:16]


async def capture_corpora(topology, url_of: dict, questions, *,
                          corpus_filter=None, capture_n: int = CAPTURE_N,
                          report_progress: bool = False) -> dict:
    """The capture HALF of capture-replay, alone: one read-only /search pass over
    each eligible corpus's member stores. Returns {corpus_name: capture} for the
    route to persist. Touches containers exactly once per (store, query); sweeps
    nothing.

    Eligible = has at least one PURE (non-packet-coupled) set after per-corpus
    resolution. A corpus whose only sets are hops sweeps gets no capture, because
    nothing could ever replay against it -- capturing it would be bytes that only
    ever mislead.

    SERIAL across corpora, same law as everywhere else: corpora overlap on member
    stores, and a contended capture poisons every sweep replayed from it.
    """
    _require_scc()
    from .regrade_live import sets_for_corpus, is_packet_coupled
    rqs = [_RegradeQuery(q.query, q.expect_content)
           for q in questions
           if getattr(q, "asks", "") == "corpus" and not getattr(q, "expect_empty", False)]
    if not rqs:
        return {}
    base = list(topology.corpus_regrades or [])
    _progress = None
    if report_progress:
        from . import progress as _progress
    out: dict = {}
    transport = RealTransport()
    try:
        targets = []
        for corpus in topology.corpus:
            if corpus.is_catchall or not corpus.stores:
                continue
            if corpus_filter and corpus.name not in corpus_filter:
                continue
            if not any(not is_packet_coupled(s) for s in sets_for_corpus(corpus, base)):
                continue
            base_stores = [StoreConfig(name=st.name, type=st.kind, url=url_of[st.name])
                           for st in corpus.stores if st.name in url_of]
            if base_stores:
                targets.append((corpus, base_stores))
        if _progress:
            for corpus, _bs in targets:
                _progress.start(corpus.name, "capture", 1)
        for corpus, base_stores in targets:
            try:
                out[corpus.name] = await capture_stores(base_stores, rqs, transport, capture_n)
            finally:
                if _progress:
                    _progress.bump(corpus.name, 1)
                    _progress.finish(corpus.name)
    finally:
        await transport.aclose()
    return out


async def _regrade_one_corpus_async(corpus, base_stores: list[StoreConfig], rqs,
                                    transport, capture_n: int, eval_k: int,
                                    sort_by: str, regrades: list, flag_map: dict,
                                    sem: "asyncio.Semaphore", scc_url: str = "",
                                    preloaded_capture: dict | None = None) -> dict:
    """One corpus's full regrade: ONE read-only capture of its backing stores, then
    sweep every CorpusRegrades set's combos over the frozen capture, in-process.

    PARITY WITH THE LIVE ENGINE IS THE CONTRACT. This used to sweep build_grid(),
    which keeps the FULL default sweep for every knob a set does not name -- so
    `weight-sweep`, naming loci_weight with 8 values, expanded to rrf_k(3) x
    weight(8) x floor(3) x authority(3) x min_per_store(3) x n_results(4) x
    fetch_mult(2) = 5,184 combos. The live engine runs the SAME set as 8. Two
    engines answering different questions is worse than one slow engine, and it is
    invisible -- both produce a full, confident table.

    So this uses compact_combos() -- the product over ONLY the knobs a set names --
    and pins every unnamed knob to the container's CURRENT config, read from
    GET /stores exactly as the live path does. Same set, same combo count, same
    reference point, comparable deltas.

    CAVEAT, named because it cannot be fixed from here: GET /stores exposes k,
    n_results, per-store weight/floor and the hop knobs -- NOT authority_margin,
    min_per_store, fusion_mode or fetch_multiplier. The live engine cannot reset
    those either (its baseline_cfg has no field for them), so both leave them
    wherever the container sits; here that means SCC's FederationConfig defaults.
    A set that sweeps one of those is still measured correctly -- every combo sets
    it explicitly -- but its BASELINE row is defaults-based rather than
    container-based. Same limitation the live path's "best-effort restore + a note"
    already documents.
    """
    _METRICS = ("ndcg", "docket_coverage", "docket_density", "recall",
                "mrr", "hit_rate", "iou", "prec_omega")

    # The container's CURRENT config = the reference every unnamed knob sits at.
    # Read BEFORE the capture, so a failure here costs nothing but defaults.
    cur_k, cur_n = 60, 10
    cur_weight, cur_floor = 1.0, 0.0
    if scc_url:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(f"{scc_url}/stores")
                info = r.json() if (r.status_code == 200 and r.content) else {}
            cur_k = info.get("k", cur_k)
            cur_n = info.get("n_results", cur_n)
            loci_row = next((s for s in (info.get("stores") or [])
                             if s.get("type") == "seren_loci"), None)
            if loci_row:
                cur_weight = loci_row.get("weight", cur_weight)
                cur_floor = loci_row.get("floor", cur_floor)
        except Exception as exc:      # noqa: BLE001 - fall back to documented defaults
            logger.warning("could not read %s/stores for baseline, using defaults: %s",
                           scc_url, exc)

    async with sem:
        # ONE read-only capture per corpus -- unless the route handed us a SAVED one,
        # in which case the containers are not touched at all. The route owns the
        # staleness check (captured_at vs seeded_at, question-hash match); by the time
        # a preloaded capture reaches here it has already been judged fresh.
        if preloaded_capture is not None:
            capture = preloaded_capture
        else:
            capture = await capture_stores(base_stores, rqs, transport, capture_n)

    def _cfg(combo: dict):
        """Baseline + this combo laid over it -- the replay twin of the live path's
        full_config_body(). Unnamed knobs take the container's current value (or
        SCC's default where /stores doesn't expose it), never the previous combo's,
        so every combo starts from the same place."""
        stores: list[StoreConfig] = []
        for sc in base_stores:
            is_mem = sc.type == "seren_memory"
            stores.append(StoreConfig(
                name=sc.name, type=sc.type, url=sc.url,
                weight=1.0 if is_mem else combo.get("loci_weight", cur_weight),
                floor=0.0 if is_mem else combo.get("loci_floor", cur_floor)))
        kwargs: dict = {"stores": stores,
                        "k": combo.get("rrf_k", cur_k),
                        "n_results": combo.get("n_results", cur_n),
                        "edges_enabled": False}
        # Only pass what the set actually NAMES. Anything omitted falls to
        # FederationConfig's own default rather than a value invented here.
        for knob in ("authority_margin", "min_per_store", "fusion_mode",
                     "fetch_multiplier"):
            if knob in combo:
                kwargs[knob] = combo[knob]
        return FederationConfig(**kwargs)

    # The baseline row: the container's current config, measured. Every delta below
    # is relative to THIS, matching the live engine's "current" row.
    base_agg, _ = await regrade_one(capture, _cfg({}), rqs, eval_k)
    set_rows: list[dict] = [{
        "name": "current",
        "metrics": {m: base_agg.get(m, 0.0) for m in _METRICS},
        "params": {"k": cur_k, "n_results": cur_n},
        "delta": {m: 0.0 for m in ("ndcg", "docket_coverage", "recall", "mrr")}}]

    from .regrade_live import compact_combos
    combo_cache: dict[str, dict] = {}
    for rset in regrades:
        best = None
        combo_rows: list[dict] = []
        for combo in compact_combos(rset.overrides):
            ck = json.dumps(combo, sort_keys=True, default=str)
            cached = combo_cache.get(ck)
            if cached is not None:
                row = dict(cached)
            else:
                agg, _misses = await regrade_one(capture, _cfg(combo), rqs, eval_k)
                row = {"metrics": {m: agg.get(m, 0.0) for m in _METRICS}, "params": combo}
                row["delta"] = {m: round(row["metrics"][m] - base_agg.get(m, 0.0), 4)
                                for m in ("ndcg", "docket_coverage", "recall", "mrr")}
                combo_cache[ck] = row
            combo_rows.append(row)
            if best is None or row["metrics"].get(sort_by, 0) > best["metrics"].get(sort_by, 0):
                best = row
        if best:
            # COPY before attaching combos -- `best` IS one of the dicts in combo_rows,
            # so assigning the list onto it in place makes the structure
            # self-referential and blows up json encoding. Same trap as the live path.
            best = dict(best)
            best["name"] = rset.name
            best["combos"] = combo_rows      # the CURVE, not just max()
            set_rows.append(best)

    flavor = ("vector" if any("vector" in flag_map.get(st.name, [])
                              for st in corpus.stores) else "lexical")
    return {"corpus": corpus.name, "flavor": flavor,
            "capture_source": "saved" if preloaded_capture is not None else "fresh",
            "baseline": "current", "sets": set_rows}


async def run_config_regrade(topology, url_of: dict, questions, *,
                             capture_n: int = CAPTURE_N, eval_k: int = EVAL_K,
                             sort_by: str = "ndcg",
                             max_parallel_corpora: int = 8,
                             report_progress: bool = False,
                             set_filter=None, corpus_filter=None,
                             saved_captures: dict | None = None) -> dict:
    """Roll every CorpusRegrades set against every (non-catch-all) corpus in the
    topology. Per corpus: capture its backing stores' candidate pools ONCE
    (read-only /search on the eval containers - never live memory), then sweep
    each set's grid over the frozen capture. Returns per-corpus best-per-set with
    the delta vs the baseline set. Replays the REAL Federation, so it needs SCC
    importable - raises via _require_scc() otherwise.

    SERIAL ACROSS CORPORA. Corpora overlap on member stores, so concurrent
    captures are concurrent /search against the same containers -- see the module
    CONCURRENCY note. The capture is frozen and every combo is graded against it,
    so a contended capture poisons a whole sweep invisibly.

    A failing corpus does NOT abort the others: this harness is read-only
    (capture is /search-only, never a write), so a corpus that fails to
    capture/sweep doesn't corrupt anything - the rest still finish and report,
    and the failed one shows up as an "error" entry in its slot.

    max_parallel_corpora is accepted for compatibility and ignored above 1, with
    a warning. A knob that silently does nothing is its own bug.
    """
    _require_scc()
    from .regrade_live import sets_for_corpus
    rqs = [_RegradeQuery(q.query, q.expect_content)
           for q in questions
           if getattr(q, "asks", "") == "corpus" and not getattr(q, "expect_empty", False)]
    base_regrades = list(topology.corpus_regrades or [])
    if not rqs:
        return {"corpora": [], "note": "No corpus questions to regrade."}
    # NO early-out on an empty top-level list. Per-corpus CorpusRegrades mean a config
    # can legitimately have no base sets and still have work to do -- the same rule-2
    # gap that was in run_live_regrade and the route. Resolution is per corpus, below.

    flag_map = {n.name: (n.flags or []) for n in topology.loci}
    transport = RealTransport()
    # ALWAYS 1. The semaphore wraps the capture step, which is the only part that
    # touches containers -- and corpora share those containers, so any width above 1
    # is two N-store fans against an overlapping set. Ignored out loud rather than
    # silently, so a config value that does nothing says so.
    _requested = max(1, int(max_parallel_corpora or 1))
    if _requested > 1:
        logger.warning(
            "regrade: ignoring max_parallel_corpora=%d -- corpora share member stores, "
            "so concurrent captures contend and a contended capture silently poisons "
            "every combo swept against it. Capturing serially.", _requested)
    sem = asyncio.Semaphore(1)
    corpora_out: list[dict] = []

    _progress = None
    if report_progress:
        from . import progress as _progress

    async def _one(corpus, base_stores, sets, preloaded=None):
        """Run one corpus and publish it the moment it lands, rather than making every
        result wait on the whole gather. Failures publish too -- a corpus that dies on
        its capture should be visible immediately, not after the rest finish."""
        try:
            out = await _regrade_one_corpus_async(
                corpus, base_stores, rqs, transport, capture_n, eval_k,
                sort_by, sets, flag_map, sem, url_of.get(corpus.name, ""),
                preloaded_capture=preloaded)
        except Exception as exc:  # noqa: BLE001 - read-only; one bad corpus must not sink the rest
            logger.error("Regrade failed for corpus %s: %s", corpus.name, exc)
            out = {"corpus": corpus.name, "error": f"{type(exc).__name__}: {exc}"}
        if _progress:
            _progress.publish(corpus.name, out)
            _progress.finish(corpus.name)
        return out

    try:
        tasks_meta = []
        coros = []
        for corpus in topology.corpus:
            if corpus.is_catchall or not corpus.stores:
                continue
            if corpus_filter and corpus.name not in corpus_filter:
                # Per-corpus button: sweep JUST this one. Filtered before tasks exist,
                # same reason as the sets filter -- an unswept corpus must not appear
                # in the progress table as a 0/N row that never moves.
                continue
            base_stores = [
                StoreConfig(name=st.name, type=st.kind, url=url_of[st.name])
                for st in corpus.stores if st.name in url_of
            ]
            if not base_stores:
                continue
            # Same four rules as the live path, same resolver. The two engines must
            # agree about what the config MEANS -- a fast path that sweeps a different
            # set than the slow one is not a fast path, it is a second answer.
            sets = sets_for_corpus(corpus, base_regrades)
            if set_filter is not None:
                # Router split: this engine takes the sets that only RE-FUSE captured
                # candidates; packet-coupled ones go to the live engine. Filtering after
                # resolution keeps inheritance/override/opt-out decided in exactly one
                # place for both engines.
                sets = [s for s in sets if set_filter(s)]
            if not sets:
                logger.info("regrade: skipping %s (no CorpusRegrades sets apply)", corpus.name)
                continue
            tasks_meta.append((corpus, sets))
            coros.append(_one(corpus, base_stores, sets,
                              (saved_captures or {}).get(corpus.name)))
        if not coros:
            return {"corpora": [], "note": (
                "No corpus has any CorpusRegrades sets that apply -- every corpus "
                "either opted out with an empty CorpusRegrades or there is no "
                "top-level set to inherit.")}
        if _progress:
            # Declared for EVERY corpus before the first capture, so the table renders
            # complete and empty rather than growing a row at a time. Unit is the SET
            # here, not the combo: combos replay in-process in milliseconds, so the
            # honest unit of visible work is one set's grid sweep.
            for corpus, sets in tasks_meta:
                _progress.start(corpus.name, "regrade", 1 + len(sets))
        corpora_out = list(await asyncio.gather(*coros))
    finally:
        await transport.aclose()
    return {"corpora": corpora_out, "sort_by": sort_by, "eval_k": eval_k,
            "engine": "capture-replay",
            # UNION across corpora: with per-corpus overrides, no single corpus
            # necessarily ran every set.
            "set_names": sorted({r.name for _c, s in tasks_meta for r in s}),
            "question_count": len(rqs)}



# ── trace persistence (the regrade-trace: capture once, re-sweep offline) ────
def save_capture(path: str, captures: dict[str, dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(captures, fh)
    print(f"  capture trace saved -> {path}")


def load_capture(path: str) -> dict[str, dict]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _print_row(label: str, agg: dict) -> None:
    p = agg.get("params", {})
    print(f"  {label:9} "
          f"hit={agg.get('hit_rate',0):.3f} mrr={agg.get('mrr',0):.3f} "
          f"recall={agg.get('recall',0):.3f} ndcg={agg.get('ndcg',0):.3f} "
          f"iou={agg.get('iou',0):.3f} p-omega={agg.get('prec_omega',0):.3f} "
          f"cov={agg.get('docket_coverage',0):.3f} den={agg.get('docket_density',0):.3f}"
          + (f"  {p}" if p else ""))


# ── CLI ──────────────────────────────────────────────────────────────────────
async def _run(args) -> None:
    # LAZY, and deliberately so. This is the ONLY thing in the whole module that needs
    # dataset.py -- the synthetic corpus generator. As a MODULE-LEVEL import it dragged
    # the fake corpus into the import graph of everything that merely wanted
    # DEFAULT_GRID or build_grid, and when dataset.py got quarantined it took the whole
    # file down with it -- and nine tests, which then SKIPPED instead of FAILING, and
    # printed a green summary while not running.
    #
    # The grid, the sweep, the capture/replay transports and run_config_regrade are all
    # clean. Only this legacy synthetic-corpus CLI mode wants it, and that mode is
    # superseded by the config-driven path (run_config_regrade), which sweeps your REAL
    # questions against containers the topology spun up. --load-capture offline
    # re-sweeps don't need it either.
    try:
        from .dataset import seed_synthetic_dataset
    except ImportError as exc:
        raise RuntimeError(
            "the synthetic-corpus CLI mode needs seren_probe.dataset, which is "
            "quarantined in _attic/ (it generated a fake corpus, and its neighbours "
            "knew the real store ports). Use the config-driven regrade instead -- it "
            "sweeps your actual ProbeConfig questions against the containers SerenProbe "
            "spun up itself.") from exc

    ds = seed_synthetic_dataset()                 # dataset only - NO store seeding here
    queries = ds.filter_by_source("corpus")
    print(f"Corpus queries: {len(queries)}")

    # Build the two store sets (NV/V) we'll capture + sweep separately.
    runs: list[tuple[str, dict, list[StoreConfig]]] = []  # (label, capture, base_stores)

    if args.load_capture:
        # Fully offline: re-sweep frozen traces, nothing live attached.
        print(f"\nOFFLINE: loading capture trace from {args.load_capture}")
        blob = load_capture(args.load_capture)
        for label in ("no-vector", "vector"):
            if label in blob:
                runs.append((label, blob[label], _stores_from_capture(blob[label])))
        if not runs:
            sys.exit("loaded trace has no 'no-vector'/'vector' captures")
    else:
        if not (args.memory_url and (args.loci_nv_url or args.loci_v_url)):
            sys.exit("SAFETY: pass --memory-url and at least one of --loci-nv-url/"
                     "--loci-v-url (DEV stores!), or run offline with --load-capture.")
        print("\n*** CAPTURE hits LIVE stores, read-only /search. Use DEV stores. ***")
        print(f"    memory={args.memory_url}  loci-nv={args.loci_nv_url}  "
              f"loci-v={args.loci_v_url}")
        transport = RealTransport()
        try:
            sets = []
            if args.loci_nv_url:
                sets.append(("no-vector", args.loci_nv_url))
            if args.loci_v_url:
                sets.append(("vector", args.loci_v_url))
            for label, loci_url in sets:
                base_stores = [
                    StoreConfig(name="memory", type="seren_memory", url=args.memory_url),
                    StoreConfig(name="loci", type="seren_loci", url=loci_url),
                ]
                print(f"\n  capturing [{label}] ...")
                cap = await capture_stores(base_stores, queries, transport, args.capture_n)
                runs.append((label, cap, base_stores))
        finally:
            await transport.aclose()
        if args.save_capture:
            save_capture(args.save_capture, {label: cap for label, cap, _ in runs})

    # Sweep each capture in-process.
    for label, cap, base_stores in runs:
        print(f"\n── Sweep [{label}] ({len(queries)} queries) ──")
        best, rows = await sweep(cap, base_stores, queries,
                                 eval_k=args.k, sort_by=args.sort_by)
        # coverage plateau: the best coverage reachable at each packet size, so
        # you can see WHERE more slots stop buying you facts.
        by_n: dict[int, dict] = {}
        for r in rows:
            n = r.get("params", {}).get("n_results")
            if n is not None and (n not in by_n
                                  or r["docket_coverage"] > by_n[n]["docket_coverage"]):
                by_n[n] = r
        print("  coverage by packet size (best per n_results):")
        for n in sorted(by_n):
            rr = by_n[n]
            print(f"    n={n:<3} cov={rr['docket_coverage']:.3f} "
                  f"den={rr['docket_density']:.3f} "
                  f"recall@{args.k}={rr.get('recall',0):.3f} "
                  f"fetch={rr['params'].get('fetch_mult')}")
        for r in rows[:5]:
            _print_row("", r)
        print(f"  BEST ({args.sort_by}):")
        _print_row("best", best)


def _stores_from_capture(cap: dict) -> list[StoreConfig]:
    """Reconstruct the StoreConfigs (the URLs the ReplayTransport keys on) from
    whatever base URLs appear in the capture - so offline sweeps need no live
    args. We tag by type heuristically from the port/name in the URL; the only
    thing that matters for replay is that the URL matches a captured base."""
    _require_scc()
    bases: set[str] = set()
    for per_q in cap.values():
        bases.update(per_q.keys())
    stores: list[StoreConfig] = []
    for b in sorted(bases):
        # Memory vs Loci: the capture doesn't label them, but the adapter type
        # only affects the request payload shape, which replay ignores (it keys
        # on url+query). We still need the right TYPE so build_fed_config sets
        # memory weight=1.0. Heuristic: a base seen with memory tiers is memory.
        # Simplest robust signal: a memory /search response carries 'searched_tiers'
        # or hits with 'tier'; loci carries 'finder'. Sniff one response.
        sample = next((per_q[b] for per_q in cap.values() if b in per_q), {})
        is_loci = "finder" in sample or any(
            "match_kind" in (h or {}) for h in sample.get("hits", []))
        stores.append(StoreConfig(
            name=("loci" if is_loci else "memory"),
            type=("seren_loci" if is_loci else "seren_memory"),
            url=b))
    # memory first, so cross-store tie-breaks match the live capture order
    stores.sort(key=lambda s: s.type != "seren_memory")
    return stores


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Capture-once / regrade-many SCC fusion tuning (READ-ONLY).")
    ap.add_argument("--memory-url", help="DEV SerenMemory base URL (e.g. http://127.0.0.1:7420)")
    ap.add_argument("--loci-nv-url", help="DEV no-vector Loci base URL")
    ap.add_argument("--loci-v-url", help="DEV vector/hybrid Loci base URL")
    ap.add_argument("--capture-n", type=int, default=CAPTURE_N,
                    help=f"candidates to freeze per store/query (default {CAPTURE_N})")
    ap.add_argument("--k", type=int, default=EVAL_K, help="metric depth top-k")
    ap.add_argument("--sort-by", default="ndcg",
                    help="metric to rank configs by (ndcg/recall/docket_coverage/...)")
    ap.add_argument("--save-capture", help="write the frozen trace to JSON")
    ap.add_argument("--load-capture", help="re-sweep a saved trace OFFLINE (no live stores)")
    args = ap.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
