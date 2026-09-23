"""
seren_probe.runtime.eval_run - the ONE way an evaluation is started.

Both doors into "run the eval" - POST /eval/run and the MCP tool
run_evaluation - go through run_topology_eval() below. They used to be two
implementations: the route had the topology path with its seed guard and its
persistence, and the MCP tool imported `run_live_evaluation`, the retired
fixed-five-store seeder that this package quarantined for writing a
synthetic corpus into a real SerenMemory. Every MCP call was an ImportError,
and nothing tested it. One implementation, two thin callers, so the guard
that protects the route protects the tool by construction.

Errors are typed rather than HTTP-shaped so the route can turn them into the
status it always used and the MCP tool can turn them into a plain sentence.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class NoTopologyRunning(Exception):
    """No pod is up. The message is the whole explanation."""

    def __init__(self) -> None:
        super().__init__(
            "No topology is running - Start a topology first (Docker tab, or POST "
            "/docker/start with a ProbeConfig). SerenProbe only evaluates stores it "
            "spun up itself; it will not reach out to whatever happens to be "
            "listening on the default ports.")


class EvalInputError(Exception):
    """The ProbeConfig or the body could not be turned into questions and seeds."""

    def __init__(self, detail: Any) -> None:
        super().__init__(str(detail))
        self.detail = detail


class EvalFailed(Exception):
    """The evaluator itself raised."""


def lean(results: dict) -> dict:
    """Drop each store's per-question `q_detail` for the wire. It rides on the
    snapshot so it persists and rehydrates as ONE object with the run it belongs
    to -- but a 22-store report's detail is megabytes, and /eval/results is polled.
    The full object stays in app.state; the drill-down is fetched à la carte from
    /eval/detail. Non-destructive: builds a shallow copy, never mutates the cache."""
    if not isinstance(results, dict) or not isinstance(results.get("stores"), dict):
        return results
    stores = {}
    for name, snap in results["stores"].items():
        if isinstance(snap, dict) and "q_detail" in snap:
            stores[name] = {k: v for k, v in snap.items() if k != "q_detail"}
        else:
            stores[name] = snap
    return {**results, "stores": stores}


def configured_store_count(store_config: dict | None) -> int:
    """How many live-store URLs the operator has actually typed. This used to be
    the literal 5 on two routes, a leftover from the fixed-five design; the
    defaults are empty now and the honest number is usually 0."""
    if not isinstance(store_config, dict):
        return 0
    return sum(1 for k, v in store_config.items() if k.endswith("_url") and v)


async def run_topology_eval(state, body: dict | None = None) -> dict:
    """Score the running topology. Returns the FULL results (with q_detail);
    callers decide whether to lean them for the wire.

    *state* is the app's state object (request.app.state). Raises
    NoTopologyRunning, EvalInputError or EvalFailed; never an HTTPException,
    because the MCP tool is a caller too.
    """
    ts = getattr(state, "topology_state", None)
    topo = getattr(state, "compiled_topology", None)
    if not (ts and topo):
        # NO SILENT FALLBACK TO LIVE STORES. This used to drop through to the legacy
        # hardcoded-five-store path, which reads its URLs from app.state.store_config
        # -- the OPERATOR'S REAL STORES -- and run_live_evaluation SEEDED them if it
        # found them empty. "Run Eval with no topology up" was one click from writing
        # a synthetic corpus into a live SerenMemory, and the only thing preventing
        # it was that the real store happened to be non-empty. That is not a safety
        # mechanism, that is luck. (write_guard refuses it at the transport too --
        # belt AND braces, because this one already went off once.)
        raise NoTopologyRunning()

    from .live_eval import run_topology_evaluation
    from ..core.resolve import resolve_eval_inputs
    from ..core.seed_dataset import SeedError
    from . import progress

    body = body if isinstance(body, dict) else {}

    # Config-first: seeds + questions come from the compiled ProbeConfig
    # (DefaultLociSeed / DefaultMemorySeed / per-node Seed / Questions); the
    # body can still override questions or supply a legacy pools seed.
    progress.clear_all()
    try:
        ei = resolve_eval_inputs(topo, body)
    except SeedError as e:
        raise EvalInputError({"stage": "validate", "errors": e.errors, "warnings": e.warnings})
    if not ei.questions:
        raise EvalInputError(
            "No questions to score against - set DefaultQuestions in the ProbeConfig "
            "(or a per-node Questions), or pass body.questions.")

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
        from datetime import datetime
        ts["seeded"] = True
        ts["seeded_at"] = datetime.utcnow().isoformat()   # the staleness reference
        try:
            from .docker_env import save_topology_state, load_topology_state
            saved = load_topology_state() or {}
            save_topology_state({**saved, **ts})
        except Exception as exc:     # noqa: BLE001
            # Do NOT swallow this silently -- an unpersisted flag is exactly how
            # the duplicate-corpus bug reaches the next run.
            logger.warning("could not persist seeded flag before eval: %s", exc)

    try:
        # run_in_threadpool: seeding is thousands of BLOCKING httpx round-trips and
        # takes HOURS on a big corpus. Called directly from an async caller it
        # seizes uvicorn's only worker for the entire seed -- the whole app, viewer
        # included, is frozen until it finishes. It also gives seed_from_plan a
        # plain worker thread to spawn its own per-store pool from, instead of
        # fighting the event loop for it.
        from starlette.concurrency import run_in_threadpool
        results = await run_in_threadpool(
            run_topology_evaluation,
            topo, ts["url_of"], ei.questions,
            seed_by_store=ei.seed_by_store, seed=do_seed,
            questions_by_store=ei.questions_by_store,
            # LOCI+MEMORY PARALLEL, CORPORA SERIAL, QUESTIONS SERIAL.
            # Independent containers fan out for real concurrency. Corpus
            # columns then run one at a time regardless of this width --
            # an SCC fans into member containers that other columns share,
            # so parallel corpora contend instead of adding throughput. A
            # single store's own /search calls stay one-at-a-time so the
            # wall clock reads as "N stores at once" rather than a spray of
            # overlapping searches against the SAME store no one asked for.
            # Also forwarded to seed_from_plan, so throttling here throttles
            # the seed too.
            max_parallel_stores=body.get("max_parallel_stores", 8),
            max_parallel_questions=body.get("max_parallel_questions", 1),
            report_progress=True)
    except Exception as exc:
        logger.error("Topology eval failed: %s", exc)
        raise EvalFailed(str(exc)) from exc

    if do_seed:
        ts["seeded"] = True          # idempotent re-confirm; the authoritative write
                                     # happened BEFORE the eval, see the note above
        try:
            from .docker_env import save_topology_state, load_topology_state
            saved = load_topology_state() or {}
            save_topology_state({**saved, **ts})
        except Exception as exc:     # noqa: BLE001
            # Still non-fatal -- but it SAYS SO. A silent pass here is how a
            # persistence bug hides for a week.
            logger.warning("could not persist topology state (seeded flag): %s", exc)
    if ts_seeded and not force_reseed:
        results = {**results, "seed_skipped": (
            "stores were already seeded - scored as-is. seed_from_plan is additive, so "
            "reseeding would stack a second copy of the corpus. Pass reseed:true to force it.")}
    if ei.warnings:
        results = {**results, "resolve_warnings": ei.warnings}
    state.eval_results = results

    # PERSIST, so the next process (or an adopt) knows this pod has been scored.
    # After the results are in app.state, so a write failure costs the
    # convenience and never the run.
    try:
        from .docker_env import save_eval_results
        from ..core.topology import topology_fingerprint
        from . import regrade as _rg
        save_eval_results(ts.get("project_name", ""), results,
                          fingerprint=topology_fingerprint(topo),
                          seeded_at=ts.get("seeded_at", ""),
                          question_hash=_rg.corpus_question_hash(ei.questions))
    except Exception as exc:     # noqa: BLE001
        logger.warning("could not persist eval results: %s", exc)
    return results
