"""
seren_probe.mcp
═══════════════

Optional MCP server surface for SerenProbe. Only meaningful when the [mcp]
extras are installed (``pip install seren-probe[mcp]``); without those deps,
this subpackage's modules fail to import and app.py's mount-attempt silently
no-ops, leaving SerenProbe in pure-HTTP mode.

The MCP tools call the evaluators directly (not via HTTP round-trip to
ourselves) since we're mounted INTO the same FastAPI app that owns the
evaluation state. Less wire, less latency, fewer failure modes.
"""
from __future__ import annotations
