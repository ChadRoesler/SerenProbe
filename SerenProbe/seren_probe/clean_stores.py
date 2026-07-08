"""
SerenProbe — Store cleanup utility.

Wipes all persistent data stores so you can reload a fresh dataset without
reinstalling any packages.  Run this BEFORE ``python -m seren_probe.runner``
when you want a clean-slate evaluation.

Usage::

    # Default paths (/tmp/loci.db, /tmp/memory)
    python clean_stores.py

    # Custom paths
    python clean_stores.py --loci-db /custom/loci.db --memory-dir /custom/memory

    # Also clean SCC runtime overlay
    python clean_stores.py --scc-overlay /etc/serenbrain/runtime-stores.json

What gets deleted:

    Store          | What        | Why
    ───────────────|─────────────|─────────────────────────────────
    SerenLoci      | loci.db     | SQLite FTS5 + vector tables
    SerenMemory    | persist_dir | Chroma DB (short/near/long tiers)
    SCC (overlay)  | JSON file   | Runtime UI-added store config

    Everything else (package installs, config files, source code) is left
    completely untouched — you don't need to reinstall anything.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _clean_loci(db_path: str) -> bool:
    """Delete the Loci SQLite database file.  Returns True if something was
    actually removed, False if it didn't exist."""
    p = Path(db_path)
    if not p.exists():
        print(f"  [SKIP] Loci DB not found: {p}")
        return False

    # Remove the main DB and any WAL / SHM journals SQLite may have left.
    suffixes = ["", "-wal", "-shm", ".bak"]
    removed_any = False
    for sfx in suffixes:
        target = p.with_suffix(p.suffix + sfx) if sfx.startswith(".") else p.parent / f"{p.name}{sfx}"
        # For the main file and .bak we just use the path directly
        if not sfx:
            target = p
        elif sfx.startswith("."):
            target = p.with_suffix(p.suffix + sfx)
        else:
            target = p.parent / (p.stem + sfx)

        try:
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                print(f"  [DEL]  {target}")
                removed_any = True
        except Exception as e:
            print(f"  [FAIL] {target}: {e}")

    if not removed_any:
        print(f"  [SKIP] Loci DB not found: {p}")
    return removed_any


def _clean_memory(persist_dir: str) -> bool:
    """Recursively delete the Chroma persist directory.  Returns True if
    something was actually removed."""
    p = Path(persist_dir)
    if not p.exists():
        print(f"  [SKIP] Memory persist dir not found: {p}")
        return False

    try:
        if p.is_dir():
            shutil.rmtree(p)
            print(f"  [DEL]  {p}/")
        else:
            p.unlink()
            print(f"  [DEL]  {p}")
        return True
    except Exception as e:
        print(f"  [FAIL] {p}: {e}")
        return False


def _clean_scc_overlay(overlay_path: str) -> bool:
    """Delete the SCC runtime overlay JSON file.  Returns True if something
    was actually removed."""
    p = Path(overlay_path)
    if not p.exists():
        print(f"  [SKIP] SCC overlay not found: {p}")
        return False

    try:
        # Also clean any .tmp left from atomic saves
        for f in [p, p.with_suffix(p.suffix + ".tmp")]:
            if f.exists():
                f.unlink()
                print(f"  [DEL]  {f}")
        return True
    except Exception as e:
        print(f"  [FAIL] {p}: {e}")
        return False


def _clean_all(loci_db: str, memory_dir: str, scc_overlay: str | None = None):
    """Wipe every known store location."""
    print("=" * 60)
    print("SerenProbe — Store Cleanup")
    print("=" * 60)

    print("\n── SerenLoci ──────────────────────────────")
    _clean_loci(loci_db)

    print("\n── SerenMemory ────────────────────────────")
    _clean_memory(memory_dir)

    if scc_overlay:
        print("\n── SCC runtime overlay ───────────────────")
        _clean_scc_overlay(scc_overlay)

    print("\n── Summary ────────────────────────────────")
    # Re-check existence
    remaining = []
    if Path(loci_db).exists():
        remaining.append(f"  Loci DB:    {loci_db}")
    if Path(memory_dir).exists():
        remaining.append(f"  Memory dir: {memory_dir}")
    if scc_overlay and Path(scc_overlay).exists():
        remaining.append(f"  SCC overlay: {scc_overlay}")

    if remaining:
        print("  Some paths could not be removed:")
        for r in remaining:
            print(r)
        print("  (They may be recreated by a running process — close any\n"
              "   active stores first, or check permissions.)")
    else:
        print("  All stores cleaned.  Ready for fresh evaluation.")

    print("=" * 60)


def _cli():
    parser = argparse.ArgumentParser(
        description="Wipe Seren evaluation stores for a clean reload.",
    )
    parser.add_argument(
        "--loci-db",
        default="/tmp/loci.db",
        help="Path to Loci SQLite database (default: /tmp/loci.db)",
    )
    parser.add_argument(
        "--memory-dir",
        default="/tmp/memory",
        help="Path to Memory Chroma persist directory (default: /tmp/memory)",
    )
    parser.add_argument(
        "--scc-overlay",
        default=None,
        help="Path to SCC runtime-stores.json overlay (optional)",
    )
    args = parser.parse_args()

    _clean_all(
        loci_db=args.loci_db,
        memory_dir=args.memory_dir,
        scc_overlay=args.scc_overlay,
    )
    sys.exit(0)


if __name__ == "__main__":
    _cli()
