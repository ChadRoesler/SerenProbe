"""
Entry point: python -m seren_probe [--config path]   (also the `seren-probe` script)

Boots the FastAPI app with uvicorn using the resolved config.
"""
from __future__ import annotations

import argparse
import sys

import uvicorn
from seren_meninges.exposure import enforce_server

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


def _bundle_command(args) -> int:
    """seren-probe bundle pack | inspect: the inquisition bundle from the shell,
    for the box the dataset lives on (the viewer uploads what this packs)."""
    import json
    from pathlib import Path
    from .runtime import bundles

    if args.bundle_command == "inspect":
        rep = bundles.inspect_archive(Path(args.file))
        print(json.dumps(rep, indent=1))
        return 0 if rep.get("ok") else 1
    folder = Path(args.folder)
    configs = []
    for spec in args.probe_configs:
        name, _, rest = spec.partition("=")
        path, _, note = rest.partition("::")
        configs.append({"name": name.strip(), "path": path.strip(), "note": note.strip()})
    if not configs:
        configs = bundles.discover_configs(folder)
    try:
        rec = bundles.pack_dataset(folder, Path(args.out), name=args.name or folder.resolve().name,
                                   configs=configs, note=args.note, include_all=args.all)
    except bundles.BundleError as exc:
        print("bundle refused:", file=sys.stderr)
        for p in exc.problems:
            print(f"  x {p}", file=sys.stderr)
        return 1
    print(json.dumps(rec, indent=1))
    return 0


def main() -> None:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="seren-probe",
        description="SerenProbe - RAG evaluation toolkit. Operator dashboard.")
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to seren-probe.yaml (default: ./seren-probe.yaml or "
             "$SEREN_PROBE_CONFIG, falling back to built-in defaults).")
    sub = parser.add_subparsers(dest="command")
    bundle = sub.add_parser("bundle", help="pack or inspect an inquisition bundle (.iqz): a dataset that travels whole")
    bsub = bundle.add_subparsers(dest="bundle_command", required=True)
    pack = bsub.add_parser("pack", help="build an .iqz from a folder of ProbeConfigs and the files they reference")
    pack.add_argument("folder", help="the dataset folder; refs in the configs are relative to it")
    pack.add_argument("-o", "--out", required=True, help="where to write the .iqz")
    pack.add_argument("--name", default=None, help="the bundle's name (default: the folder's name)")
    pack.add_argument("--note", default="", help="what this dataset is, for whoever installs it")
    pack.add_argument("--probe-config", dest="probe_configs", action="append", default=[],
                      metavar="NAME=PATH[::NOTE]",
                      help="a config to carry, by name (repeatable; default: every ProbeConfig*.yml at the "
                           "top of the folder, named by its suffix)")
    pack.add_argument("--all", action="store_true",
                      help="pack every file under the folder, not only the ones the configs reference")
    insp = bsub.add_parser("inspect", help="say what an .iqz holds and whether it can be trusted; installs nothing")
    insp.add_argument("file")
    args = parser.parse_args()

    if args.command == "bundle":
        sys.exit(_bundle_command(args))

    cfg = load_config(args.config)
    # FIRST: an open bind with no token is refused here, at zero cost, with the
    # three ways out printed.
    enforce_server(cfg.server, service="seren-probe", env_prefix="SEREN_PROBE")
    _maybe_inject_truststore(cfg)
    app = create_app(cfg)

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="info")


if __name__ == "__main__":
    main()
