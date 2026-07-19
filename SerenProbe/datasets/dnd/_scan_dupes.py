#!/usr/bin/env python3
"""
_scan_dupes.py -- find EVERY duplicate id in the dnd dataset in ONE pass.

WHY THIS EXISTS
---------------
resolve_plan loads store-by-store and raises on the FIRST store whose seed is
invalid. With 66 seeded stores, a dataset generated with RANDOM note ids gives you
fix -> rerun -> next collision -> fix -> rerun, one at a time, for as long as the
birthday paradox feels like it. That is the exact whack-a-mole SeedError exists to
prevent; it just prevents it one FILE at a time.

So: read every file, report every collision, and -- the bit that actually decides
how you fix them -- say which collisions the QUESTIONS depend on.

A duplicate loci key is not a lint nit. `set_fact` SUPERSEDES: the second write
wins and the first value is silently gone. It is in the yaml. It is not in the
store. Anything expecting it scores zero, and nothing in the harness can tell you
why. Same shape as the mycelium catch, one layer earlier.

Usage:  python datasets/dnd/_scan_dupes.py [dataset_dir]
Exit:   0 = clean, 1 = collisions found
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml


def _load(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("items", data.get("questions", []))
    return data if isinstance(data, list) else []


def main(root: Path) -> int:
    entities = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_"))

    # ident -> [(value, index)]  per file
    loci_dupes: dict[Path, dict[str, list]] = {}
    mem_dupes: dict[Path, dict[str, list]] = {}
    # every ident that collides anywhere -> the file it lives in
    colliding: dict[str, Path] = {}

    for ent in entities:
        lf = ent / "loci.yaml"
        if lf.exists():
            seen = defaultdict(list)
            for i, it in enumerate(_load(lf)):
                if not isinstance(it, dict):
                    continue
                ident = f"{it.get('project', '*')}/{it.get('key', '')}"
                seen[ident].append((it.get("value", ""), i))
            dups = {k: v for k, v in seen.items() if len(v) > 1}
            if dups:
                loci_dupes[lf] = dups
                for k in dups:
                    colliding[k] = lf
                    colliding[k.split("/", 1)[-1]] = lf   # bare key, for question matching

        mf = ent / "memory.yaml"
        if mf.exists():
            seen = defaultdict(list)
            for i, it in enumerate(_load(mf)):
                if not isinstance(it, dict):
                    continue
                ref = it.get("ref")
                if ref:
                    seen[str(ref)].append((it.get("content") or it.get("intent") or "", i))
            dups = {k: v for k, v in seen.items() if len(v) > 1}
            if dups:
                mem_dupes[mf] = dups
                for k in dups:
                    colliding[k] = mf

    # Which collisions does the GROUND TRUTH actually lean on? Those are the ones you
    # cannot fix by renumbering blindly -- renumber the key a question points at and you
    # have traded a silent supersede for a silent miss.
    load_bearing: dict[str, list] = defaultdict(list)
    for qf in sorted(root.glob("*/questions.yaml")) + sorted(root.glob("cross/*_questions.yaml")):
        for i, q in enumerate(_load(qf)):
            if not isinstance(q, dict):
                continue
            for field in ("expect_key", "expect_ref"):
                for want in (q.get(field) or []):
                    w = str(want)
                    if w in colliding or w.split("/", 1)[-1] in colliding:
                        load_bearing[w].append(f"{qf.relative_to(root)}[{i}] {field}")

    total = sum(len(d) for d in loci_dupes.values()) + sum(len(d) for d in mem_dupes.values())

    print("=" * 78)
    print(f"scanned {len(entities)} entities under {root}")
    print("=" * 78)

    for f, dups in sorted(loci_dupes.items()):
        print(f"\nLOCI  {f.relative_to(root)}")
        for ident, hits in sorted(dups.items()):
            print(f"  x {ident}  ({len(hits)}x -- only the LAST survives set_fact)")
            for val, i in hits:
                print(f"      [{i:>4}] {val!r}")

    for f, dups in sorted(mem_dupes.items()):
        print(f"\nMEMORY  {f.relative_to(root)}")
        for ref, hits in sorted(dups.items()):
            print(f"  x ref {ref}  ({len(hits)}x -- ref_to_id binds only ONE row)")
            for val, i in hits:
                print(f"      [{i:>4}] {str(val)[:70]!r}")

    if load_bearing:
        print("\n" + "!" * 78)
        print("LOAD-BEARING collisions -- the ground truth POINTS AT these:")
        for ident, where in sorted(load_bearing.items()):
            print(f"  ! {ident}")
            for w in where:
                print(f"      <- {w}")
        print("Renumbering one of these blindly trades a silent SUPERSEDE for a silent")
        print("MISS. Decide which value the question meant, keep THAT id, renumber the other.")
        print("!" * 78)

    print(f"\n{total} colliding id(s); {len(load_bearing)} of them load-bearing.")
    if not total:
        print("clean.")
    return 1 if total else 0


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    sys.exit(main(root))
