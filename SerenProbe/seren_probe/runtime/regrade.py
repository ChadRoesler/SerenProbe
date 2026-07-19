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
run_config_regrade fans OUT across corpora (asyncio.gather, bounded by
max_parallel_corpora) since each corpus captures its own backing stores into
its own frozen trace and sweeps that trace independently - nothing shared
except the one RealTransport/httpx.AsyncClient, which is concurrency-safe.
Combos within ONE corpus's sweep still run strictly serially. A corpus that
fails to capture/sweep does not abort the others - read-only means a failure
here can't corrupt anything, so the rest still finish and the failed corpus
shows up as an "error" entry.

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


async def _regrade_one_corpus_async(corpus, base_stores: list[StoreConfig], rqs,
                                    transport, capture_n: int, eval_k: int,
                                    sort_by: str, regrades: list, flag_map: dict,
                                    sem: "asyncio.Semaphore") -> dict:
    """One corpus's full regrade: ONE read-only capture of its backing stores,
    then sweep every CorpusRegrades set's grid over the frozen capture. Fully
    self-contained (its own base_stores, its own capture) so it's SAFE TO RUN
    CONCURRENTLY with other corpora's calls to this function via asyncio.gather
    - the shared `transport` is an httpx.AsyncClient, which supports concurrent
    requests from multiple coroutines. `sem` bounds how many corpora capture at
    once, same rationale as regrade_live's max_parallel_corpora.

    Returns the same {"corpus", "flavor", "baseline", "sets"} shape the old
    inline loop body produced. Raises on failure -- callers running this under
    gather(return_exceptions=True) are responsible for catching per-corpus so
    one bad SCC/store doesn't sink the others.
    """
    _METRICS = ("ndcg", "docket_coverage", "docket_density", "recall",
                "mrr", "hit_rate", "iou", "prec_omega")
    async with sem:
        # ONE read-only capture per corpus, then sweep every set in-process.
        capture = await capture_stores(base_stores, rqs, transport, capture_n)
    set_rows: list[dict] = []
    for rset in regrades:
        best, _rows = await sweep(capture, base_stores, rqs, eval_k=eval_k,
                                  sort_by=sort_by, grid=build_grid(rset.overrides))
        set_rows.append({"name": rset.name,
                         "metrics": {m: best.get(m, 0.0) for m in _METRICS},
                         "params": best.get("params", {})})
    baseline = next((r for r in set_rows if r["name"] == "baseline"),
                    set_rows[0] if set_rows else None)
    if baseline:
        for r in set_rows:
            r["delta"] = {m: round(r["metrics"][m] - baseline["metrics"][m], 4)
                          for m in ("ndcg", "docket_coverage", "recall", "mrr")}
    flavor = ("vector" if any("vector" in flag_map.get(st.name, [])
                              for st in corpus.stores) else "lexical")
    return {"corpus": corpus.name, "flavor": flavor,
            "baseline": baseline["name"] if baseline else None, "sets": set_rows}


async def run_config_regrade(topology, url_of: dict, questions, *,
                             capture_n: int = CAPTURE_N, eval_k: int = EVAL_K,
                             sort_by: str = "ndcg",
                             max_parallel_corpora: int = 8) -> dict:
    """Roll every CorpusRegrades set against every (non-catch-all) corpus in the
    topology. Per corpus: capture its backing stores' candidate pools ONCE
    (read-only /search on the eval containers - never live memory), then sweep
    each set's grid over the frozen capture. Returns per-corpus best-per-set with
    the delta vs the baseline set. Replays the REAL Federation, so it needs SCC
    importable - raises via _require_scc() otherwise.

    PARALLEL ACROSS CORPORA, SERIAL WITHIN ONE.
    Each corpus captures its OWN backing stores and sweeps its OWN frozen
    capture - nothing is shared between corpora, so nothing contends. The
    shared RealTransport is a single httpx.AsyncClient, which is safe for
    concurrent requests from multiple coroutines. asyncio.gather fans the
    per-corpus work out instead of doing it corpus-after-corpus; the same move
    as regrade_live.run_live_regrade's ThreadPoolExecutor fan-out, just async
    instead of threaded since this path is already async end-to-end.

    A failing corpus does NOT abort the others: this harness is read-only
    (capture is /search-only, never a write), so a corpus that fails to
    capture/sweep doesn't corrupt anything - the rest still finish and report,
    and the failed one shows up as an "error" entry in its slot.

    max_parallel_corpora bounds how many corpora capture concurrently (a
    semaphore around the capture step only - the in-process sweep is cheap and
    unbounded), same rationale as regrade_live's cap: unbounded fan-out on a
    big topology would open a pile of simultaneous connections at once.
    """
    _require_scc()
    rqs = [_RegradeQuery(q.query, q.expect_content)
           for q in questions
           if getattr(q, "asks", "") == "corpus" and not getattr(q, "expect_empty", False)]
    regrades = list(topology.corpus_regrades or [])
    if not rqs:
        return {"corpora": [], "note": "No corpus questions to regrade."}
    if not regrades:
        return {"corpora": [], "note": "No CorpusRegrades sets in the active ProbeConfig."}

    flag_map = {n.name: (n.flags or []) for n in topology.loci}
    transport = RealTransport()
    sem = asyncio.Semaphore(max(1, int(max_parallel_corpora or 1)))
    corpora_out: list[dict] = []
    try:
        tasks_meta = []
        coros = []
        for corpus in topology.corpus:
            if corpus.is_catchall or not corpus.stores:
                continue
            base_stores = [
                StoreConfig(name=st.name, type=st.kind, url=url_of[st.name])
                for st in corpus.stores if st.name in url_of
            ]
            if not base_stores:
                continue
            tasks_meta.append(corpus)
            coros.append(_regrade_one_corpus_async(
                corpus, base_stores, rqs, transport, capture_n, eval_k,
                sort_by, regrades, flag_map, sem))
        results = await asyncio.gather(*coros, return_exceptions=True)
        # results is in the SAME order as tasks_meta/coros (gather preserves
        # input order regardless of completion order), so this merge is
        # already in original topology order.
        for corpus, result in zip(tasks_meta, results):
            if isinstance(result, Exception):
                # Caught per-corpus, NOT re-raised: the rest of the batch must
                # still finish and report. This is the deliberate deviation
                # from a loud re-raise -- see the docstring's PARALLEL ACROSS
                # CORPORA note.
                logger.error("Regrade failed for corpus %s: %s", corpus.name, result)
                corpora_out.append({"corpus": corpus.name,
                                    "error": f"{type(result).__name__}: {result}"})
            else:
                corpora_out.append(result)
    finally:
        await transport.aclose()
    return {"corpora": corpora_out, "sort_by": sort_by, "eval_k": eval_k,
            "set_names": [r.name for r in regrades], "question_count": len(rqs)}



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
