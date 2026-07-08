"""
Docker routes — /docker/start, /docker/stop, /docker/status, /docker/run-eval,
/docker/config, /docker/validate.

Manages the Docker test environment lifecycle and deployment configuration.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/docker", tags=["docker"])


@router.post("/start")
async def docker_start(request: Request):
    """Compile the ProbeConfig -> emit compose -> `docker compose up` the whole
    declared topology -> health-gate every store. Compile errors surface HERE,
    before Docker runs (compassion-first: the operator sees exactly what's wrong,
    naming node/rule/fix, without a container ever starting)."""
    from ..topology import load_probe_config, TopologyError
    from ..docker_env import spin_up_topology

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
        env = spin_up_topology(topology, project_name=project, build=build,
                               image_overrides=image_overrides)
    except Exception as exc:
        logger.error("Topology spin-up failed: %s", exc)
        raise HTTPException(status_code=500, detail={"stage": "spin_up", "error": str(exc)})

    # Stash for the eval + teardown. url_of maps EVERY store -> host URL.
    request.app.state.topology_state = {
        "project_name": env.project_name, "work_dir": env.work_dir, "url_of": env.url_of,
        "loci": env.loci, "memory": env.memory, "corpus": env.corpus,
    }
    request.app.state.compiled_topology = topology

    return {"ok": True, "warnings": topology.warnings, "stores": env.url_of,
            "loci": env.loci, "memory": env.memory, "corpus": env.corpus, "logs": env.logs}


@router.post("/stop")
async def docker_stop(request: Request):
    # Topology (compose) path first.
    ts = getattr(request.app.state, "topology_state", None)
    if ts:
        from ..docker_env import compose_down
        try:
            compose_down(ts["work_dir"], ts["project_name"])
        except Exception as exc:
            logger.error("compose down failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        request.app.state.topology_state = None
        request.app.state.compiled_topology = None
        return {"ok": True, "torn_down": ts["project_name"]}
    # Legacy single-container fallback.
    ds = request.app.state.docker_state
    if not ds or not ds.get("container_id"):
        return {"ok": False, "error": "Nothing running"}
    try:
        from ..docker_env import stop_container as _stop
        _stop(ds)
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
        return {"managed": True, "mode": "topology", "project_name": ts["project_name"],
                "stores": ts["url_of"], "loci": ts["loci"], "memory": ts["memory"],
                "corpus": ts["corpus"]}
    ds = request.app.state.docker_state
    if ds and ds.get("container_id"):
        from ..docker_env import container_status
        status = container_status(
            ds.get("container_name", "seren-probe-target")
        )
        status["managed"] = True
        return status
    return {"managed": False, "running": False, "exists": False}


@router.post("/run-eval")
async def docker_run_eval(request: Request):
    try:
        from ..docker_env import launch_and_eval
        results = launch_and_eval()
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
    from ..docker_env import _discover_configs, check_docker_available
    docker_info = check_docker_available()
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

    from ..docker_env import save_config
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

    from ..docker_env import get_config_path
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
    from ..docker_env import (
        check_docker_available, validate_dockerfile, validate_compose,
        get_config_path,
    )
    docker_info = check_docker_available()

    active = getattr(request.app.state, "docker_config_active", "default")
    config_path_str = getattr(request.app.state, "docker_config_path", None)

    config_valid = True
    config_errors: list[str] = []

    if config_path_str:
        p = Path(config_path_str)
        if p.name == "docker-compose.yml":
            valid, errs = validate_compose(p)
            config_valid = valid
            config_errors = errs
        elif p.name == "Dockerfile":
            valid, errs = validate_dockerfile(p)
            config_valid = valid
            config_errors = errs
    else:
        # Fall back to the default Dockerfile location
        default_df = Path(__file__).resolve().parent.parent.parent / "Dockerfile"
        if default_df.exists():
            valid, errs = validate_dockerfile(default_df)
            config_valid = valid
            config_errors = errs
        else:
            config_valid = False
            config_errors.append("No Dockerfile found — nothing to build")

    return {
        "docker": docker_info,
        "active_config": active,
        "config_valid": config_valid,
        "config_errors": config_errors,
    }


@router.get("/config/{name}")
async def docker_config_get(name: str):
    """Retrieve the full config files for a named deployment config."""
    from ..docker_env import CONFIG_DIR
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
    """Upload + validate a ProbeConfig (YAML string or object). Compiles it —
    surfacing errors/warnings compassion-first — and, if valid, stores it as the
    ACTIVE topology that /docker/start will use. This is how people swap in their
    own eval topology to eval against their own stuff."""
    from ..topology import load_probe_config, TopologyError
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
    # Persist the raw YAML so /start re-compiles it identically.
    import yaml as _yaml
    text = pc if isinstance(pc, str) else _yaml.safe_dump(pc, sort_keys=False)
    request.app.state.probe_config_text = text
    return {"ok": True, "active": True, "warnings": topo.warnings, "summary": {
        "loci": [n.name for n in topo.loci],
        "memory": [n.name for n in topo.memory],
        "corpus": [c.name for c in topo.corpus]}}


@router.get("/probeconfig")
async def get_probeconfig(request: Request):
    """Return the active ProbeConfig YAML (uploaded if present, else the shipped
    example) PLUS its compiled summary (loci/memory/corpus names + warnings) so
    the UI can show the real topology. A broken active config still returns its
    yaml + the compile errors rather than 500-ing."""
    from ..topology import load_probe_config, TopologyError
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
