"""
seren_probe.runtime.keys - the keys Probe holds for outside models.

An answer run hands a model what a memory system retrieved and asks it the
question; a hosted model wants a key for that, and so does the judge. Probe
holds them, so a run on any box can use one without the key crossing a command
line, sitting in a shell history, or riding along in a bundle.

A key is used BY NAME, and comes from one of two places:

  seren-probe.yaml   keys: <name>: {key_env | key_keyring | key}, a pointer
                     resolved by seren_meninges.resolve_token like every bearer
                     in the family. Read-only here: change it in the yaml.
  the key file       <state_dir>/keys.json, mode 0600, written through
                     PUT /keys/<name> (the viewer's Keys box). Probe's own.

A name in the yaml wins, and the key file cannot take one: a PUT to a name the
yaml holds is refused rather than quietly shadowed, because two values for one
name is how a run spends the wrong account.

WRITE-ONLY. Nothing hands a value back. The listing gives each key's name,
where it lives, when it was set, and a fingerprint (the first 8 hex of its
sha256) so two keys can be told apart without being shown. A value is never
logged, never returned in an error, never written into a run record (a run
names its key), and never travels in a bundle: the key file sits beside
datasets/, not inside it, and bundles are built from datasets/ alone.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from seren_meninges.credentials import resolve_token
from seren_sinew import atomic

from ..core.keynames import KEY_NAME_RE as NAME_RE
from .docker_env import state_dir

MAX_VALUE = 8192
FILE_NAME = "keys.json"

_lock = threading.Lock()


class KeysError(Exception):
    """A refused key operation. `status` is the HTTP status the route answers with.
    The message never carries a key's value."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def key_file() -> Path:
    return state_dir() / FILE_NAME


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _read() -> dict[str, dict[str, Any]]:
    """The key file. Missing is empty; DAMAGED is an error, not empty: a write
    after a lenient read would replace every key held with the one being set."""
    path = key_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeysError(500, f"{FILE_NAME} in the state folder is unreadable; mend or remove it") from None
    if not isinstance(data, dict):
        raise KeysError(500, f"{FILE_NAME} in the state folder is not a key file; mend or remove it")
    return data


def _write(data: dict[str, dict[str, Any]]) -> None:
    atomic.write_json(key_file(), data, mode=0o600)


def _configured(config) -> dict:
    return dict(getattr(config, "keys", None) or {})


def _resolve_ref(ref) -> str:
    return resolve_token(inline=ref.key or None, keyring_ref=ref.key_keyring or None,
                         env_var=ref.key_env or None, default="")


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise KeysError(400, "a key name is 1-64 characters of a-z, 0-9, '.', '_' or '-', starting with a letter or digit")
    return name


def resolve(name: str, config=None) -> Optional[str]:
    """The key named `name`, or None. The yaml's pointer first, then the key file."""
    ref = _configured(config).get(name)
    if ref is not None:
        return _resolve_ref(ref) or None
    with _lock:
        row = _read().get(name)
    value = (row or {}).get("value")
    return value if isinstance(value, str) and value else None


def listing(config=None) -> list[dict[str, Any]]:
    """Every key Probe can use, without one value among them."""
    rows: list[dict[str, Any]] = []
    configured = _configured(config)
    for name, ref in sorted(configured.items()):
        value = _resolve_ref(ref)
        via, pointer = (("env", ref.key_env) if ref.key_env and not ref.key and not ref.key_keyring
                        else ("keyring", ref.key_keyring) if ref.key_keyring and not ref.key
                        else ("inline", "seren-probe.yaml") if ref.key
                        else ("none", ""))
        rows.append({"name": name, "source": "config", "via": via, "pointer": pointer, "set": bool(value),
                     "fingerprint": fingerprint(value) if value else None, "note": ref.note,
                     "editable": False})
    with _lock:
        held = _read()
    for name, row in sorted(held.items()):
        value = row.get("value") or ""
        # editable: Probe's own key can always be let go of, even when the yaml has
        # since named the same key (then it is shadowed: the yaml's is the one used)
        rows.append({"name": name, "source": "probe", "via": "key file", "pointer": FILE_NAME,
                     "set": bool(value), "fingerprint": fingerprint(value) if value else None,
                     "note": row.get("note", ""), "set_at": row.get("set_at"),
                     "editable": True, "shadowed": name in configured})
    return rows


def put(name: str, value: str, note: str = "", config=None) -> dict[str, Any]:
    """Hold `value` as the key `name`, replacing one already held. Returns its
    listing row, which carries no value."""
    check_name(name)
    if name in _configured(config):
        raise KeysError(409, f"the key {name!r} is set in seren-probe.yaml; change it there")
    if not isinstance(value, str) or not value.strip():
        raise KeysError(400, "the key is empty")
    value = value.strip()
    if len(value) > MAX_VALUE:
        raise KeysError(400, f"the key is longer than {MAX_VALUE} characters")
    if any(c in value for c in "\r\n\x00"):
        raise KeysError(400, "a key is one line")
    note = str(note or "")[:200]
    with _lock:
        data = _read()
        data[name] = {"value": value, "note": note, "set_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _write(data)
    return next(r for r in listing(config) if r["name"] == name and r["source"] == "probe")


def env_for(key_refs: dict[str, str], config=None) -> dict[str, str]:
    """The environment a `docker compose up` needs for the keys a pod names:
    interpolation variable -> value (core/keynames.py says how a pod asks). Every
    name is resolved before anything starts; one missing refuses the lot, by name."""
    env: dict[str, str] = {}
    missing: list[str] = []
    for var, name in sorted(key_refs.items()):
        value = resolve(name, config)
        if value:
            env[var] = value
        else:
            missing.append(name)
    if missing:
        raise KeysError(400, "the ProbeConfig asks for key(s) Probe doesn't hold: "
                             + ", ".join(sorted(set(missing))) + " (add them in the Keys tab)")
    return env


def delete(name: str, config=None) -> None:
    """Let go of a key Probe holds. One that lives only in the yaml is refused."""
    check_name(name)
    with _lock:
        data = _read()
        if name not in data:
            if name in _configured(config):
                raise KeysError(409, f"the key {name!r} is set in seren-probe.yaml; remove it there")
            raise KeysError(404, f"no key named {name!r} is held")
        del data[name]
        _write(data)
