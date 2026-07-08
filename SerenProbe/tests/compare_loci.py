"""
Two-stage Loci retrieval comparison  (manual bench, not a pytest test).

Stage 1 — FTS5-only (no [vector] flag):
    Loci installed without sqlite-vec.  Search is purely lexical via FTS5.

Stage 2 — Hybrid (with [vector] flag):
    Loci installed with sqlite-vec + embedding model.  Search is
    FTS5 + vector hybrid.

Both stages share the same synthetic facts and Loci queries; metrics are
printed side-by-side. This is a run-on-demand comparison, NOT part of the
pytest suite — it needs a real seren-loci install to do anything, so it lives
outside the `test_*` namespace on purpose (pytest won't collect it).

Requires: pip install seren-probe seren-loci  (and seren-loci[vector] for Stage 2)

Usage::
    python tests/compare_loci.py
"""
from __future__ import annotations

import os
import sys
import tempfile

# Absolute imports off the installed package (pip install -e .), so this runs
# from anywhere without sys.path surgery or a source-checkout assumption.
from seren_probe.dataset import LOCI_FACTS, seed_synthetic_dataset
from seren_probe.evaluators import LociEvaluator
from seren_probe.metrics import compute_metrics_batch


def _require_loci() -> None:
    """seren-loci is the store this bench builds in-process. Fail loud and kind
    with a fix hint instead of a cryptic ModuleNotFoundError deep in a helper."""
    try:
        import seren_loci  # noqa: F401
    except ImportError as exc:
        sys.exit(
            "compare_loci needs the 'seren-loci' package (it builds a real "
            "LociStore in-process). Install it to run this bench:\n"
            "    pip install seren-loci          # Stage 1 (FTS5-only)\n"
            "    pip install 'seren-loci[vector]' # Stage 2 (hybrid) too\n"
            f"(original import error: {exc})")


def _build_loci_store(db_path: str, embedding_model=None):
    """Build a LociStore with optional embedding model.

    embedding_model : callable or None
        None  -> no sqlite-vec support -> FTS5-only (Stage 1).
        callable -> sqlite-vec enabled -> hybrid (Stage 2).
    """
    from seren_loci.config import LociConfig, StorageConfig
    from seren_loci.store import LociStore
    cfg = LociConfig(storage=StorageConfig(
        db_path=db_path,
        embedding_model=embedding_model,
    ))
    return LociStore(cfg)


def _seed_facts(store):
    """Write all LOCI_FACTS into the store."""
    from seren_loci.models.schemas import FactWrite
    for project, key, value, why in LOCI_FACTS:
        store.set_fact(FactWrite(
            project=project, key=key, value=value, why=why,
        ))


def evaluate(store, queries, k=10):
    """Run Loci evaluation and return aggregate metrics."""
    evaluator = LociEvaluator(store=store)
    results = []
    for q in queries:
        retrieved, relevant = evaluator._search_one(q, k)
        results.append((retrieved, relevant))
    metrics = compute_metrics_batch(results, k=k)
    return metrics.aggregate()


def _print_metrics(label: str, metrics: dict):
    print(f"  {label}")
    print(f"    hit_rate = {metrics.get('hit_rate', 0):.3f}")
    print(f"    mrr      = {metrics.get('mrr', 0):.3f}")
    print(f"    precision= {metrics.get('precision', 0):.3f}")
    print(f"    recall   = {metrics.get('recall', 0):.3f}")
    print(f"    ndcg     = {metrics.get('ndcg', 0):.3f}")
    print()


def main():
    _require_loci()

    print("=" * 72)
    print("Loci Two-Stage Comparison")
    print("=" * 72)

    ds = seed_synthetic_dataset()
    loci_queries = ds.filter_by_source("loci")
    print(f"\nLoci queries: {len(loci_queries)}")
    print(f"Loci facts:   {len(LOCI_FACTS)}")

    # ─────────────────────────────────────────────────────────────────────
    #  Stage 1 — FTS5-only (no [vector] flag)
    # ─────────────────────────────────────────────────────────────────────
    print("\n── Stage 1: FTS5-only (no [vector] flag) ──")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_a = tmp.name
    try:
        store_a = _build_loci_store(db_a, embedding_model=None)
        _seed_facts(store_a)
        metrics_a = evaluate(store_a, loci_queries, k=10)
        _print_metrics("FTS5-only", metrics_a)
    finally:
        os.unlink(db_a)

    # ─────────────────────────────────────────────────────────────────────
    #  Stage 2 — Hybrid (with [vector] flag)
    # ─────────────────────────────────────────────────────────────────────
    print("── Stage 2: Hybrid (with [vector] flag) ──")

    try:
        from seren_loci.embedder import resolve_embedding_function
        ef = resolve_embedding_function("all-MiniLM-L6-v2")
        hybrid_available = True
    except Exception as e:
        print(f"  [UNAVAILABLE] sqlite-vec / embedder not present: {e}")
        print("  Skipping Stage 2 — install seren-loci[vector] to run.")
        hybrid_available = False

    if hybrid_available:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_b = tmp.name
        try:
            store_b = _build_loci_store(db_b, embedding_model=ef)
            _seed_facts(store_b)
            metrics_b = evaluate(store_b, loci_queries, k=10)
            _print_metrics("FTS5 + sqlite-vec hybrid", metrics_b)
        finally:
            os.unlink(db_b)

    print("=" * 72)
    print("Comparison complete.")
    print("=" * 72)


if __name__ == "__main__":
    main()
