"""
seren_probe.routes.bundles - the inquisition bundle (.iqz) over HTTP.

Upload is the archive as the request body, streamed to a temp file beside its
final home and validated before a byte of it is kept (runtime/bundles.py says
how). Activate makes one of a bundle's configs the active ProbeConfig through
the same door an upload uses, with the bundle's folder as the base its refs
are relative to. Delete refuses while runs name the bundle, or while it is
the active config's source, unless forced.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..runtime import bundles

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bundles", tags=["bundles"])


async def _spool(request: Request) -> Path:
    """The request body, streamed to a temp file under the datasets folder (the
    same filesystem as its final home). Capped: over the cap is a 413, an empty
    body a 400. The caller deletes it."""
    incoming = bundles.datasets_dir() / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / f"{uuid.uuid4().hex}.upload"
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > bundles.MAX_BUNDLE_BYTES:
                    raise HTTPException(status_code=413,
                                        detail=f"the bundle is over {bundles.MAX_BUNDLE_BYTES // (1024 * 1024)} MB")
                f.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="send the .iqz archive as the request body")
    return tmp


@router.get("")
async def list_bundles():
    """What is installed, newest first, with how many runs in the ledger name each."""
    rows = await run_in_threadpool(bundles.list_bundles)
    return {"count": len(rows), "bundles": rows}


@router.post("/inspect")
async def inspect_bundle(request: Request):
    """The upload's checks without the install: the manifest, each config
    compiled, every ref accounted for. `ok` is the verdict; nothing is written."""
    tmp = await _spool(request)
    try:
        return await run_in_threadpool(bundles.inspect_archive, tmp)
    finally:
        tmp.unlink(missing_ok=True)


@router.post("")
async def upload_bundle(request: Request):
    """Validate, then install. 400 with every problem when the bundle cannot be
    trusted; the same content already installed is said, not redone."""
    tmp = await _spool(request)
    try:
        rec = await run_in_threadpool(bundles.install_archive, tmp)
    except bundles.BundleError as exc:
        raise HTTPException(status_code=400, detail={"stage": "bundle", "problems": exc.problems})
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, **rec}


@router.get("/{bundle_id}")
async def get_bundle(bundle_id: str):
    rec = await run_in_threadpool(bundles.get_bundle, bundle_id, details=True)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"no bundle {bundle_id!r}")
    return rec


@router.get("/{bundle_id}/configs/{name}")
async def get_bundle_config(bundle_id: str, name: str):
    """A bundled config's YAML, with its compile summary."""
    from ..core.topology import load_probe_config, TopologyError
    found = bundles.config_text(bundle_id, name)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no config {name!r} in bundle {bundle_id!r}")
    text, entry = found
    out = {"bundle": bundle_id, "name": name, "note": entry.get("note", ""), "path": entry.get("path"), "yaml": text}
    try:
        topo = load_probe_config(text, base_dir=bundles.bundle_dir(bundle_id))
        out["summary"] = {"loci": [n.name for n in topo.loci], "memory": [n.name for n in topo.memory],
                          "corpus": [c.name for c in topo.corpus]}
        out["warnings"] = topo.warnings
    except TopologyError as e:
        out["errors"], out["warnings"] = e.errors, e.warnings
    return out


@router.get("/{bundle_id}/export")
async def export_bundle(bundle_id: str, runs: bool = True):
    """The bundle to hand on: the dataset and configs as installed, plus (runs)
    every run in the ledger that names it, as the receiver's baseline."""
    from starlette.background import BackgroundTask
    from starlette.responses import FileResponse
    if bundles.bundle_dir(bundle_id) is None:
        raise HTTPException(status_code=404, detail=f"no bundle {bundle_id!r}")
    tmp = bundles.datasets_dir() / ".incoming" / f"{uuid.uuid4().hex}.iqz"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        await run_in_threadpool(bundles.export_bundle, bundle_id, tmp, with_runs=runs)
    except (bundles.BundleError, KeyError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"could not pack {bundle_id!r}: {exc}")
    return FileResponse(str(tmp), media_type="application/gzip", filename=f"{bundle_id}.iqz",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@router.post("/{bundle_id}/activate")
async def activate_bundle_config(request: Request, bundle_id: str):
    """Make one of the bundle's configs the active ProbeConfig: body {"config":
    name}, or nothing when the bundle holds exactly one. Ends in the same
    activate an upload does, with the bundle's folder as the base for its refs."""
    from .docker import activate_probe_config
    try:
        body = await request.json()
    except Exception:
        body = {}
    rec = bundles.get_bundle(bundle_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"no bundle {bundle_id!r}")
    names = [c["name"] for c in rec["configs"]]
    name = (body.get("config") if isinstance(body, dict) else None) or (names[0] if len(names) == 1 else None)
    if not name:
        raise HTTPException(status_code=400, detail=f"say which config to activate: one of {names}")
    found = bundles.config_text(bundle_id, str(name))
    if found is None:
        raise HTTPException(status_code=404, detail=f"bundle {bundle_id!r} has no config {name!r} (it has {names})")
    text, entry = found
    source = {"bundle": bundle_id, "config_name": str(name), "base_dir": str(bundles.bundle_dir(bundle_id))}
    out = await activate_probe_config(request, text, source=source)
    return {**out, "note": entry.get("note", "")}


@router.delete("/{bundle_id}")
async def delete_bundle(request: Request, bundle_id: str, force: bool = False):
    """Refuses (409) while anything holds the bundle, naming every holder at
    once with a reason code each, so the viewer can offer the way out:

        running_pod    a pod was started from it: its Seed and Evaluate read the
                       bundle's files. Stop the pod.
        active_config  it holds the active ProbeConfig. Clear it
                       (DELETE /docker/probeconfig) or activate another.
        runs           runs in the ledger name it. They keep their numbers
                       either way; only their re-run is lost.

    force deletes anyway: the active config is cleared with it, and a running
    pod's next Evaluate fails until it is stopped (said in the note)."""
    if bundles.bundle_dir(bundle_id) is None:
        raise HTTPException(status_code=404, detail=f"no bundle {bundle_id!r}")
    src = getattr(request.app.state, "probe_config_source", None) or {}
    ts = getattr(request.app.state, "topology_state", None) or {}
    holds_pod = ts.get("bundle") == bundle_id
    holds_config = src.get("bundle") == bundle_id
    runs = bundles.run_count(bundle_id)
    problems, reasons = [], []
    if holds_pod:
        reasons.append("running_pod")
        problems.append(f"a running pod ({ts.get('project_name') or 'unnamed'}) was started from {bundle_id}: its Seed "
                        f"and Evaluate read the bundle's files. Stop it in the Docker tab first.")
    if holds_config:
        reasons.append("active_config")
        problems.append(f"{bundle_id} holds the active config ({src.get('config_name')}). Clear it, or activate "
                        f"another config.")
    if runs:
        reasons.append("runs")
        problems.append(f"{runs} run(s) in the ledger name {bundle_id}. They keep their numbers if it goes; only "
                        f"their re-run is lost.")
    if problems and not force:
        raise HTTPException(status_code=409, detail={"problems": problems, "reasons": reasons})
    try:
        out = await run_in_threadpool(bundles.delete_bundle, bundle_id, force=True)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no bundle {bundle_id!r}")
    notes = []
    if holds_config:
        from ..runtime.docker_env import save_active_config
        request.app.state.probe_config_text = None
        request.app.state.probe_config_source = None
        save_active_config(None)
        notes.append("the active config came from this bundle and was cleared with it")
    if holds_pod:
        notes.append("the running pod was started from it: its next Seed or Evaluate fails until it is stopped")
    if notes:
        out["note"] = "; ".join(notes)
    out["config_cleared"] = holds_config
    return {"ok": True, **out}
