"""
serenprobe.runner
=================
Main evaluation runner. Seeds stores, runs queries, computes metrics,
and prints/saves a report.

Usage::

    # Python API
    from seren_probe.runner import run_evaluation
    report = run_evaluation(
        loci_store=my_loci_store,
        memory_store=my_memory_store,
        scc_client=my_scc_client,
        output_path="eval_report.json",
    )

    # CLI
    python -m seren_probe.runner --loci-db /tmp/loci.db --memory-dir /tmp/memory

    # Export dataset JSON (no stores needed)
    python -m seren_probe.runner --export-dataset eval_dataset.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .dataset import EvalDataset, export_synthetic_dataset_json, seed_synthetic_dataset
from .evaluators import (
    CorpusCallosumEvaluator,
    LociEvaluator,
    MemoryEvaluator,
)
from .metrics import EvalMetrics


def _build_loci_store(db_path: str) -> Any:
    """Build a LociStore from a db path (embedding-free floor)."""
    from seren_loci.config import LociConfig, StorageConfig
    from seren_loci.store import LociStore
    cfg = LociConfig(storage=StorageConfig(db_path=db_path, embedding_model=None))
    return LociStore(cfg)


def _build_memory_store(persist_dir: str) -> Any:
    """Build a MemoryStore with a fake embedder (deterministic, offline)."""
    from seren_memory.config import MemoryConfig, StorageConfig
    from seren_memory.collections import MemoryStore
    from seren_memory.embedder import resolve_embedding_function

    cfg = MemoryConfig(storage=StorageConfig(
        persist_dir=persist_dir,
        embedding_model="all-MiniLM-L6-v2",
    ))
    # Use the built-in chroma default to avoid network deps
    ef = resolve_embedding_function(None)  # None -> chroma default
    store = MemoryStore(cfg, embedding_function=ef, _allow_reset=True)
    return store


def run_evaluation(
    *,
    loci_store: Optional[Any] = None,
    memory_store: Optional[Any] = None,
    scc_federation: Optional[Any] = None,
    scc_client: Optional[Any] = None,
    dataset: Optional[EvalDataset] = None,
    loci_db_path: Optional[str] = None,
    memory_dir: Optional[str] = None,
    output_path: Optional[str] = None,
    k: int = 10,
    seed: bool = True,
) -> dict[str, Any]:
    """Run a full RAG evaluation across available stores.

    Args:
        loci_store: direct LociStore instance.
        memory_store: direct MemoryStore instance.
        scc_federation: direct SCC Federation instance.
        scc_client: HTTP TestClient for SCC.
        dataset: pre-built EvalDataset. If None and seed=True, generates one.
        loci_db_path: path to create a temp Loci db (if no store given).
        memory_dir: path to create a temp Memory persist dir (if no store given).
        output_path: if set, saves JSON report to this path.
        k: evaluation depth (top-k).
        seed: if True, seed synthetic data into stores.

    Returns:
        dict with per-system metrics and aggregate results.
    """
    # Build stores if paths given
    if loci_store is None and loci_db_path:
        loci_store = _build_loci_store(loci_db_path)
    if memory_store is None and memory_dir:
        memory_store = _build_memory_store(memory_dir)

    # Generate or load dataset
    if dataset is None:
        if seed:
            dataset = seed_synthetic_dataset(
                loci_store=loci_store if seed else None,
                memory_store=memory_store if seed else None,
            )
        else:
            dataset = seed_synthetic_dataset()  # just the dataset, no seed

    report: dict[str, Any] = {
        "evaluation": {
            "name": dataset.name,
            "description": dataset.description,
            "timestamp": time.time(),
            "k": k,
            "num_queries": len(dataset.queries),
        },
        "systems": {},
    }

    # ── Loci evaluation ──────────────────────────────────────────────────
    if loci_store is not None:
        loci_queries = dataset.filter_by_source("loci")
        if loci_queries:
            print(f"\n[seren-probe] Evaluating Loci ({len(loci_queries)} queries)...")
            evaluator = LociEvaluator(store=loci_store)
            metrics = evaluator.evaluate(loci_queries, k=k)
            report["systems"]["loci"] = {
                "queries": len(loci_queries),
                "metrics": metrics.aggregate(),
                "details": metrics.snapshot(),
            }
            print(f"  Loci results: {metrics.aggregate()}")

    # ── Memory evaluation ────────────────────────────────────────────────
    if memory_store is not None:
        memory_queries = dataset.filter_by_source("memory")
        if memory_queries:
            print(f"\n[seren-probe] Evaluating Memory ({len(memory_queries)} queries)...")
            evaluator = MemoryEvaluator(store=memory_store)
            metrics = evaluator.evaluate(memory_queries, k=k)
            report["systems"]["memory"] = {
                "queries": len(memory_queries),
                "metrics": metrics.aggregate(),
                "details": metrics.snapshot(),
            }
            print(f"  Memory results: {metrics.aggregate()}")

    # ── Corpus Callosum evaluation ───────────────────────────────────────
    if scc_federation is not None or scc_client is not None:
        corpus_queries = dataset.filter_by_source("corpus")
        if corpus_queries:
            print(f"\n[seren-probe] Evaluating SCC ({len(corpus_queries)} queries)...")
            evaluator = CorpusCallosumEvaluator(
                federation=scc_federation, client=scc_client,
            )
            metrics = evaluator.evaluate(corpus_queries, k=k)
            report["systems"]["corpus_callosum"] = {
                "queries": len(corpus_queries),
                "metrics": metrics.aggregate(),
                "details": metrics.snapshot(),
            }
            print(f"  SCC results: {metrics.aggregate()}")

    # ── Cross-system aggregate ───────────────────────────────────────────
    all_metrics = EvalMetrics()
    for sys_name, sys_data in report["systems"].items():
        all_metrics.hit_rates.extend(sys_data["details"]["per_query"]["hit_rates"])
        all_metrics.mrrs.extend(sys_data["details"]["per_query"]["mrrs"])
        all_metrics.precisions.extend(sys_data["details"]["per_query"]["precisions"])
        all_metrics.recalls.extend(sys_data["details"]["per_query"]["recalls"])
        all_metrics.ndcgs.extend(sys_data["details"]["per_query"]["ndcgs"])
        all_metrics.ious.extend(sys_data["details"]["per_query"]["ious"])
        all_metrics.prec_omegas.extend(sys_data["details"]["per_query"]["prec_omegas"])
        if "docket_coverages" in sys_data["details"]["per_query"]:
            all_metrics.docket_coverages.extend(sys_data["details"]["per_query"]["docket_coverages"])
        if "docket_densities" in sys_data["details"]["per_query"]:
            all_metrics.docket_densities.extend(sys_data["details"]["per_query"]["docket_densities"])

    report["aggregate"] = all_metrics.aggregate()
    print(f"\n[seren-probe] Aggregate: {report['aggregate']}")

    # Save report
    if output_path:
        out = Path(output_path)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[seren-probe] Report saved to {out.resolve()}")

    return report


def _cli():
    """Minimal CLI for running evaluation."""
    import argparse

    parser = argparse.ArgumentParser(description="Seren RAG Evaluation Runner")
    parser.add_argument("--loci-db", help="Path to Loci SQLite database")
    parser.add_argument("--memory-dir", help="Path to Memory persist directory")
    parser.add_argument("--output", default="eval_report.json", help="Output JSON path")
    parser.add_argument("--k", type=int, default=10, help="Evaluation depth (top-k)")
    parser.add_argument("--dataset", help="Path to pre-built eval dataset JSON")
    parser.add_argument("--no-seed", action="store_true", help="Skip synthetic seeding")
    parser.add_argument("--export-dataset", help="Export synthetic dataset JSON to this path and exit")
    args = parser.parse_args()

    # Export-only mode: generate dataset JSON without live stores
    if args.export_dataset:
        export_synthetic_dataset_json(args.export_dataset)
        sys.exit(0)

    dataset = None
    if args.dataset:
        dataset = EvalDataset.load(args.dataset)

    report = run_evaluation(
        loci_db_path=args.loci_db,
        memory_dir=args.memory_dir,
        dataset=dataset,
        output_path=args.output,
        k=args.k,
        seed=not args.no_seed,
    )
    sys.exit(0)


if __name__ == "__main__":
    _cli()
