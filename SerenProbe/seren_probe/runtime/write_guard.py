"""
seren_probe.write_guard - the interlock.
════════════════════════════════════════════════════════════════════════

SerenProbe must be STRUCTURALLY INCAPABLE of writing to a store it did not
spin up.

WHY THIS EXISTS
───────────────
This harness seeds synthetic corpora. It also, by default, knew the URLs of the
operator's REAL stores (memory on 7420, loci on 7421/7422, SCC on 7423/7424).
Those two facts in one process is a loaded gun, and it went off: a dataset
generator defaulted to live ports and wrote a synthetic corpus straight into a
real, in-use SerenMemory instance. Hours of surgery to get it back out.

The reflex fix is "delete the dangerous script." That is whack-a-mole. There
were at least three other paths to the same barrel:

  * seed_memory_only.py      - hardcoded http://localhost:7420, no flag, no guard
  * live_eval.run_live_evaluation - MEMORY_URL/LOCI_*_URL constants, seeds if empty
  * routes/eval.py           - fell back to the above whenever no topology was up
  * clean_stores.py          - shutil.rmtree() behind a --memory-dir flag

Delete all four and the fifth one gets written at 2am by someone tired.

So this is an INVARIANT, not a rule. Every mutating request in SerenProbe goes
through assert_write_allowed(), and the allowlist is populated from the running
topology's own url_of map - i.e. from the containers SerenProbe itself created.
Anything else raises. Loudly. Before the request is sent.

FAIL-CLOSED. An empty allowlist refuses EVERY write. If no topology is up,
SerenProbe cannot write to anything, anywhere. That is the correct default for a
tool whose whole job is to generate fake data.

The lesson this encodes, from the day we found nine silent measurement bugs in a
row: discipline never caught a single one of them. The interlocks caught all of
them. "Don't run the dangerous script" is a rule. "The harness cannot address a
store it did not create" is a fact about the world.

OPERATOR ESCAPE HATCH
─────────────────────
There IS a way to write to a store SerenProbe didn't create, because refusing to
give the operator any way to do a thing is a mandate, not an ethos. But it must
be DELIBERATE - hands on the surface:

    export SEREN_PROBE_WRITE_TARGETS="127.0.0.1:7420,127.0.0.1:7422"

Explicit, per-run, and it names exactly what it's unlocking. Typing that is
consent. Inheriting a default is not.
"""
from __future__ import annotations

import logging
import os
import threading
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


class WriteTargetError(RuntimeError):
    """A write was attempted against a store SerenProbe does not own.

    This is never a store problem and never a network problem. It means the code
    tried to mutate something outside the topology it created - which is either a
    bug, or a live store about to be filled with synthetic data.
    """


# POST is not a synonym for "write". /search is a POST and it is a READ; blocking
# it would break every eval. Everything ELSE with a mutating verb is treated as a
# write, so a new mutating endpoint added later is guarded by DEFAULT rather than
# forgotten. Fail-closed on the unknown.
_READ_ONLY_POSTS: frozenset[str] = frozenset({"/search"})
_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

_lock = threading.Lock()
_allowed: set[str] = set()


def _authority(url: str) -> str:
    """host:port, normalized. localhost/::1/0.0.0.0 all collapse to 127.0.0.1 so
    a guard can't be walked around by spelling the same box a different way."""
    raw = url if "//" in url else "//" + url
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host in ("localhost", "::1", "0.0.0.0", ""):
        host = "127.0.0.1"
    port = parts.port
    return f"{host}:{port}" if port else host


def _env_targets() -> set[str]:
    raw = os.environ.get("SEREN_PROBE_WRITE_TARGETS", "").strip()
    if not raw:
        return set()
    return {_authority(t.strip()) for t in raw.split(",") if t.strip()}


def allow_targets(urls) -> None:
    """Declare the stores SerenProbe owns this run. Called with the running
    topology's url_of values - the containers it spun up itself."""
    with _lock:
        _allowed.clear()
        _allowed.update(_authority(u) for u in urls if u)
    logger.debug("write_guard: %d target(s) allowed", len(_allowed))


def clear_targets() -> None:
    """Drop every allowed target. After this, NOTHING is writable."""
    with _lock:
        _allowed.clear()


def allowed_targets() -> set[str]:
    with _lock:
        return set(_allowed) | _env_targets()


def is_write(method: str, path: str) -> bool:
    if (method or "POST").upper() in _SAFE_METHODS:
        return False
    clean = (path or "/").split("?", 1)[0].rstrip("/") or "/"
    return clean not in _READ_ONLY_POSTS


def assert_write_allowed(url: str, path: str, method: str = "POST") -> None:
    """Raise WriteTargetError unless (url) is a store this run owns.

    Reads pass freely. Writes to an unowned store NEVER happen -- the request is
    refused before it leaves the process, so there is nothing to clean up after.
    """
    if not is_write(method, path):
        return
    target = _authority(url)
    ok = allowed_targets()
    if target in ok:
        return
    raise WriteTargetError(
        f"REFUSED {method.upper()} {path} -> {url}\n"
        f"  SerenProbe does not own that store. It only writes to containers it "
        f"spun up itself.\n"
        f"  owned this run: {sorted(ok) or '(nothing - no topology is running)'}\n"
        f"  This guard exists because a seeder once defaulted to a live port and "
        f"wrote a synthetic corpus into a real, in-use SerenMemory. If you truly "
        f"mean to write to this store, say so out loud:\n"
        f"      SEREN_PROBE_WRITE_TARGETS={target}"
    )
