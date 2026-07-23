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
    """The last eval's results - from memory, or rehydrated from disk.

    THE FALLBACK IS THE POINT. Results used to live only in app.state, so a restart
    or an adopt returned {} and the viewer rendered "not yet seeded/evaluated" about
    a pod that had been fully scored. The app didn't know, and said something
    definite anyway -- the same class of mistake as grading against a missing answer
    key, and it sends you to re-run an hour of work you already have.

    Rehydrating HERE rather than in the adopt route covers every way app.state can
    end up empty (restart, adopt, a worker recycle) in one place, instead of one fix
    per entry point and a new gap the next time someone adds another.

    Guarded by project name: results belong to the pod that produced them, and a
    different fleet's numbers are worse than none.
    """
    cached = request.app.state.eval_results
    if cached:
        return cached
    ts = getattr(request.app.state, "topology_state", None)
    topo = getattr(request.app.state, "compiled_topology", None)
    if ts and ts.get("project_name"):
        try:
            from ..runtime.docker_env import load_eval_results, eval_results_staleness
            env = load_eval_results(ts["project_name"])
        except Exception as exc:      # noqa: BLE001 - a convenience read, never fatal
            logger.warning("could not rehydrate eval results: %s", exc)
            env = None
        if env:
            # DISCARD, don't warn-and-show. A stale capture is excluded and silently
            # re-captured because that is cheap and recoverable; an eval result is an
            # HOUR of work that cannot be silently recomputed, so the honest move is to
            # get out of the way and let the operator decide to re-run. Results that
            # describe a fleet which no longer exists are not degraded data -- they are
            # a DIFFERENT fleet's data, and showing them with a caveat invites reading
            # them anyway.
            fp = qh = ""
            if topo is not None:
                try:
                    from ..core.topology import topology_fingerprint
                    from ..core.resolve import resolve_eval_inputs
                    from ..runtime import regrade as _rg
                    fp = topology_fingerprint(topo)
                    qh = _rg.corpus_question_hash(resolve_eval_inputs(topo, {}).questions)
                except Exception:      # noqa: BLE001 - unknown means "don't judge on it"
                    pass
            why = eval_results_staleness(env, fp, ts.get("seeded_at", ""), qh)
            if why:
                logger.info("discarding saved eval results: %s", why)
                try:
                    from ..runtime.docker_env import clear_eval_results
                    clear_eval_results()
                except Exception:      # noqa: BLE001
                    pass
                return {"stores": {}, "query_count": 0, "date": "", "discarded": why}
            # `restored` is not decoration. "Scored in this process" and "scored
            # earlier, read back off disk" are different confidence levels, and the
            # viewer says so rather than presenting a rehydrated table as live.
            results = {**env["results"], "restored": True,
                       "restored_at": env.get("saved_at", "")}
            request.app.state.eval_results = results
            return results
    return {"stores": {}, "query_count": 0, "date": ""}


@router.delete("/results")
async def delete_eval_results(request: Request):
    """Throw the saved eval away on purpose.

    The automatic checks catch the changes they can SEE. They cannot see a rebuilt
    image at the same version pin, or edited seed CONTENT behind an unchanged path,
    or any of the reasons an operator simply knows the numbers are junk. Without
    this the only way to clear a cache file is to tear down the pod, which is a
    terrible trade for a stale JSON.
    """
    from ..runtime.docker_env import clear_eval_results
    request.app.state.eval_results = {}
    clear_eval_results()
    return {"ok": True}


@router.get("/progress")
async def get_eval_progress():
    """Live X/Y status per store for the Eval table's status column.

    Polled from the viewer while a blocking /eval/seed or /eval/run request is
    still in flight on a SEPARATE connection -- see runtime/progress.py for why
    that split is required (the seed/run response IS the finished result; there
    is nothing to poll on that request). Returns instantly regardless of what
    the worker threads are doing, and reads {} once nothing is running.

    SHAPE IS UNCHANGED ON PURPOSE. Early per-store results live at /eval/partials
    instead of being folded in here as {"stores": ..., "partials": ...}. Wrapping
    this response would have silently emptied the status column of every caller
    already reading it -- a new feature is not a reason to break a working
    contract when a new endpoint costs nothing.
    """
    from ..runtime import progress
    return progress.snapshot()


@router.get("/partials")
async def get_eval_partials():
    """Snapshots of every store scored SO FAR in the run that is currently going.

    Lets the Eval table fill in column by column instead of staying blank for the
    length of the run. Loci columns finish in seconds; corpora are serialized and
    All-scc fans 22 containers, so without this every fast column is hostage to the
    slowest thing in the topology. Error snapshots publish too -- a container that
    died in wave 1 should be visible immediately, not after the corpora finish.

    Rows are the same shape as /eval/results rows (flags, negative_test,
    is_catchall are all attached before publishing), so the viewer can render them
    through the existing code path with no special case.

    A PEEK, NOT THE RESULT. Anything computed ACROSS stores is absent here by
    construction -- the docket with/without-edges comparison and the ground_truth
    notes cannot be computed until every column is in. An empty docket here is
    correct, not a bug. Read this to watch; read /eval/results to conclude.
    """
    from ..runtime import progress
    return {"partials": progress.partials()}


@router.post("/seed")
async def seed_eval(request: Request):
    """Seed the running topology's stores from the ProbeConfig -- and ONLY that.
    No scoring, no metrics. Split off from /eval/run so the Eval tab can offer
    'seed' and 'evaluate' as two separate, honest actions instead of one button
    that silently seeds on your behalf the first time you click 'evaluate'.

    Same additive-seed guard as /eval/run: a fresh pod (seeded=False) seeds; an
    already-seeded pod is a no-op unless reseed:true, because seed_from_plan
    does not clear first and reseeding stacks a second copy of the corpus.
    """
    ts = getattr(request.app.state, "topology_state", None)
    topo = getattr(request.app.state, "compiled_topology", None)
    if not (ts and topo):
        raise HTTPException(
            status_code=400,
            detail=("No topology is running - Start a topology first (Docker tab). "
                    "SerenProbe only seeds stores it spun up itself."))
    from ..core.resolve import resolve_eval_inputs
    from ..core.seed_dataset import SeedError, seed_from_plan
    from ..runtime import progress
    progress.clear_all()
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

    ts_seeded = bool(ts.get("seeded"))
    force_reseed = bool(body.get("reseed"))
    if not ei.seed_by_store:
        return {"ok": True, "seeded": False,
                "note": "config has no seed sources - nothing to seed (stores are pre-seeded)."}
    if ts_seeded and not force_reseed:
        return {"ok": True, "seeded": False,
                "note": ("stores are already seeded - seed_from_plan is additive, so seeding "
                         "again would stack a second copy of the corpus. Pass reseed:true to "
                         "force it.")}

    # RECORD BEFORE THE WRITE, same reasoning as /eval/run: seeding is thousands of
    # blocking round-trips, and marking AFTER makes a completed side effect
    # conditional on the persistence write below succeeding too. A crash mid-seed
    # should leave a LOUD partial (seeded=True, low scores next eval), never a
    # silent stack of a second corpus on the next attempt.
    #
    # seeded_at is the reference the capture-staleness guard compares against: a
    # regrade capture OLDER than this timestamp describes data that no longer
    # exists. ISO-8601 UTC on purpose -- string comparison IS chronological
    # comparison for these, no parsing needed at the check site.
    from datetime import datetime
    ts["seeded"] = True
    ts["seeded_at"] = datetime.utcnow().isoformat()
    try:
        from ..runtime.docker_env import save_topology_state, load_topology_state
        saved = load_topology_state() or {}
        save_topology_state({**saved, **ts})
    except Exception as exc:     # noqa: BLE001
        logger.warning("could not persist seeded flag before seed-only run: %s", exc)

    try:
        from starlette.concurrency import run_in_threadpool
        from ..runtime.live_eval import post as _post, _delete as _delete_fn
        from ..runtime.write_guard import allow_targets
        allow_targets(ts["url_of"].values())
        seed_result = await run_in_threadpool(
            seed_from_plan, topo, ei.seed_by_store, ts["url_of"], _post, _delete_fn,
            max_parallel_stores=body.get("max_parallel_stores", 8), report_progress=True)

        live_import_report: dict = {}
        if any(getattr(n, "live_url", None) for n in topo.loci + topo.memory):
            from ..runtime.live_import import import_live_stores
            live_import_report = await run_in_threadpool(import_live_stores, topo, ts["url_of"])
    except Exception as exc:
        logger.error("Seed-only run failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    try:
        from ..runtime.docker_env import save_topology_state, load_topology_state
        saved = load_topology_state() or {}
        save_topology_state({**saved, **ts})
    except Exception as exc:     # noqa: BLE001
        logger.warning("could not persist topology state (seeded flag) after seed-only run: %s", exc)

    result = {"ok": True, "seeded": True,
               "loci_counts": seed_result.loci_counts, "memory_counts": seed_result.memory_counts}
    if live_import_report:
        result["live_import"] = live_import_report
    if ei.warnings:
        result["resolve_warnings"] = ei.warnings
    return result


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
        from ..runtime import progress
        progress.clear_all()
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
            from datetime import datetime
            ts["seeded"] = True
            ts["seeded_at"] = datetime.utcnow().isoformat()   # the staleness reference
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
        # PERSIST, so the next process (or an adopt) knows this pod has been scored.
        # After the results are in app.state and in the response, so a write failure
        # costs the convenience and never the run.
        try:
            from ..runtime.docker_env import save_eval_results
            from ..core.topology import topology_fingerprint
            from ..runtime import regrade as _rg
            save_eval_results(ts.get("project_name", ""), results,
                              fingerprint=topology_fingerprint(topo),
                              seeded_at=ts.get("seeded_at", ""),
                              question_hash=_rg.corpus_question_hash(ei.questions))
        except Exception as exc:     # noqa: BLE001
            logger.warning("could not persist eval results: %s", exc)
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


@router.get("/regrade/plan")
async def get_regrade_plan(request: Request):
    """What a regrade WOULD sweep, resolved but not run.

    A sweep is minutes-to-hours per corpus and corpora run serially, so "which of my
    corpora are actually in this, and how many combos each" is worth answering BEFORE
    spending the afternoon rather than after. With per-corpus CorpusRegrades the answer
    stopped being readable off the config: sets are inherited, overridden by name, or
    opted out of, and that resolution happens in code. This calls the SAME resolver the
    sweep uses (sets_for_corpus), so the plan cannot disagree with what executes.

    Read-only and instant -- touches no container, turns no knob.
    """
    topo = getattr(request.app.state, "compiled_topology", None)
    if not topo:
        return {"corpora": [], "note": "No topology compiled - set an active ProbeConfig first."}
    from ..runtime.regrade_live import sets_for_corpus, compact_combos

    base = list(getattr(topo, "corpus_regrades", None) or [])
    rows, total_combos = [], 0
    for c in topo.corpus:
        if getattr(c, "is_catchall", False) or not c.stores:
            continue
        own = getattr(c, "regrades", None)
        own_names = {r.name for r in (own or [])}
        sets = sets_for_corpus(c, base)
        if not sets:
            # Say WHY it is out. "Opted out" and "nothing to inherit" look identical in
            # a table and mean completely different things -- one is a decision, the
            # other is a config gap you probably did not intend.
            rows.append({"corpus": c.name, "skipped": True, "sets": [], "combos": 0,
                         "reason": ("opted out (empty CorpusRegrades)" if own is not None
                                    else "no top-level CorpusRegrades to inherit")})
            continue
        srows, n_total = [], 1          # +1 for the baseline measurement
        for rs in sets:
            _combos = compact_combos(rs.overrides)
            n = len(_combos)
            n_total += n
            srows.append({"name": rs.name, "combos": n,
                          "source": "corpus" if rs.name in own_names else "base",
                          "knobs": {k: list(v) for k, v in rs.overrides.items()},
                          # The ACTUAL combos, not just how many. The viewer renders a
                          # skeleton row per combo so a running sweep shows every row it
                          # will eventually fill -- and it must not rebuild the product
                          # in JS to do it. compact_combos is the contract; a second
                          # implementation of it in the viewer is a second thing to go
                          # stale the next time a knob's semantics change.
                          "combo_params": _combos})
        total_combos += n_total
        # combos EXCLUDES the baseline, measured reports the total passes. They used to
        # be one number: a single-combo set displayed "2", which reads as a miscount
        # rather than "your combo plus the baseline it is measured against". Invisible
        # at 44, glaring at 2 -- and a number the operator cannot reconcile is a number
        # they stop trusting.
        rows.append({"corpus": c.name, "skipped": False, "sets": srows,
                     "combos": n_total - 1, "measured": n_total})
    return {"corpora": rows, "total_combos": total_combos,
            "swept": sum(1 for r in rows if not r["skipped"]), "corpus_count": len(rows)}


@router.post("/capture")
async def run_capture(request: Request):
    """Capture NOW, sweep later. One read-only /search pass over each eligible
    corpus's member stores, frozen to disk with a timestamp and a question-set
    fingerprint. The regrade button then replays from this file without touching a
    single container -- capture is the only part of a pure-fusion sweep that costs
    anything, so splitting it off makes regrades effectively free to repeat.

    body.corpus limits it to one corpus (re-capture just the one you reseeded).
    Eligible = has at least one pure (non-hops) set after per-corpus resolution;
    hops-only corpora get no capture because nothing can ever replay against one.
    """
    ts = getattr(request.app.state, "topology_state", None)
    topo = getattr(request.app.state, "compiled_topology", None)
    if not (ts and topo):
        raise HTTPException(status_code=400,
                            detail="No topology running - Start a topology first.")
    from ..runtime import regrade as regrade_mod
    if not regrade_mod._SCC_AVAILABLE:
        raise HTTPException(status_code=400, detail=(
            "capture-replay needs 'seren-corpus-callosum' installed host-side: "
            "pip install seren-corpus-callosum"))
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
    corpus = body.get("corpus")
    if corpus and corpus not in {c.name for c in topo.corpus}:
        raise HTTPException(status_code=400, detail=f"unknown corpus {corpus!r}")
    from ..runtime import progress
    progress.clear_all()
    try:
        captures = await regrade_mod.capture_corpora(
            topo, ts["url_of"], ei.questions,
            corpus_filter={corpus} if corpus else None, report_progress=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Capture failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    if not captures:
        return {"ok": True, "captured": [], "note": (
            "No corpus was eligible - a capture only serves pure-fusion sets, and "
            "every corpus in scope either has none or opted out.")}
    qhash = regrade_mod.corpus_question_hash(ei.questions)
    from ..runtime.docker_env import save_corpus_captures
    save_corpus_captures(ts.get("project_name", ""), captures, qhash)
    return {"ok": True, "captured": sorted(captures), "question_hash": qhash}


@router.get("/capture/status")
async def get_capture_status(request: Request):
    """Which corpora have a saved capture, and whether each is still TRUSTWORTHY.

    Stale is decided here, in one place, by the same rules the regrade route
    enforces: (a) captured_at older than the pod's seeded_at -- the capture
    describes data that no longer exists; (b) question-hash mismatch -- the capture
    cannot answer the current questions and every miss would grade as a retrieval
    failure that never happened. The viewer renders this; it never re-derives it.
    """
    ts = getattr(request.app.state, "topology_state", None)
    topo = getattr(request.app.state, "compiled_topology", None)
    if not (ts and topo):
        return {"corpora": {}, "note": "No topology running."}
    from ..runtime.docker_env import load_corpus_captures
    env = load_corpus_captures(ts.get("project_name", ""))
    if not env:
        return {"corpora": {}, "seeded_at": ts.get("seeded_at", "")}
    qhash_now = ""
    try:
        from ..core.resolve import resolve_eval_inputs
        from ..runtime import regrade as regrade_mod
        ei = resolve_eval_inputs(topo, {})
        qhash_now = regrade_mod.corpus_question_hash(ei.questions)
    except Exception:      # noqa: BLE001 - status must never 500 over a config hiccup
        pass
    seeded_at = ts.get("seeded_at", "")
    hash_ok = (not qhash_now) or env.get("question_hash") == qhash_now
    out = {}
    for name, entry in env.get("corpora", {}).items():
        cat = entry.get("captured_at", "")
        reseeded = bool(seeded_at and cat and cat < seeded_at)
        out[name] = {
            "captured_at": cat,
            "stale": reseeded or not hash_ok,
            "reason": ("stores were reseeded after this capture" if reseeded
                       else "" if hash_ok else "question set changed since this capture")}
    return {"corpora": out, "seeded_at": seeded_at,
            "question_hash": env.get("question_hash", ""), "hash_ok": hash_ok}


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
    if not getattr(topo, "corpus_regrades", None) and not any(
            getattr(c, "regrades", None) for c in topo.corpus):
        # Checks BOTH levels. A config with no top-level CorpusRegrades but per-corpus
        # sets on a few corpora is the opt-in workflow, and the old top-level-only
        # guard rejected it at the door.
        raise HTTPException(
            status_code=400,
            detail=("No CorpusRegrades sets anywhere in the active ProbeConfig - add "
                    "a top-level CorpusRegrades (applies to every corpus) or a "
                    "per-corpus one (applies to just that corpus)."))
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
    # Same clear_all as /eval/seed and /eval/run: a stale row from a previous,
    # unrelated operation must never bleed into this one's table.
    from ..runtime import progress
    progress.clear_all()
    try:
        # sort_by defaults to docket_coverage, NOT ndcg. Coverage is the SCC's mission
        # metric: it divides matched docket items by the number the question ASKED for,
        # so it answers "did the assembled briefing carry the ground" rather than "was
        # the ranking pretty".
        #
        # (This comment used to justify the default by claiming metrics._ndcg returns
        # 1.0 when `relevant` is empty, so an empty result set would win the sweep.
        # That was true and is not any more -- _ndcg returns 0.0 in that case now, on
        # purpose, so unscorable questions score like HR and recall instead of
        # inflating. The default stands on the reason above. There were TWO copies of
        # the stale rationale, here and in regrade_live; a duplicated explanation is a
        # duplicated chance to go stale, which is exactly what happened.)
        #
        # max_parallel_corpora defaults to 1: corpora fan into SHARED member
        # containers, so parallel sweeps contend and the contention grades as a knob
        # result. run_live_regrade serializes regardless and warns if asked otherwise;
        # the default matches the behaviour so the warning means something.
        #
        # run_in_threadpool: the sweep is dozens of BLOCKING httpx round-trips
        # (configure + search per combo per question). On the event loop it freezes
        # the entire app for the length of the sweep.
        # ENGINE SELECTION. Two engines measure the same thing at wildly different
        # cost, and neither is universally correct:
        #
        #   live    - POST /configure + re-search every question, PER COMBO. Exercises
        #             the real containers end to end, so it can sweep ANY knob. Also
        #             ~11 minutes a combo: 44 sets against one corpus is most of a day.
        #   capture - one read-only /search pass per corpus, frozen, then every combo
        #             replays the REAL Federation in-process against that capture.
        #             Seconds instead of hours, same fusion code, same hits.
        #
        # Capture-replay is faithful for every knob that only RE-FUSES candidates the
        # stores already returned: rrf_k, loci_weight, loci_floor, authority_margin,
        # min_per_store, fusion_mode, and n_results/fetch_multiplier up to capture_n.
        # It CANNOT reproduce hops/edges -- the /by_topic call depends on the regraded
        # packet's own ids and centre topics, so it is packet-coupled and a single
        # frozen /search cannot answer it. Sweeping hops on the fast path would return
        # confident, meaningless numbers, which is worse than being slow.
        #
        # So: auto picks capture when it is faithful and live when it is not, and says
        # which it used in the response. An explicit mode is honoured, except that
        # mode=capture with packet-coupled knobs is REFUSED rather than quietly
        # downgraded -- a caller asking for the fast path on a sweep it cannot do has a
        # wrong expectation, and a 400 corrects it where a silent fallback would not.
        mode = str(body.get("mode", "auto")).lower()
        if mode not in ("auto", "live", "capture"):
            raise HTTPException(status_code=400, detail="mode must be auto, live or capture")
        # Per-corpus button: body.corpus scopes the whole run to one corpus. Validated
        # here so a typo is a 400 naming the real corpora, not an empty result that
        # reads as "nothing to sweep".
        one_corpus = body.get("corpus")
        if one_corpus and one_corpus not in {c.name for c in topo.corpus}:
            raise HTTPException(status_code=400, detail=(
                f"unknown corpus {one_corpus!r} - declared corpora: "
                f"{sorted(c.name for c in topo.corpus)}"))
        corpus_filter = {one_corpus} if one_corpus else None

        from ..runtime.regrade_live import sets_for_corpus, is_packet_coupled
        _base = list(getattr(topo, "corpus_regrades", None) or [])
        # Scoped to the requested corpus, so `routed`/`set_names` describe THIS run
        # rather than the whole config.
        _scoped = [c for c in topo.corpus
                   if not corpus_filter or c.name in corpus_filter]
        _all_sets = [rs for c in _scoped for rs in sets_for_corpus(c, _base)]
        coupled = sorted({rs.name for rs in _all_sets if is_packet_coupled(rs)})
        pure = sorted({rs.name for rs in _all_sets if not is_packet_coupled(rs)})

        from ..runtime import regrade as regrade_mod
        scc_ok = regrade_mod._SCC_AVAILABLE

        if mode == "capture" and coupled:
            raise HTTPException(status_code=400, detail=(
                f"capture-replay cannot sweep packet-coupled sets {coupled} - they "
                f"depend on the regraded packet's own ids/topics, so a frozen /search "
                f"capture cannot reproduce them. Use mode=auto to route them to the "
                f"live engine automatically, or mode=live for everything."))
        if mode == "capture" and not scc_ok:
            raise HTTPException(status_code=400, detail=(
                "capture-replay replays the real SCC Federation and needs the "
                "'seren-corpus-callosum' package installed in this environment: "
                "pip install seren-corpus-callosum"))

        # PER-SET ROUTING, not per-run. Routing by run meant a single hops set dragged
        # every pure-fusion set onto the eight-hour path with it -- on a 4-set config
        # that is 3 sets paying the slow price for the 1 that needs it. Each set goes
        # to the only engine that can measure it faithfully, and the results merge.
        run_capture = scc_ok and pure and mode in ("auto", "capture")
        run_live = bool(coupled) if mode == "auto" else (mode == "live")
        if mode == "live":
            run_capture, run_live = False, True
        if not scc_ok and mode == "auto":
            run_capture, run_live = False, True      # no SCC: everything on live

        merged: dict[str, dict] = {}
        engines: list[str] = []

        # SAVED CAPTURES, staleness-gated HERE. A capture older than the pod's
        # seeded_at describes data that no longer exists; a question-hash mismatch
        # cannot answer the current queries. Either way it is EXCLUDED -- the engine
        # captures fresh for that corpus instead, so nothing silently replays against
        # dead data and nothing blocks. The response hollers per corpus
        # (capture_source on each result, stale_captures for the rejected).
        saved_caps: dict = {}
        stale_notes: dict = {}
        if run_capture:
            try:
                from ..runtime.docker_env import load_corpus_captures
                env = load_corpus_captures(ts.get("project_name", ""))
                if env:
                    qhash_now = regrade_mod.corpus_question_hash(ei.questions)
                    hash_ok = env.get("question_hash") == qhash_now
                    seeded_at = ts.get("seeded_at", "")
                    for name, entry in env.get("corpora", {}).items():
                        cat = entry.get("captured_at", "")
                        if not hash_ok:
                            stale_notes[name] = "question set changed since capture"
                        elif seeded_at and cat and cat < seeded_at:
                            stale_notes[name] = "stores reseeded after capture"
                        else:
                            saved_caps[name] = entry.get("capture") or {}
            except Exception as exc:      # noqa: BLE001 - saved captures are a shortcut,
                logger.warning("could not load saved captures: %s", exc)  # never a wall

        def _absorb(res: dict, engine: str):
            """Fold one engine's per-corpus rows into the merged result, tagging every
            set row with the engine that produced it. Rows are NOT re-based across
            engines: each carries its own baseline and its deltas are relative to it,
            because the two baselines are genuinely different measurements (capture
            replays with edges off by construction). Silently mixing them would
            manufacture deltas nobody measured."""
            engines.append(engine)
            for c in res.get("corpora", []):
                name = c.get("corpus")
                for row in c.get("sets", []):
                    row["engine"] = engine
                slot = merged.setdefault(name, {"corpus": name, "sets": []})
                for key in ("flavor", "error"):
                    if key in c and key not in slot:
                        slot[key] = c[key]
                slot.setdefault(f"baseline_{engine}", c.get("baseline"))
                slot["sets"].extend(c.get("sets", []))

        if run_capture:
            _absorb(await regrade_mod.run_config_regrade(
                topo, ts["url_of"], ei.questions,
                eval_k=body.get("k", 10),
                sort_by=body.get("sort_by", "docket_coverage"),
                report_progress=True,
                set_filter=lambda s: not is_packet_coupled(s),
                corpus_filter=corpus_filter,
                saved_captures=saved_caps), "capture-replay")
        if run_live:
            # run_in_threadpool: the live sweep is dozens of BLOCKING httpx round-trips
            # (configure + search per combo per question). On the event loop it freezes
            # the entire app for the length of the sweep.
            _absorb(await run_in_threadpool(
                run_live_regrade,
                topo, ts["url_of"], ei.questions,
                sort_by=body.get("sort_by", "docket_coverage"),
                max_parallel_corpora=body.get("max_parallel_corpora", 1),
                report_progress=True,
                set_filter=(is_packet_coupled if mode == "auto" else None),
                corpus_filter=corpus_filter), "live")

        results = {
            "corpora": list(merged.values()),
            "sort_by": body.get("sort_by", "docket_coverage"),
            "engines": engines,
            "routed": {"capture-replay": pure if run_capture else [],
                       "live": coupled if (run_live and mode == "auto") else
                               (pure + coupled if run_live else [])},
            "set_names": sorted({rs.name for rs in _all_sets}),
        }
        if one_corpus:
            results["corpus"] = one_corpus
        if stale_notes:
            results["stale_captures"] = stale_notes
        if not scc_ok:
            results["engine_note"] = (
                "seren-corpus-callosum is not installed, so every set ran on the slow "
                "live engine. pip install seren-corpus-callosum to replay pure-fusion "
                "sets from a single capture.")
        elif coupled and mode == "auto":
            results["engine_note"] = (
                f"sets {coupled} contain packet-coupled knobs (hops/edges) and ran on "
                f"the live engine; {pure} replayed from a capture. Deltas are relative "
                f"to each engine's own baseline, not across engines.")
    except HTTPException:
        # Let deliberate 4xx through UNTOUCHED. The generic handler below catches
        # Exception, and HTTPException is one -- so without this, every validation
        # error raised inside this block (bad mode, capture-replay asked for a
        # packet-coupled sweep, SCC missing) comes back as a 500 with its
        # actionable message buried in a generic "Regrade failed".
        raise
    except Exception as exc:
        logger.error("Regrade failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    request.app.state.regrade_results = results
    return {"ok": True, "results": results}
