"""
seren_probe.lint_cli
====================
Standalone reachability lint for a generated dataset - the post-generation gate.

A model-authored dataset (DeepSeek on the Spark, here) is fast and rigorous and
HALLUCINATES ground truth: it emits expectations its own corpus can't satisfy,
and questions whose answer exists but has no retrieval path. Both look identical
to a bad eval score. Run this as the LAST step of generation; a non-zero exit
means "do not ship this dataset, regenerate the flagged questions."

    python -m seren_probe.lint_cli DATASET_DIR
    python -m seren_probe.lint_cli DATASET_DIR --json      # machine-readable
    python -m seren_probe.lint_cli DATASET_DIR --strict    # unbridged also fails

Expects a directory with loci.yaml, memory.yaml, questions.yaml (and optionally
questions_hard.yaml). Exit 0 = clean, 1 = errors (or unbridged under --strict),
2 = no questions file found.

THREE VERDICTS, THREE FIXES (the whole mycelium night, in one command):
  * UNANSWERABLE  - the expected phrase is in NO document. The generator invented
                    ground truth. FIX: regenerate the question.
  * UNBRIDGED     - the phrase exists but no rail reaches it (asper-k1 makes
                    cellulASE, the feed is cellulOSE, nothing links them). FIX:
                    lay the linking fact (see the generator note below), or rewrite.
  * RAILED 2-HOP  - reachable only in 2+ passes, but a bridge doc exists. This is
                    a FAIR, INTERESTING question - exactly what hops:2 is for. Keep it.

GENERATOR NOTE - laying a rail. When you emit a question whose answer lives a hop
away from the query's vocabulary, EMIT THE LINKING FACT TOO. For a supply-chain
question (strain -> product -> feedstock -> supplier), the corpus needs a fact
that names the feedstock in terms of the strain or its product, e.g.
`strain_asper-k1_feedstock = cellulose` - so 'asper-k1' can reach 'cellulose' can
reach 'mat_cellulose_feed_supplier'. Without it the question is UNBRIDGED and no
retriever can ever answer it. The rail is the generator's job, not the engine's.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import yaml

from .linters.plan import lint_questions


def _load(path):
    with open(path, encoding="utf-8-sig") as f:
        return yaml.safe_load(f.read().replace("\r\n", "\n"))


def _lint_one(dataset_dir, qfile):
    loci = _load(os.path.join(dataset_dir, "loci.yaml")) or []
    mem = _load(os.path.join(dataset_dir, "memory.yaml")) or []
    qs = (_load(qfile) or {}).get("questions", [])
    return lint_questions(qs, loci, mem)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reachability-lint a generated dataset before shipping it.")
    ap.add_argument("dataset_dir")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true",
                    help="treat UNBRIDGED (no-rail) expectations as failures too")
    args = ap.parse_args(argv)

    qfiles = sorted(glob.glob(os.path.join(args.dataset_dir, "questions*.yaml")))
    if not qfiles:
        print(f"no questions*.yaml in {args.dataset_dir}", file=sys.stderr)
        return 2

    reports = {os.path.basename(q): _lint_one(args.dataset_dir, q) for q in qfiles}
    total_err = sum(len(r.errors) for r in reports.values())
    total_unbridged = sum(len(r.unbridged) for r in reports.values())
    total_railed = sum(len(r.multihop) for r in reports.values())

    if args.json:
        out = {qf: {"ok": r.ok, "checked": r.checked, "errors": r.errors,
                    "unbridged": [{"query": q, "expects": c, "holder": h} for q, c, h in r.unbridged],
                    "railed_hops": [{"query": q, "expects": c, "holder": h} for q, c, h in r.multihop]}
               for qf, r in reports.items()}
        print(json.dumps(out, indent=2))
    else:
        for qf, r in reports.items():
            print(f"\n=== {qf} ===")
            print(r.render())
        print(f"\n{'='*60}")
        print(f"TOTAL: {total_err} unanswerable \u00b7 {total_unbridged} unbridged (no rail) \u00b7 "
              f"{total_railed} railed 2-hop")
        if total_err:
            print("FAIL: regenerate the unanswerable questions (their answers aren't in the corpus).")
        if total_unbridged:
            print("WARN: unbridged questions need a LINKING FACT laid in the corpus, or a rewrite.")
        if not total_err and not total_unbridged:
            print("CLEAN: every question is answerable (direct or via a real rail).")

    fail = total_err > 0 or (args.strict and total_unbridged > 0)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
