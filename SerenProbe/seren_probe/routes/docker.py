"""
Docker routes - /docker/start, /docker/stop, /docker/status, /docker/run-eval,
/docker/config, /docker/validate.

Manages the Docker test environment lifecycle and deployment configuration.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

# EVERY docker helper in docker_env shells out to the Docker CLI with a BLOCKING
# subprocess.run(). Called straight from an `async def`, each one seizes uvicorn's
# event loop for its whole duration -- so the app cannot serve another request or
# even write a log line while it runs. For `compose ps` that's rude (a few hundred
# ms). For `compose up --build` it is a ten-minute outage that looks exactly like a
# dead button: no status, no logs, no progress, no error. Every one of them goes
# through run_in_threadpool. If you add a route here that touches Docker, it does
# too -- no exceptions.

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/docker", tags=["docker"])


@router.post("/start")
async def docker_start(request: Request):
    """Compile the ProbeConfig -> emit compose -> `docker compose up` the whole
    declared topology -> health-gate every store. Compile errors surface HERE,
    before Docker runs (compassion-first: the operator sees exactly what's wrong,
    naming node/rule/fix, without a container ever starting)."""
    from ..core.topology import load_probe_config, TopologyError
    from ..runtime.docker_env import spin_up_topology

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    # Source the ProbeConfig: request body wins, then the uploaded active config
    # (POST /docker/probeconfig), then the shipped example next to the package.
    probe_config = body.get("probe_config")
    if probe_config is None:
        probe_config = getattr(request.app.state, "probe_config_text", None)
    try:
        if probe_config is not None:
            topology = load_probe_config(probe_config)     # YAML string or dict
        else:
            default_pc = Path(__file__).resolve().parent.parent / "ProbeConfig.yml"
            topology = load_probe_config(default_pc)
    except TopologyError as e:
        raise HTTPException(status_code=400, detail={
            "stage": "compile", "errors": e.errors, "warnings": e.warnings})

    build = bool(body.get("build", True))
    project = (body.get("project_name") or "seren-probe-target")
    overrides = body.get("image_overrides")
    image_overrides = overrides if isinstance(overrides, dict) else None
    try:
        # The big one. `docker compose up --build` + a health-gate on every store
        # is minutes of blocking work; on the event loop it silences the entire app
        # for the duration. Off the loop, /docker/status keeps answering and the
        # request log keeps flowing, so a long build is VISIBLE instead of a freeze.
        env = await run_in_threadpool(
            spin_up_topology, topology, project_name=project, build=build,
            image_overrides=image_overrides)
    except Exception as exc:
        logger.error("Topology spin-up failed: %s", exc)
        raise HTTPException(status_code=500, detail={"stage": "spin_up", "error": str(exc)})

    # Stash for the eval + teardown. url_of maps EVERY store -> host URL.
    request.app.state.topology_state = {
        "project_name": env.project_name, "work_dir": env.work_dir, "url_of": env.url_of,
        "loci": env.loci, "memory": env.memory, "corpus": env.corpus,
        "seeded": False,       # fresh containers: empty stores, first eval seeds them
    }
    request.app.state.compiled_topology = topology

    # Persist the pod's identity so a RESTARTED app can adopt this fleet instead
    # of rebuilding + reseeding it. The containers outlive the process; its memory
    # of them shouldn't be the thing that dies.
    from ..runtime.docker_env import save_topology_state
    save_topology_state({**request.app.state.topology_state,
                         "probe_config_text": getattr(request.app.state, "probe_config_text", None)})

    return {"ok": True, "warnings": topology.warnings, "stores": env.url_of,
            "loci": env.loci, "memory": env.memory, "corpus": env.corpus, "logs": env.logs}


@router.get("/adoptable")
async def docker_adoptable(request: Request):
    """Is there a topology already RUNNING that we could adopt?

    Restarting SerenProbe wipes app.state but not Docker. Without this, a perfectly
    healthy fleet is orphaned and the operator rebuilds + reseeds for nothing.
    Only offers what `compose ps` says is actually up - the state file is a memory,
    the daemon is the truth.
    """
    if getattr(request.app.state, "topology_state", None):
        return {"adoptable": False, "reason": "a topology is already attached to this app"}
    from ..runtime.docker_env import adoptable_topology
    st = await run_in_threadpool(adoptable_topology)   # shells out to `compose ps`
    if not st:
        return {"adoptable": False}
    return {
        "adoptable": True,
        "project_name": st["project_name"],
        "stores": st.get("url_of", {}),
        "loci": st.get("loci", []), "memory": st.get("memory", []), "corpus": st.get("corpus", []),
        "running_count": st.get("running_count"), "service_total": st.get("service_total"),
        "seeded": bool(st.get("seeded")),
        "has_config": bool(st.get("probe_config_text")),
    }


@router.post("/adopt")
async def docker_adopt(request: Request):
    """Attach to the already-running topology instead of rebuilding it.

    Re-compiles the SAVED ProbeConfig (so knobs/questions/regrades are live again)
    and restores the store map. No build, no reseed - the containers already hold
    the data. If the pod was already seeded we say so and carry the flag, because
    seeding is ADDITIVE (seed_from_plan does not clear first): reseeding an
    already-seeded store silently gives you two copies of the corpus.
    """
    from ..runtime.docker_env import adoptable_topology
    from ..core.topology import load_probe_config, TopologyError

    st = await run_in_threadpool(adoptable_topology)   # shells out to `compose ps`
    if not st:
        raise HTTPException(status_code=404,
                            detail="No running topology to adopt (nothing is up, or no saved state).")
    pc_text = st.get("probe_config_text")
    if not pc_text:
        raise HTTPException(status_code=409,
                            detail=("A pod is running but its ProbeConfig wasn't saved, so its "
                                    "knobs/questions can't be restored. Stop it and Start fresh."))
    try:
        topology = load_probe_config(pc_text)
    except TopologyError as e:
        raise HTTPException(status_code=400, detail={
            "stage": "compile", "errors": e.errors, "warnings": e.warnings})

    request.app.state.topology_state = {
        "project_name": st["project_name"], "work_dir": st["work_dir"],
        "url_of": st.get("url_of", {}), "loci": st.get("loci", []),
        "memory": st.get("memory", []), "corpus": st.get("corpus", []),
        "seeded": bool(st.get("seeded")), "adopted": True,
    }
    request.app.state.compiled_topology = topology
    request.app.state.probe_config_text = pc_text

    return {"ok": True, "adopted": st["project_name"], "stores": st.get("url_of", {}),
            "loci": st.get("loci", []), "memory": st.get("memory", []),
            "corpus": st.get("corpus", []), "seeded": bool(st.get("seeded")),
            "warnings": topology.warnings,
            "note": ("Adopted the running fleet - no rebuild, no reseed. "
                     + ("Its stores are ALREADY SEEDED; Run Eval will score them as-is "
                        "(pass reseed:true to force a re-seed, which ADDS a second copy)."
                        if st.get("seeded") else
                        "Its stores look unseeded; the next Run Eval will seed them."))}


@router.post("/stop")
async def docker_stop(request: Request):
    # Topology (compose) path first.
    ts = getattr(request.app.state, "topology_state", None)
    if ts:
        from ..runtime.docker_env import compose_down
        try:
            await run_in_threadpool(compose_down, ts["work_dir"], ts["project_name"])
        except Exception as exc:
            logger.error("compose down failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        request.app.state.topology_state = None
        request.app.state.compiled_topology = None
        from ..runtime.docker_env import clear_topology_state
        clear_topology_state()      # the fleet is gone; don't offer to adopt a ghost
        return {"ok": True, "torn_down": ts["project_name"]}
    # Legacy single-container fallback.
    ds = request.app.state.docker_state
    if not ds or not ds.get("container_id"):
        return {"ok": False, "error": "Nothing running"}
    try:
        from ..runtime.docker_env import stop_container as _stop
        await run_in_threadpool(_stop, ds)
        request.app.state.docker_state = {}
        return {"ok": True}
    except Exception as exc:
        logger.error("Docker stop failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/status")
async def docker_status(request: Request):
    # Topology (compose) path: report the live store map + per-kind lists.
    ts = getattr(request.app.state, "topology_state", None)
    if ts:
        from ..runtime.docker_env import compose_ps
        ps = await run_in_threadpool(compose_ps, ts["work_dir"], ts["project_name"])
        # topology_state is only set after a health-gated spin-up, so treat the
        # fleet as running unless `compose ps` positively reports zero up. (The
        # old payload omitted 'running' entirely, so the viewer read it as stopped.)
        running = (ps["running"] > 0) if (ps["ok"] and ps["total"]) else True
        return {"managed": True, "mode": "topology", "running": running, "exists": True,
                "project_name": ts["project_name"], "stores": ts["url_of"],
                "loci": ts["loci"], "memory": ts["memory"], "corpus": ts["corpus"],
                "services": ps["services"], "running_count": ps["running"],
                "service_total": ps["total"]}
    ds = request.app.state.docker_state
    if ds and ds.get("container_id"):
        from ..runtime.docker_env import container_status
        status = await run_in_threadpool(
            container_status, ds.get("container_name", "seren-probe-target"))
        status["managed"] = True
        return status
    return {"managed": False, "running": False, "exists": False}


@router.post("/run-eval")
async def docker_run_eval(request: Request):
    try:
        from ..runtime.docker_env import launch_and_eval
        results = await run_in_threadpool(launch_and_eval)
        request.app.state.eval_results = results.get("eval", results)
        return {"ok": True, "results": results}
    except Exception as exc:
        logger.error("Docker run-eval failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Config management ─────────────────────────────────────────────────


@router.get("/config")
async def docker_config_list(request: Request):
    """List available deployment configs with validation status and Docker
    availability info."""
    from ..runtime.docker_env import _discover_configs, check_docker_available
    docker_info = await run_in_threadpool(check_docker_available)   # `docker info`
    configs = _discover_configs()
    active = getattr(request.app.state, "docker_config_active", "default")
    return {
        "docker": docker_info,
        "configs": [{
            "name": c.name,
            "description": c.description,
            "tags": c.tags,
            "has_compose": c.has_compose,
            "valid": c.valid,
            "validation_errors": c.validation_errors,
            "created_at": c.created_at,
        } for c in configs],
        "active_config": active,
    }


@router.post("/config/save")
async def docker_config_save(request: Request):
    """Save a new deployment config from uploaded files.

    Expects JSON body with:
      - name: str (config name)
      - dockerfile: str (Dockerfile content)
      - compose: str (optional docker-compose.yml content)
      - description: str (optional)
      - tags: list[str] (optional)
    """
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Config name is required")
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid config name")

    from ..runtime.docker_env import save_config
    try:
        cfg = save_config(
            name=name,
            dockerfile_content=body.get("dockerfile", ""),
            compose_content=body.get("compose", ""),
            description=body.get("description", ""),
            tags=body.get("tags", []),
        )
        return {"ok": True, "config": {
            "name": cfg.name,
            "valid": cfg.valid,
            "validation_errors": cfg.validation_errors,
            "has_compose": cfg.has_compose,
        }}
    except Exception as exc:
        logger.error("Config save failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/config/activate")
async def docker_config_activate(request: Request):
    """Set the active deployment config by name."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Config name is required")

    from ..runtime.docker_env import get_config_path
    path = get_config_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")

    request.app.state.docker_config_active = name
    request.app.state.docker_config_path = str(path)
    return {"ok": True, "active_config": name, "path": str(path)}


@router.post("/validate")
async def docker_validate(request: Request):
    """Validate the current Docker setup and active config.

    Returns Docker availability, config validation, and port checks.
    """
    from ..runtime.docker_env import (
        check_docker_available, validate_dockerfile, validate_compose,
        get_config_path,
    )
    docker_info = await run_in_threadpool(check_docker_available)

    active = getattr(request.app.state, "docker_config_active", "default")
    config_path_str = getattr(request.app.state, "docker_config_path", None)

    config_valid = True
    config_errors: list[str] = []

    if config_path_str:
        p = Path(config_path_str)
        if p.name == "docker-compose.yml":
            valid, errs = await run_in_threadpool(validate_compose, p)
            config_valid = valid
            config_errors = errs
        elif p.name == "Dockerfile":
            valid, errs = await run_in_threadpool(validate_dockerfile, p)
            config_valid = valid
            config_errors = errs
    else:
        # Fall back to the default Dockerfile location
        default_df = Path(__file__).resolve().parent.parent.parent / "Dockerfile"
        if default_df.exists():
            valid, errs = await run_in_threadpool(validate_dockerfile, default_df)
            config_valid = valid
            config_errors = errs
        else:
            config_valid = False
            config_errors.append("No Dockerfile found - nothing to build")

    return {
        "docker": docker_info,
        "active_config": active,
        "config_valid": config_valid,
        "config_errors": config_errors,
    }


@router.get("/config/{name}")
async def docker_config_get(name: str):
    """Retrieve the full config files for a named deployment config."""
    from ..runtime.docker_env import CONFIG_DIR
    config_dir = CONFIG_DIR / name
    if not config_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")

    result = {"name": name, "dockerfile": "", "compose": "", "metadata": {}}

    df = config_dir / "Dockerfile"
    if df.exists():
        result["dockerfile"] = df.read_text()

    compose = config_dir / "docker-compose.yml"
    if compose.exists():
        result["compose"] = compose.read_text()

    meta = config_dir / "metadata.json"
    if meta.exists():
        import json as _json
        result["metadata"] = _json.loads(meta.read_text())

    return result


@router.post("/probeconfig")
async def upload_probeconfig(request: Request):
    """Upload + validate a ProbeConfig (YAML string or object). Compiles it -
    surfacing errors/warnings compassion-first - and, if valid, stores it as the
    ACTIVE topology that /docker/start will use. This is how people swap in their
    own eval topology to eval against their own stuff."""
    from ..core.topology import load_probe_config, TopologyError
    try:
        body = await request.json()
    except Exception:
        body = {}
    pc = body.get("probe_config") if isinstance(body, dict) else body
    if pc is None:
        raise HTTPException(status_code=400,
                            detail="Provide 'probe_config' (a YAML string or a ProbeConfig object).")
    try:
        topo = load_probe_config(pc)
    except TopologyError as e:
        raise HTTPException(status_code=400, detail={
            "stage": "compile", "errors": e.errors, "warnings": e.warnings})

    # Reachability lint: can these questions actually be ANSWERED by the corpus
    # they'll be graded against? An expectation the seed never contains is
    # unanswerable at any fusion setting, and on the dashboard it looks exactly
    # like a retrieval failure. Catch it here, before a single container starts.
    # Best-effort: a missing/unreadable seed file must not 500 the validate - the
    # topology still compiled, and that's what this endpoint promises.
    lint_payload = None
    try:
        from ..core.resolve import resolve_plan
        from ..core.linters.plan import lint_plan
        plan = resolve_plan(topo)
        rep = lint_plan(topo, plan)
        lint_payload = {
            "ok": rep.ok,
            "checked": rep.checked,
            "errors": rep.errors,
            "warnings": rep.warnings,
            "notes": rep.notes,
            "multihop": [{"query": q, "expects": c, "holder": h} for q, c, h in rep.multihop],
            "unbridged": [{"query": q, "expects": c, "holder": h} for q, c, h in rep.unbridged],
            # AMBIGUOUS: the expectation EXISTS and IS reachable, and the query still
            # cannot single it out -- more than k other documents match just as well.
            # The store retrieves perfectly and scores near zero, which on a dashboard
            # is indistinguishable from a dead store, a broken embedder, and a missing
            # hop. We chased all three for eleven hours before the rows gave it up.
            "ambiguous": [{"query": q, "expects": c, "kind": h, "rivals": n}
                          for q, c, h, n in rep.ambiguous],
            # UNREACHABLE: the question DECLARES the traversal depth it needs, and no
            # config this ProbeConfig can run goes that deep. It will score ZERO in
            # every row of every sweep, and flat rows read as a retrieval CEILING.
            "unreachable": [{"query": q, "needs": n, "max": m}
                            for q, n, m in rep.unreachable],
        }
    except Exception as exc:                       # noqa: BLE001
        lint_payload = {"ok": None, "skipped": f"could not lint: {exc}"}

    # Persist the raw YAML so /start re-compiles it identically.
    import yaml as _yaml
    text = pc if isinstance(pc, str) else _yaml.safe_dump(pc, sort_keys=False)
    request.app.state.probe_config_text = text

    # HOT-SWAP: if a topology is already running and the new config asks for the
    # SAME containers, don't make the operator tear down and reseed just to change
    # knobs. Regrades run LIVE (/configure -> /search -> grade) against running
    # containers, and seeds/questions are read at eval time - only STRUCTURE
    # (names/ports/flags/wiring/version pins) is baked into the pod. Rebuilding +
    # reseeding 2500 items to widen `rrf_k` by one value costs an hour and buys
    # nothing. Same structure -> swap the knobs into the live topology and say so.
    hot = None
    running = getattr(request.app.state, "compiled_topology", None)
    if running is not None and getattr(request.app.state, "topology_state", None):
        from ..core.topology import structure_signature
        if structure_signature(running) == structure_signature(topo):
            running.corpus_regrades = topo.corpus_regrades
            running.questions_ref = topo.questions_ref
            running.default_loci_seed = topo.default_loci_seed
            running.default_memory_seed = topo.default_memory_seed
            combos = sum(1 for _ in topo.corpus_regrades)
            hot = (f"hot-swapped into the RUNNING topology - {combos} regrade set(s), "
                   f"no restart, no reseed. Hit Regrades to roll them.")
        else:
            hot = ("structure CHANGED (nodes/ports/flags/wiring/versions) - the running "
                   "pod no longer matches this config. Stop and Start to rebuild.")

    return {"ok": True, "active": True, "warnings": topo.warnings, "lint": lint_payload,
            "hot_swap": hot, "summary": {
        "loci": [n.name for n in topo.loci],
        "memory": [n.name for n in topo.memory],
        "corpus": [c.name for c in topo.corpus]}}


@router.get("/probeconfig")
async def get_probeconfig(request: Request):
    """Return the active ProbeConfig YAML (uploaded if present, else the shipped
    example) PLUS its compiled summary (loci/memory/corpus names + warnings) so
    the UI can show the real topology. A broken active config still returns its
    yaml + the compile errors rather than 500-ing."""
    from ..core.topology import load_probe_config, TopologyError
    text = getattr(request.app.state, "probe_config_text", None)
    if text is not None:
        source, active = "uploaded", True
    else:
        default_pc = Path(__file__).resolve().parent.parent / "ProbeConfig.yml"
        text = default_pc.read_text(encoding="utf-8") if default_pc.exists() else ""
        source, active = "shipped-example", False
    result = {"active": active, "source": source, "yaml": text}
    if text.strip():
        try:
            topo = load_probe_config(text)
            result["summary"] = {
                "loci": [n.name for n in topo.loci],
                "memory": [n.name for n in topo.memory],
                "corpus": [c.name for c in topo.corpus],
            }
            result["warnings"] = topo.warnings
        except TopologyError as e:
            result["errors"] = e.errors
            result["warnings"] = e.warnings
    return result
