"""
seren_probe.viewer
================
Viewer dashboard for SerenProbe — delegates to the app.py pattern.

The canonical entry point is ``python -m seren_probe`` (or the ``seren-probe``
script). This module re-exports the app builder and keeps the default store
URLs so the probe tool knows where to find its evaluation targets.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Re-export the app builder so callers can get the FastAPI app directly.
from .app import create_app, APP_VERSION  # noqa: F401

# Default store URLs for probe evaluation targets.
DEFAULT_MEMORY_URL = "http://127.0.0.1:7420"
DEFAULT_LOCI_NV_URL = "http://127.0.0.1:7422"
DEFAULT_LOCI_V_URL = "http://127.0.0.1:7421"
DEFAULT_SCC_NV_URL = "http://127.0.0.1:7423"
DEFAULT_SCC_V_URL = "http://127.0.0.1:7424"
DEFAULT_CAPTURE_PATH = "/tmp/scc_capture2.json"
