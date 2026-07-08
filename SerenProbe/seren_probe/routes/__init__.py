"""
seren_probe.routes
═══════════════════

Route subpackage for SerenProbe. Extracted from inline app.py into
separate route modules, matching the family pattern (Loci, Memory, SCC
all use ``routes/`` subpackages with APIRouter instances).

Each module defines a ``router`` (fastapi.APIRouter) that app.py includes
via ``app.include_router()``. The state lives on ``request.app.state``,
set by the lifespan handler.
"""
from __future__ import annotations
