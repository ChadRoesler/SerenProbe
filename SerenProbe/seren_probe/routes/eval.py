"""
Eval routes - /eval/results, /eval/run.

Runs evaluations against live stores and returns metrics. Results cached
in app.state for the viewer to read.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/eval", tags=["eval"])


@router.get("/results")
async def get_eval_results(request: Request):
    return request.app.state.eval_results or {
        "stores": {}, "query_count": 0, "date": ""
    }


@router.post("/run")
async def run_eval(request: Request):
    # Topology path: eval the N stores that docker_start spun up, scoring the
    # uploaded questions (optionally seeding an uploaded dataset first).
    ts = getattr(request.app.state, "topology_state", None)
    topo = getattr(request.app.state, "compiled_topology", None)
    if ts and topo:
        from ..runtime.live_eval import run_topology_evaluation
        from ..core.resolve import resolve_eval_inputs
        from ..core.seed_dataset import SeedError
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        # Config-first: seeds + questions come from the compiled ProbeConfig
        # (DefaultLociSeed / DefaultMemorySeed / per-node Seed / Questions); the
        # body can still override questions or supply a legacy pools seed.
        try:
            ei = resolve_eval_inputs(topo, body)
        except SeedError as e:
            raise HTTPException(status_code=400, detail={
                "stage": "validate", "errors": e.errors, "warnings": e.warnings})
        if not ei.questions:
            raise HTTPException(
                status_code=400,
                detail=("No questions to score against - set DefaultQuestions in the "
                        "ProbeConfig (or a per-node Questions), or pass body.questions."))
        # SEED GUARD. seed_from_plan is ADDITIVE - it does NOT clear the stores
        # first - so seeding an already-seeded pod silently gives you a SECOND copy
        # of the whole corpus, and every metric quietly lies. A fresh spin-up is
        # empty (seeded=False) and the first eval seeds it; after that we score what
        # is already there. An ADOPTED pod is already full. Pass reseed:true only if
        # you actually want another copy stacked on top.
        ts_seeded = bool(ts.get("seeded"))
        force_reseed = bool(body.get("reseed"))
        do_seed = ei.seed and (not ts_seeded or force_reseed)
        # RECORD THE SEED BEFORE THE EVAL, NOT AFTER.
        #
        # Seeding runs FIRST, inside run_topology_evaluation, and is finished long
        # before scoring starts. Recording it afterwards makes a completed side
        # effect conditional on a later, unrelated step succeeding -- so any eval
        # failure (a /fact timeout on the last corpus, an operator ctrl-C, a store
        # falling over on question 900) leaves a FULLY SEEDED pod flagged unseeded.
        # Adopt then carries seeded=False in good faith and the next run seeds a
        # second copy on top. seed_from_plan is additive; nothing errors; every
        # metric quietly lies. Observed live: a 54-minute run died at All-scc and
        # the following eval duplicated short-term and facts across all 22 stores.
        #
        # THE TRADEOFF, NAMED. Marking early means a failure DURING seeding leaves
        # a partially-seeded pod flagged as seeded, and the next eval scores low
        # instead of topping it up. That is the better failure: low scores are LOUD
        # and the fix (reseed:true on a partial pod) is one flag, whereas silent
        # duplication corrupts every number without a single warning. Loud and wrong
        # beats quiet and wrong.
        if do_seed:
            ts["seeded"] = True
            try:
                from ..runtime.docker_env import save_topology_state, load_topology_state
                saved = load_topology_state() or {}
                save_topology_state({**saved, **ts})
            except Exception as exc:     # noqa: BLE001
                # Do NOT swallow this silently -- an unpersisted flag is exactly how
                # the duplicate-corpus bug reaches the next run.
                logger.warning("could not persist seeded flag before eval: %s", exc)
        try:
            # run_in_threadpool: seeding is thousands of BLOCKING httpx round-trips and
            # takes HOURS on a big corpus. Called directly from this async route it
            # seizes uvicorn's only worker for the entire seed -- the whole app, viewer
            # included, is frozen until it finishes. Exactly the bug we fixed in the
            # Docker routes (blocking subprocess.run in an async def), with a different
            # victim. /eval/regrade already got this right; /eval/run never did.
            #
            # It also gives seed_from_plan a plain worker thread to spawn its own
            # per-store pool from, instead of fighting the event loop for it.
            from starlette.concurrency import run_in_threadpool
            results = await run_in_threadpool(
                run_topology_evaluation,
                topo, ts["url_of"], ei.questions,
                seed_by_store=ei.seed_by_store, seed=do_seed,
                questions_by_store=ei.questions_by_store,
                max_parallel_stores=body.get("max_parallel_stores", 8),
                max_parallel_questions=body.get("max_parallel_questions", 8))
        except Exception as exc:
            logger.error("Topology eval failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        if do_seed:
            ts["seeded"] = True          # idempotent re-confirm; the authoritative write
                                         # happened BEFORE the eval, see the note above
            try:
                # ..runtime.docker_env, NOT ..docker_env -- docker_env moved into the
                # runtime layer. The stale path raised ImportError, and the bare except
                # below ATE IT: the seeded flag was set in memory and never written to
                # disk, so a restart read seeded=False and the next eval RESEEDED an
                # already-full pod, stacking a second copy of the corpus. The guard
                # twenty lines up exists to prevent exactly that, and a swallowed
                # ImportError quietly walked around it.
                from ..runtime.docker_env import save_topology_state, load_topology_state
                saved = load_topology_state() or {}
                save_topology_state({**saved, **ts})
            except Exception as exc:     # noqa: BLE001
                # Still non-fatal -- but it SAYS SO now. A silent pass here is how a
                # persistence bug hides for a week.
                logger.warning("could not persist topology state (seeded flag): %s", exc)
        if ts_seeded and not force_reseed:
            results = {**results, "seed_skipped": (
                "stores were already seeded - scored as-is. seed_from_plan is additive, so "
                "reseeding would stack a second copy of the corpus. Pass reseed:true to force it.")}
        if ei.warnings:
            results = {**results, "resolve_warnings": ei.warnings}
        request.app.state.eval_results = results
        return {"ok": True, "results": results}

    # NO SILENT FALLBACK TO LIVE STORES. This used to drop through to the legacy
    # hardcoded-five-store path, which reads its URLs from app.state.store_config --
    # defaults memory=7420, loci=7421/7422, scc=7423/7424. Those are the OPERATOR'S
    # REAL STORES. And run_live_evaluation SEEDS them if it finds them empty. So
    # "hit Run Eval with no topology up" was one click from writing a synthetic
    # corpus into a live SerenMemory, and the only thing preventing it was that the
    # real store happened to be non-empty. That is not a safety mechanism, that is
    # luck. (The write_guard now refuses it at the transport too -- belt AND braces,
    # because this one already went off once.)
    raise HTTPException(
        status_code=400,
        detail=("No topology is running - Start a topology first (Docker tab). "
                "SerenProbe only evaluates stores it spun up itself; it will not "
                "reach out to whatever happens to be listening on the default ports."))


@router.post("/regrade")
async def run_regrade(request: Request):
    """Roll the active ProbeConfig's CorpusRegrades sets against the running
    topology's corpora - a capture-once/replay-many SCC fusion sweep. Read-only on
    the eval containers; never touches live memory. 501 if SCC isn't importable
    host-side (regrade replays the REAL Federation to predict each knob combo)."""
    ts = getattr(request.app.state, "topology_state", None)
    topo = getattr(request.app.state, "compiled_topology", None)
    if not (ts and topo):
        raise HTTPException(status_code=400,
                            detail="No topology running - Start a topology first.")
    if not getattr(topo, "corpus_regrades", None):
        raise HTTPException(status_code=400,
                            detail="No CorpusRegrades sets in the active ProbeConfig.")
    from ..core.resolve import resolve_eval_inputs
    from ..core.seed_dataset import SeedError
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        ei = resolve_eval_inputs(topo, body)
    except SeedError as e:
        raise HTTPException(status_code=400, detail={
            "stage": "validate", "errors": e.errors, "warnings": e.warnings})
    if not ei.questions:
        raise HTTPException(status_code=400,
                            detail="No questions to regrade - set a Questions in the ProbeConfig.")
    from ..runtime.regrade_live import run_live_regrade
    from starlette.concurrency import run_in_threadpool
    try:
        # sort_by defaults to docket_coverage, NOT ndcg. metrics._ndcg returns 1.0
        # when `relevant` is empty, and on a corpus store `relevant` is derived from
        # the hits themselves -- so a combo that retrieves NOTHING scores a perfect
        # ndcg and WINS the sweep. Coverage divides by the ground-truth count and is
        # the only metric here that can see a miss. Callers can still ask for another
        # sort explicitly; they just don't get the footgun by default.
        #
        # run_in_threadpool: the sweep is dozens of BLOCKING httpx round-trips
        # (configure + search per combo per question). On the event loop it freezes
        # the entire app for the length of the sweep.
        results = await run_in_threadpool(
            run_live_regrade,
            topo, ts["url_of"], ei.questions,
            sort_by=body.get("sort_by", "docket_coverage"),
            max_parallel_corpora=body.get("max_parallel_corpora", 8))
    except Exception as exc:
        logger.error("Regrade failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    request.app.state.regrade_results = results
    return {"ok": True, "results": results}
