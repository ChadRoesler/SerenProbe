"""
Eval routes — /eval/results, /eval/run.

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
        from ..live_eval import run_topology_evaluation
        from ..seed_dataset import load_seed_dataset, load_questions, SeedError
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        seed_ds = None
        questions = []
        try:
            if body.get("seed_dataset") is not None:
                seed_ds = load_seed_dataset(body["seed_dataset"], topo)
            if body.get("questions") is not None:
                questions = load_questions(body["questions"])
        except SeedError as e:
            raise HTTPException(status_code=400, detail={
                "stage": "validate", "errors": e.errors, "warnings": e.warnings})
        if not questions:
            raise HTTPException(
                status_code=400,
                detail="Topology eval needs questions (body.questions) to score against.")
        try:
            results = run_topology_evaluation(
                topo, ts["url_of"], questions,
                seed_dataset=seed_ds, seed=(seed_ds is not None))
        except Exception as exc:
            logger.error("Topology eval failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        request.app.state.eval_results = results
        return {"ok": True, "results": results}

    # Legacy hardcoded-5 path (no topology up).
    scfg = request.app.state.store_config
    try:
        from ..live_eval import run_live_evaluation
        results = run_live_evaluation(
            memory_url=scfg["memory_url"],
            loci_nv_url=scfg["loci_nv_url"],
            loci_v_url=scfg["loci_v_url"],
            scc_nv_url=scfg["scc_nv_url"],
            scc_v_url=scfg["scc_v_url"],
        )
        request.app.state.eval_results = results
        return {"ok": True, "results": results}
    except Exception as exc:
        logger.error("Eval run failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
