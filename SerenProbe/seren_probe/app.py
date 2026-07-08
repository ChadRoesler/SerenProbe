"""
seren_probe.app
════════════════════════════════════════════════════════════════════════

The FastAPI application for the SerenProbe RAG evaluation suite. Wires the
operator dashboard with endpoints for running evaluations and live configuration.

ENDPOINTS:
    GET  /                     - service info + version
    GET  /health               - liveness
    GET  /viewer               - the SerenProbe operator dashboard
    GET  /eval/results         - latest full eval results
    POST /eval/run             - run full evaluation against live stores
    GET  /eval/config          - current viewer config
    POST /eval/config          - update viewer configuration
    POST /docker/start         - build + start Docker test environment
    POST /docker/stop          - stop + remove Docker environment
    GET  /docker/status        - current Docker environment status
    POST /docker/run-eval      - one-shot: spin up, eval, tear down
    GET  /docker/config        - list deployment configs + Docker availability
    POST /docker/config/save   - save a new Dockerfile/compose config
    POST /docker/config/activate - set the active deployment config
    POST /docker/validate      - validate Docker install + config syntax
    MCP  /mcp                  - MCP tools (when [mcp] extras installed)

Deliberately parallel to SerenLoci / SerenMemory: same auth posture, same
conditional-MCP-mount shape, same public-paths set.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from .config import SerenProbeConfig, load_config
from .routes import eval as eval_routes
from .routes import config as config_routes
from .routes import docker as docker_routes

from seren_meninges import get_version
from seren_meninges.auth import bearer_auth_middleware
from seren_meninges.viewer import render_from_dir
from seren_sinew.request_log import RequestLoggingMiddleware

from . import __version__ as _fallback_version
APP_VERSION = get_version("seren-probe", fallback=_fallback_version)

logger = logging.getLogger(__name__)


def create_app(config: SerenProbeConfig | None = None) -> FastAPI:
    cfg = config or load_config()
    bearer = cfg.server.resolve_bearer()

    # Shared mutable state that MCP tools also read/write.
    _mcp_state_ref: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # -- Startup --
        app.state.config = cfg
        app.state.eval_results = {}
        app.state.docker_state = {}
        app.state.docker_config_active = "default"
        app.state.docker_config_path = None
        app.state.store_config = {
            "memory_url": cfg.stores.memory_url,
            "loci_nv_url": cfg.stores.loci_nv_url,
            "loci_v_url": cfg.stores.loci_v_url,
            "scc_nv_url": cfg.stores.scc_nv_url,
            "scc_v_url": cfg.stores.scc_v_url,
            "capture_path": cfg.stores.capture_path,
        }
        # MCP state ref — a dict the MCP tools mutate, same references as the
        # route handlers read. Both see the same values.
        app.state._mcp_state_ref = _mcp_state_ref
        _mcp_state_ref["eval_results"] = app.state.eval_results

        print(f"[seren-probe] viewer ready on {cfg.server.host}:{cfg.server.port}")
        print(f"[seren-probe] stores: memory={cfg.stores.memory_url} "
              f"loci-nv={cfg.stores.loci_nv_url} loci-v={cfg.stores.loci_v_url} "
              f"scc-nv={cfg.stores.scc_nv_url} scc-v={cfg.stores.scc_v_url}")

        # -- Optional MCP server --
        try:
            from .mcp.server import mount_mcp_routes
            mcp_server = mount_mcp_routes(app)
        except ImportError as exc:
            mcp_server = None
            print(f"[seren-probe] MCP surface not available; HTTP-only mode ({exc})")
        except Exception as exc:  # noqa: BLE001
            mcp_server = None
            print(f"[seren-probe] MCP mount failed: {exc!r} — continuing without MCP")

        # Enter the MCP session manager's task group if mounted.
        async with AsyncExitStack() as _mcp_stack:
            session_manager = getattr(mcp_server, "session_manager", None)
            if session_manager is not None:
                await _mcp_stack.enter_async_context(session_manager.run())
                print("[seren-probe] MCP session manager running")
            yield

        # -- Shutdown: tear down any running Docker container --
        docker_state = app.state.docker_state
        if docker_state and docker_state.get("container_id"):
            try:
                from .docker_env import stop_container as _stop
                _stop(docker_state)
            except Exception as e:
                logger.warning("Docker teardown on shutdown: %s", e)

        print("[seren-probe] shut down")

    app = FastAPI(
        title="SerenProbe",
        description="RAG evaluation toolkit for Seren's memory architecture.",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    # -- Bearer auth --
    app.add_middleware(bearer_auth_middleware(bearer))

    # -- Request logging --
    app.add_middleware(
        RequestLoggingMiddleware,
        service_name="seren-probe",
        env_prefix="SEREN_PROBE",
    )

    viewer_dir = Path(__file__).resolve().parent / "viewer" / "ui"

    # -- Info routes --
    @app.get("/")
    async def root():
        return {
            "service": "SerenProbe",
            "version": APP_VERSION,
            "stores": 5,
        }

    @app.get("/health")
    async def health():
        return {"ok": True, "ts": time.time(), "version": APP_VERSION}

    # -- The operator dashboard viewer --
    @app.get("/viewer")
    async def viewer():
        html = render_from_dir(
            viewer_dir,
            title="SerenProbe",
            brand="Seren<b>Probe</b> · RAG Evaluation Suite",
            subtitle=f"v{APP_VERSION} · probe retrieval quality",
            accent="#f59e3b",
        )
        return HTMLResponse(html)

    # -- Route subpackage mounts --
    app.include_router(eval_routes.router)
    app.include_router(config_routes.router)
    app.include_router(docker_routes.router)

    return app
