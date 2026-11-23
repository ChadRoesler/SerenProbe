"""
seren_probe.core.keynames - what a key may be called, and how a pod is handed one.

Pure: names and rules, no values. runtime/keys.py holds the keys; this is the
part the topology compiler and the compose emitter need without reaching into
the runtime layer.

A ProbeConfig node asks for a key BY NAME, as an environment variable:

    Memory:
      DefaultKeys: {ANTHROPIC_API_KEY: anthropic}     # every Memory node
      MemoryConfigs:
        - Name: mem-hosted
          Keys: {OPENAI_API_KEY: openai-probe}        # this node too; its own win

The emitted docker-compose.yml carries only an interpolation, never the value:

    environment:
      ANTHROPIC_API_KEY: ${SEREN_PROBE_KEY_ANTHROPIC}

and Probe resolves that variable into the environment of the `docker compose up`
process alone, the way HF_TOKEN already travels. The file on disk, the topology
state and the ledger hold names; the value exists in Probe's key holder and in
the running container's environment (where `docker inspect` can read it, as it
can any container env: whoever has the docker socket has the box).
"""
from __future__ import annotations

import re

KEY_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")

# What a key may not be delivered as: everything SEREN_* (the services' own
# settings, and the SEREN_PROBE_KEY_* interpolations themselves), and the
# variables the emitter sets for model caches and the interpreter.
RESERVED_PREFIX = "SEREN_"
RESERVED = frozenset({"PYTHONUTF8", "HF_TOKEN", "HF_HOME", "SENTENCE_TRANSFORMERS_HOME",
                      "XDG_CACHE_HOME", "BUILDKIT_PROGRESS", "PATH", "HOME"})

INTERP_PREFIX = "SEREN_PROBE_KEY_"


def interp_var(key_name: str) -> str:
    """The compose interpolation variable that carries key `key_name`. One to one:
    '_' doubles, '-' and '.' are spelled out, so no two key names share one."""
    body = (key_name.upper().replace("_", "__").replace("-", "_DASH_").replace(".", "_DOT_"))
    return INTERP_PREFIX + body


def parse_keys(raw, where: str, errors: list[str]) -> dict[str, str]:
    """A `Keys:` or `DefaultKeys:` mapping, ENV_VAR -> key name, checked. Problems
    go into `errors` naming `where`; what is valid is kept."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        errors.append(f"{where} must be a mapping of ENV_VAR: key-name (got {type(raw).__name__}).")
        return {}
    out: dict[str, str] = {}
    for var, name in raw.items():
        if not isinstance(var, str) or not ENV_NAME_RE.match(var):
            errors.append(f"{where}: {var!r} isn't an environment variable name (A-Z, 0-9, _; not starting with a digit).")
            continue
        if var.startswith(RESERVED_PREFIX) or var in RESERVED:
            errors.append(f"{where}: {var} is set by Probe itself and can't carry a key; pick another name.")
            continue
        if not isinstance(name, str) or not KEY_NAME_RE.match(name):
            errors.append(f"{where}: {var} names key {name!r}, which isn't a key name "
                          f"(1-64 of a-z, 0-9, '.', '_', '-', as in the Keys tab).")
            continue
        out[var] = name
    return out
