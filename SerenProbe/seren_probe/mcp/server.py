"""
seren_probe.mcp.server
══════════════════════

Wires the FastMCP server INTO the existing FastAPI app at /mcp.

Same process, same port. The MCP tools call evaluators directly - no HTTP
round-trip back to ourselves. One install, one approval surface, one set of
logs. Mounted at /mcp by default; override via SEREN_PROBE_MCP_MOUNT.

This is a near-exact sibling of seren_loci.mcp.server and
seren_memory.mcp.server - the same three transport footguns bite any
FastMCP-into-FastAPI mount, so the same three fixes apply.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def mount_mcp_routes(app: FastAPI) -> object:
    """Mount the SerenProbe MCP server onto an existing FastAPI app.

    Called from seren_probe.app at startup IF the [mcp] extras are installed
    (the import gate in app.py catches ImportError when ``mcp`` isn't available).

    Reads app.state.store_config and app.state._mcp_state_ref to wire tools to
    live state. Returns the FastMCP instance; the caller MUST enter
    ``mcp.session_manager.run()`` for the app's lifetime (the streamable-HTTP
    transport's task group lives there) - see app.py's lifespan.
    """
    # Imported here, not at module top, so an import failure of ``mcp`` bubbles
    # up to app.py's try/except (HTTP-only fallback) rather than crashing load.
    from mcp.server.fastmcp import FastMCP

    from .tools import ProbeToolImpl, register_tools

    mount_path = os.environ.get("SEREN_PROBE_MCP_MOUNT", "/mcp").rstrip("/")
    if not mount_path.startswith("/"):
        mount_path = "/" + mount_path

    store_config = getattr(app.state, "store_config", None)
    state_ref = getattr(app.state, "_mcp_state_ref", None)
    if store_config is None or state_ref is None:
        raise RuntimeError(
            "mount_mcp_routes called before app.state.store_config/_mcp_state_ref "
            "were set. Mount inside the lifespan handler."
        )

    mcp = FastMCP("seren-probe")
    impl = ProbeToolImpl(store_config=store_config, state_ref=state_ref)
    register_tools(mcp, impl)

    # -- Bug 1: the double-/mcp footgun --
    # streamable_http_app()/sse_app() serve at settings.streamable_http_path,
    # which DEFAULTS TO "/mcp". If we then mount at "/mcp", the real endpoint
    # lands at "/mcp/mcp" and "/mcp" itself 404s. Push the sub-app's own path to
    # root so mount("/mcp", ...) resolves to exactly "/mcp". hasattr-guarded for
    # older (sse-only) SDKs.
    if hasattr(mcp.settings, "streamable_http_path"):
        mcp.settings.streamable_http_path = "/"

    # -- Bug 3: DNS-rebinding host check vs cross-host LAN access --
    if hasattr(mcp.settings, "transport_security"):
        _apply_transport_security(mcp)

    asgi_app = _resolve_transport_app(mcp)
    app.mount(mount_path, asgi_app)
    logger.info("[seren-probe] MCP server mounted at %s (%d tools)",
                mount_path, _count_tools(mcp))

    # -- Bug 2: the mounted sub-app's lifespan never runs --
    # Returned so app.py's lifespan can run the session manager's task group.
    return mcp


def _apply_transport_security(mcp) -> None:
    """Configure FastMCP's DNS-rebinding host check from env, defaulting OFF
    (trusted-LAN posture)."""
    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except Exception as exc:  # noqa: BLE001
        logger.info("[seren-probe] transport_security module unavailable (%s); "
                    "leaving SDK default in place", exc)
        return

    def _split(name: str) -> list[str]:
        return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]

    allowed_hosts = _split("SEREN_PROBE_MCP_ALLOWED_HOSTS")
    allowed_origins = _split("SEREN_PROBE_MCP_ALLOWED_ORIGINS")

    if allowed_hosts or allowed_origins:
        if not allowed_origins:
            allowed_origins = [f"http://{h}" for h in allowed_hosts] + \
                              [f"https://{h}" for h in allowed_hosts]
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )
        logger.info("[seren-probe] MCP host check ON; allowed_hosts=%s",
                    allowed_hosts)
    else:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False)
        logger.info("[seren-probe] MCP host check OFF (trusted-LAN); set "
                    "SEREN_PROBE_MCP_ALLOWED_HOSTS to enable an allowlist")


def _resolve_transport_app(mcp) -> object:
    """Return an ASGI app for the MCP HTTP transport, tolerating SDK drift."""
    for attr in ("streamable_http_app", "sse_app"):
        factory = getattr(mcp, attr, None)
        if callable(factory):
            logger.info("[seren-probe] MCP transport: %s", attr)
            return factory()
    try:
        import mcp as _mcp_pkg
        version = getattr(_mcp_pkg, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        version = "unknown"
    raise RuntimeError(
        f"mcp SDK version {version} exposes neither streamable_http_app nor "
        "sse_app on FastMCP - cannot mount HTTP transport. Try "
        "`pip install -U mcp` or pin a known-good version in extras."
    )


def _count_tools(mcp) -> int:
    """Best-effort tool count for the startup log line."""
    for attr in ("_tools", "tools", "_tool_manager"):
        obj = getattr(mcp, attr, None)
        if obj is None:
            continue
        if hasattr(obj, "list_tools"):
            try:
                return len(list(obj.list_tools()))
            except Exception:  # noqa: BLE001
                continue
        if isinstance(obj, dict):
            return len(obj)
    return 0
