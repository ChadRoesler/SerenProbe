"""
Entry point: python -m seren_probe [--config path]   (also the `seren-probe` script)

Boots the FastAPI app with uvicorn using the resolved config.
"""
from __future__ import annotations

import argparse
import sys

import uvicorn

from .app import create_app
from .config import load_config


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 regardless of OS locale."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _maybe_inject_truststore(cfg, log=print) -> None:
    """If tls.trust_system_store is on, route Python TLS through the OS trust
    store via `truststore`. MUST run before any SSLContext is created."""
    if not cfg.tls.trust_system_store:
        return
    try:
        import truststore
    except ImportError:
        log("[seren-probe] tls.trust_system_store is ON but 'truststore' isn't "
            "installed. Install the corp extra: pip install 'seren-probe[corp]' "
            "(continuing with certifi defaults).")
        return
    truststore.inject_into_ssl()
    log("[seren-probe] TLS: using OS trust store (truststore injected)")


def main() -> None:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="seren-probe",
        description="SerenProbe - RAG evaluation toolkit. Operator dashboard.")
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to seren-probe.yaml (default: ./seren-probe.yaml or "
             "$SEREN_PROBE_CONFIG, falling back to built-in defaults).")
    args = parser.parse_args()

    cfg = load_config(args.config)
    _maybe_inject_truststore(cfg)
    app = create_app(cfg)

    print(f"[seren-probe] listening on {cfg.server.host}:{cfg.server.port}")
    print(f"[seren-probe] auth: "
          f"{'enabled' if cfg.server.bearer_token else 'DISABLED (no token)'}")

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="info")


if __name__ == "__main__":
    main()
