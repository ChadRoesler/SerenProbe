"""
seren_probe.core -- the pure layer.

Everything in here is enforced by tests/test_layering.py to be free of httpx,
Docker, and any live-store port literal. It compiles config, parses seeds,
computes metrics, and emits compose files. It never touches the network.

The folder exists for navigation. The boundary exists because a test says so.
"""
from __future__ import annotations

# Re-export every module so `from seren_probe.core.foo import bar` works and,
# via the package __init__ shim, `from seren_probe.foo import bar` keeps
# working unchanged.
from . import topology, topology_emit, seed_dataset, resolve, metrics, docket, knob_caps, lint_cli