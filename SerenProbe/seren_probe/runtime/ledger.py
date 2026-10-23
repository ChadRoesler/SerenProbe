"""
seren_probe.runtime.ledger - every run is kept, keyed, and comparable.

Until 21 Oct 2026 Probe kept ONE results file per topology, overwritten on each
run, with a staleness check against when the stores were seeded and a hash of
the question set. The fingerprint idea was right; it just did not accumulate.
So the question "did the change help" was answered by diffing two JSON files
by hand.

A run is a record, not a file that gets replaced. Each eval and each regrade
sweep lands as runs/<stamp>-<id>.json under the state dir, carrying what makes
it comparable later: the fleet fingerprint, when the stores were seeded, the
question hash, the version pins, the Probe version, how long it took, and the
results. /eval/results stays "latest" and now names its run_id.

Two runs over the same question hash compare per store (every numeric metric
the build grades) and per tag (the questions' own classes, which ride on each
drill-down row), with the movers first. Editing the questions starts a new
lineage by design: the hash is on every row.

Best-effort on the write, like save_eval_results: a finished run is never
failed because a convenience file could not be written.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .docker_env import state_dir

logger = logging.getLogger(__name__)

KINDS = ("eval", "regrade")
_SKIP_METRICS = {"k", "question_count"}        # shape, not score


def runs_dir() -> Path:
    return state_dir() / "runs"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _stamp(ts: float) -> str:
    # Microseconds, so two runs in one second still sort by time and not by id.
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def headline(results: dict) -> dict[str, dict[str, float]]:
    """Per store, every numeric metric at the top of its snapshot (hit_rate,
    recall, ndcg, docket_coverage, stale_rate... whatever this build grades),
    so the ledger does not have to know the metric list and go stale with it."""
    out: dict[str, dict[str, float]] = {}
    for name, snap in (results.get("stores") or {}).items():
        if not isinstance(snap, dict):
            continue
        out[name] = {k: float(v) for k, v in snap.items()
                     if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in _SKIP_METRICS}
    return out


def by_tag(results: dict) -> dict[str, dict[str, dict[str, float]]]:
    """Per store, per question tag: how many questions carried it and the share
    found within k. Read off the drill-down rows, which carry each question's
    tags; rows from an older build (no tags) fall into no bucket."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for name, snap in (results.get("stores") or {}).items():
        rows = (snap or {}).get("q_detail") if isinstance(snap, dict) else None
        if not isinstance(rows, list):
            continue
        acc: dict[str, list[int]] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("unscored") or row.get("has_gt") is False:
                continue
            for tag in row.get("tags") or []:
                n, hit = acc.setdefault(str(tag), [0, 0])
                acc[str(tag)] = [n + 1, hit + (1 if row.get("within_k") else 0)]
        if acc:
            out[name] = {tag: {"n": n, "within_k": round(hit / n, 4) if n else 0.0} for tag, (n, hit) in acc.items()}
    return out


def record_run(kind: str, results: dict, *, project_name: str = "", fingerprint: str = "",
               seeded_at: str = "", question_hash: str = "", config_name: Optional[str] = None,
               bundle: Optional[str] = None, versions: Optional[dict] = None,
               started_at: Optional[float] = None, note: str = "",
               knobs: Optional[dict] = None, config_edited: bool = False) -> Optional[dict]:
    """Write one run. Returns the record (results included), or None when the
    write failed: the caller logs and carries on, the numbers are already in
    the response either way."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    try:
        from .. import __version__ as probe_version
    except Exception:                                      # noqa: BLE001
        probe_version = "0.0.0+unknown"
    finished = time.time()
    started = float(started_at) if started_at else finished
    rec: dict[str, Any] = {
        "id": uuid.uuid4().hex[:8], "kind": kind,
        "started_at": _iso(started), "finished_at": _iso(finished),
        "duration_seconds": round(finished - started, 2),
        "project_name": project_name, "fingerprint": fingerprint, "seeded_at": seeded_at,
        "question_hash": question_hash, "config_name": config_name, "bundle": bundle,
        "config_edited": bool(config_edited),          # the bundled config, with its knobs turned in the editor
        "versions": dict(versions or {}), "probe_version": probe_version, "note": note,
        "knobs": knobs, "question_count": results.get("question_count"),
        "stores": sorted((results.get("stores") or {}).keys()),
        "headline": headline(results), "by_tag": by_tag(results),
        "results": results,
    }
    try:
        d = runs_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_stamp(finished)}-{rec['id']}.json"
        path.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:        # noqa: BLE001
        logger.warning("could not record the run: %s", exc)
        return None
    return rec


def import_run(rec: dict, *, origin: str = "imported", imported_from: str = "") -> bool:
    """Keep a run that was recorded elsewhere: a baseline that travelled in a
    bundle. It keeps its own id, stamp and numbers, and is marked with where it
    came from, so the receiver's own runs sit beside it and compare against it.
    False when it is already here, or is not a run."""
    if not isinstance(rec, dict) or not rec.get("id") or rec.get("kind") not in KINDS:
        return False
    rid = str(rec["id"])
    if "/" in rid or "\\" in rid or ".." in rid or _path_of(rid) is not None:
        return False
    try:
        ts = datetime.fromisoformat(str(rec.get("finished_at") or "")).timestamp()
    except ValueError:
        ts = time.time()
    rec = {**rec, "origin": origin, "imported_from": imported_from, "imported_at": _iso(time.time())}
    try:
        d = runs_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{_stamp(ts)}-{rid}.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:        # noqa: BLE001
        logger.warning("could not import run %s: %s", rid, exc)
        return False
    return True


def summary(rec: dict) -> dict:
    """A run as the list shows it: everything but the results and the tag table."""
    return {k: v for k, v in rec.items() if k not in ("results", "by_tag")}


def _files() -> list[Path]:
    d = runs_dir()
    if not d.is_dir():
        return []
    return sorted((p for p in d.glob("*.json") if p.is_file()), key=lambda p: p.name, reverse=True)


def _read(path: Path) -> Optional[dict]:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:                   # noqa: BLE001
        logger.warning("could not read run %s: %s", path.name, exc)
        return None
    return rec if isinstance(rec, dict) and rec.get("id") else None


def list_runs(*, kind: Optional[str] = None, bundle: Optional[str] = None,
              config_name: Optional[str] = None, question_hash: Optional[str] = None,
              limit: int = 100) -> list[dict]:
    """Newest first. Filters are exact matches; an unset filter matches all."""
    out = []
    for p in _files():
        rec = _read(p)
        if rec is None:
            continue
        if kind and rec.get("kind") != kind:
            continue
        if bundle and rec.get("bundle") != bundle:
            continue
        if config_name and rec.get("config_name") != config_name:
            continue
        if question_hash and rec.get("question_hash") != question_hash:
            continue
        out.append(summary(rec))
        if len(out) >= max(1, int(limit)):
            break
    return out


def _path_of(run_id: str) -> Optional[Path]:
    rid = str(run_id or "").strip()
    if not rid or "/" in rid or "\\" in rid or ".." in rid:
        return None
    for p in _files():
        if p.name.endswith(f"-{rid}.json"):
            return p
    return None


def load_run(run_id: str) -> Optional[dict]:
    p = _path_of(run_id)
    return _read(p) if p else None


def delete_run(run_id: str) -> bool:
    p = _path_of(run_id)
    if not p:
        return False
    try:
        p.unlink()
    except OSError as exc:
        logger.warning("could not delete run %s: %s", run_id, exc)
        return False
    return True


def compare_runs(a: dict, b: dict) -> dict:
    """Two runs side by side. Per store, every metric both graded, with the
    delta (b minus a); per store and tag, the within-k share; and the movers,
    largest absolute delta first. same_questions says whether the comparison
    means anything: a differing question hash is a different experiment."""
    same = bool(a.get("question_hash")) and a.get("question_hash") == b.get("question_hash")
    stores: dict[str, dict[str, dict[str, float]]] = {}
    moved: list[dict] = []
    ha, hb = a.get("headline") or headline(a.get("results") or {}), b.get("headline") or headline(b.get("results") or {})
    for name in sorted(set(ha) & set(hb)):
        row: dict[str, dict[str, float]] = {}
        for metric in sorted(set(ha[name]) & set(hb[name])):
            va, vb = ha[name][metric], hb[name][metric]
            row[metric] = {"a": va, "b": vb, "delta": round(vb - va, 4)}
            if abs(vb - va) > 1e-9:
                moved.append({"store": name, "metric": metric, "a": va, "b": vb, "delta": round(vb - va, 4)})
        stores[name] = row
    ta, tb = a.get("by_tag") or by_tag(a.get("results") or {}), b.get("by_tag") or by_tag(b.get("results") or {})
    tags: dict[str, dict[str, dict[str, float]]] = {}
    for name in sorted(set(ta) & set(tb)):
        row = {}
        for tag in sorted(set(ta[name]) & set(tb[name])):
            xa, xb = ta[name][tag], tb[name][tag]
            row[tag] = {"n_a": xa["n"], "n_b": xb["n"], "a": xa["within_k"], "b": xb["within_k"],
                        "delta": round(xb["within_k"] - xa["within_k"], 4)}
        if row:
            tags[name] = row
    moved.sort(key=lambda m: -abs(m["delta"]))
    return {"a": summary(a), "b": summary(b), "same_questions": same,
            "stores": stores, "by_tag": tags, "moved": moved[:20]}
