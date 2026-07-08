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
import sys
from typing import Any, Optional

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

from .dataset import seed_synthetic_dataset
from .metrics import compute_metrics_batch, grade_against_content


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
                eval_k: int = EVAL_K, sort_by: str = "ndcg") -> tuple[dict, list[dict]]:
    """Full in-process grid sweep over the frozen capture. Returns (best, rows)."""
    _require_scc()
    combos = list(itertools.product(
        RRF_KS, LOCI_WEIGHTS, LOCI_FLOORS, AUTHORITY_MARGINS,
        MIN_PER_STORES, FUSION_MODES, N_RESULTS_GRID, FETCH_MULTIPLIERS))
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
