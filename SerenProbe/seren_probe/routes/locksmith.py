"""
seren_probe.routes.locksmith - the keys Probe holds for outside models, over HTTP
(/keys). Named for the locksmith because runtime/keys.py holds the stem, the
same way the bundle routes are named for the inquisition.

GET lists them without a value among them; PUT holds one under a name; DELETE
lets one go. runtime/keys.py says where they live and why nothing reads back.

The PUT body is parsed by hand, not through a pydantic model: a validation
error from FastAPI echoes the request's input in its 422, and the input here is
a key. Every refusal is a plain message that names the key, never its value.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..runtime import keys

router = APIRouter(prefix="/keys", tags=["keys"])


def _config(request: Request):
    return getattr(request.app.state, "config", None)


async def _call(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except keys.KeysError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@router.get("")
async def list_keys(request: Request):
    """Every key Probe can use: name, where it lives, set or not, a fingerprint."""
    rows = await _call(keys.listing, _config(request))
    return {"count": len(rows), "keys": rows}


@router.put("/{name}")
async def put_key(name: str, request: Request):
    """Hold a key under `name`: body {"value": "...", "note": "..."}. Replaces one
    already held under that name. Answers with its listing row, never the value."""
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="send JSON: {\"value\": \"...\", \"note\": \"...\"}") from None
    if not isinstance(body, dict) or not isinstance(body.get("value"), str):
        raise HTTPException(status_code=400, detail="send JSON: {\"value\": \"...\", \"note\": \"...\"}")
    row = await _call(keys.put, name, body["value"], str(body.get("note") or ""), _config(request))
    return {"ok": True, "key": row}


@router.delete("/{name}")
async def delete_key(name: str, request: Request):
    """Let go of a key Probe holds. One set in seren-probe.yaml is refused."""
    await _call(keys.delete, name, _config(request))
    return {"ok": True, "name": name}
