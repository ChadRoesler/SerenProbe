"""
seren_probe.progress - a tiny thread-safe registry for "how far along is this
store" during seed/eval, so the viewer can poll live X/Y counts.

WHY A SEPARATE ENDPOINT, NOT THE /eval/seed OR /eval/run RESPONSE.
Both routes run the actual work inside run_in_threadpool -- which BLOCKS the
HTTP request until the whole thing finishes. There is no response to poll for
progress on; the response IS the finished result. So progress has to live
somewhere the request handler can read WHILE a separate, still-running request
is doing the work: process-global state, guarded by a lock, read by a cheap
GET that returns instantly regardless of what the worker threads are doing.

NOT PERSISTED. This is ephemeral UI feedback, not eval data -- it resets on
every seed/eval run (clear_all()) and means nothing once the request that
populated it has returned. Never read this to decide anything about
correctness; read /eval/results for that.

ONE OPERATION AT A TIME, BY DESIGN. Like write_guard's allowlist, this is
process-global rather than per-request, because SerenProbe's whole model is
one topology, one operation in flight. A second concurrent seed/eval would
already be racing against the FIRST one's writes -- this registry does not
need to solve a problem the rest of the app doesn't either.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
# store_name -> {"phase": "seed"|"eval", "current": int, "total": int, "done": bool}
_state: dict[str, dict] = {}


def clear_all() -> None:
    """Wipe every row. Called at the START of /eval/seed and /eval/run so a
    stale row from a previous, unrelated run can never bleed into a new one."""
    with _lock:
        _state.clear()


def start(store: str, phase: str, total: int) -> None:
    """Declare a store's total BEFORE work begins, so X/Y has a denominator
    from the very first poll instead of flickering in a few seconds late."""
    with _lock:
        _state[store] = {"phase": phase, "current": 0, "total": max(0, int(total)), "done": False}


def bump(store: str, delta: int = 1) -> None:
    """Advance one store's counter. Cheap and lock-scoped tight -- called once
    per seeded item or per question scored, potentially thousands of times."""
    with _lock:
        row = _state.get(store)
        if row is not None:
            row["current"] += delta


def finish(store: str) -> None:
    """Mark a store done regardless of how it got there (success OR failure).
    A store that raises must still stop reporting X/Y<total forever -- a
    permanently-stuck row is worse than an honest 'this one errored out'."""
    with _lock:
        row = _state.get(store)
        if row is not None:
            row["done"] = True


def snapshot() -> dict[str, dict]:
    """A plain-dict copy, safe to serialize straight to JSON."""
    with _lock:
        return {name: dict(row) for name, row in _state.items()}
