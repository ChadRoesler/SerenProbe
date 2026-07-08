"""
Config route — /eval/config (GET/POST).

Reads/writes the viewer's store URL configuration at runtime.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .._version import __version__ as _fallback_version
from seren_meninges import get_version

APP_VERSION = get_version("seren-probe", fallback=_fallback_version)

router = APIRouter(prefix="/eval", tags=["config"])


@router.get("/config")
async def get_config(request: Request):
    scfg = request.app.state.store_config
    return {
        "version": APP_VERSION,
        "stores": 5,
        **scfg,
    }


@router.post("/config")
async def update_config(request: Request, body: dict):
    scfg = request.app.state.store_config
    for key in ("memory_url", "loci_nv_url", "loci_v_url",
                 "scc_nv_url", "scc_v_url", "capture_path"):
        if key in body:
            scfg[key] = body[key]
    return {"ok": True, "config": scfg}
