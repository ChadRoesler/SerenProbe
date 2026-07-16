"""
serenprobe.metrics
===================
Standard RAG evaluation metrics.

All metrics compare a list of retrieved (hit_id, score) tuples against
a ground-truth set of relevant document IDs.

Supported metrics:
  - HitRate@k  (HR@k)   : was any relevant doc in top-k?
  - MRR@k               : reciprocal rank of first relevant doc
  - Precision@k         : fraction of top-k that are relevant
  - Recall@k            : fraction of all relevant docs found in top-k
  - NDCG@k              : normalized discounted cumulative gain
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EvalMetrics:
    """Container for per-query and aggregate evaluation metrics."""

    # Per-query metrics (list parallel to the queries)
    hit_rates: list[float] = field(default_factory=list)
    mrrs: list[float] = field(default_factory=list)
    precisions: list[float] = field(default_factory=list)
    recalls: list[float] = field(default_factory=list)
    ndcgs: list[float] = field(default_factory=list)
    ious: list[float] = field(default_factory=list)
    prec_omegas: list[float] = field(default_factory=list)

    # Docket metrics (for SCC / docket-building evaluation)
    docket_coverages: list[float] = field(default_factory=list)
    docket_densities: list[float] = field(default_factory=list)

    # Aggregate (mean across all queries)
    def aggregate(self) -> dict[str, float]:
        n = len(self.hit_rates)
        if n == 0:
            return {}
        result = {
            "hit_rate": sum(self.hit_rates) / n,
            "mrr": sum(self.mrrs) / n,
            "precision": sum(self.precisions) / n,
            "recall": sum(self.recalls) / n,
            "ndcg": sum(self.ndcgs) / n,
            "iou": sum(self.ious) / n,
            "prec_omega": sum(self.prec_omegas) / n,
            "count": n,
        }
        # Only include docket metrics if there are actual values
        if self.docket_coverages:
            result["docket_coverage"] = sum(self.docket_coverages) / n
        if self.docket_densities:
            result["docket_density"] = sum(self.docket_densities) / n
        return result

    def snapshot(self) -> dict:
        return {
            "aggregate": self.aggregate(),
            "per_query": {
                "hit_rates": self.hit_rates,
                "mrrs": self.mrrs,
                "precisions": self.precisions,
                "recalls": self.recalls,
                "ndcgs": self.ndcgs,
                "ious": self.ious,
                "prec_omegas": self.prec_omegas,
                "docket_coverages": self.docket_coverages,
                "docket_densities": self.docket_densities,
            },
        }


# ---------------------------------------------------------------------------
#  Metric computation functions
# ---------------------------------------------------------------------------

def compute_metrics(
    retrieved: list[tuple[str, float]],     # (doc_id, score) in rank order
    relevant: set[str],                      # ground-truth relevant doc ids
    k: int = 10,
) -> dict[str, float]:
    """Compute all metrics for ONE query.

    Args:
        retrieved: list of (id, score) tuples in descending rank order.
        relevant: set of relevant document IDs.
        k: evaluation depth (top-k).

    Returns:
        {hit_rate, mrr, precision, recall, ndcg, iou, prec_omega}
    """
    top_k = retrieved[:k]
    top_ids = [doc_id for doc_id, _ in top_k]

    # Hit Rate
    hit_rate = 1.0 if any(doc_id in relevant for doc_id in top_ids) else 0.0

    # MRR@k - reciprocal rank of the first relevant doc WITHIN top-k. Capped at
    # k so it agrees with the other @k metrics: a relevant doc past rank k is a
    # miss, not long-tail credit (it used to scan the full retrieved list).
    mrr = 0.0
    for rank, (doc_id, _) in enumerate(top_k, start=1):
        if doc_id in relevant:
            mrr = 1.0 / rank
            break

    # Precision@k
    relevant_in_top = sum(1 for doc_id in top_ids if doc_id in relevant)
    precision_k = relevant_in_top / k if k > 0 else 0.0

    # Recall@k
    recall_k = relevant_in_top / len(relevant) if relevant else 0.0

    # NDCG@k
    ndcg_k = _ndcg(top_ids, relevant, k)

    # IoU@k (Intersection over Union = Jaccard similarity)
    #   |retrieved ∩ relevant| / |retrieved ∪ relevant|
    retrieved_set = set(top_ids)
    inter = len(retrieved_set & relevant)
    union = len(retrieved_set | relevant)
    iou_k = inter / union if union > 0 else 0.0

    # Precision Omega (PΩ@k) - rank-weighted precision
    #   sum_{i=1..k} rel_i * log₂(1 + 1/i) / sum_{i=1..k} log₂(1 + 1/i)
    #   Higher weight to top ranks.
    omega_weights = [math.log2(1.0 + 1.0 / (i + 1)) for i in range(k)]
    weighted_sum = 0.0
    for i, doc_id in enumerate(top_ids):
        if doc_id in relevant:
            weighted_sum += omega_weights[i] if i < k else 0.0
    omega_norm = sum(omega_weights)
    prec_omega_k = weighted_sum / omega_norm if omega_norm > 0 else 0.0

    return {
        "hit_rate": hit_rate,
        "mrr": mrr,
        "precision": precision_k,
        "recall": recall_k,
        "ndcg": ndcg_k,
        "iou": iou_k,
        "prec_omega": prec_omega_k,
    }


def compute_metrics_batch(
    queries_results: list[tuple[list[tuple[str, float]], set[str]]],
    k: int = 10,
) -> EvalMetrics:
    """Run compute_metrics across many queries.

    Args:
        queries_results: list of (retrieved_list, relevant_set) per query.
        k: evaluation depth.

    Returns:
        EvalMetrics with per-query arrays.
    """
    m = EvalMetrics()
    for retrieved, relevant in queries_results:
        res = compute_metrics(retrieved, relevant, k=k)
        m.hit_rates.append(res["hit_rate"])
        m.mrrs.append(res["mrr"])
        m.precisions.append(res["precision"])
        m.recalls.append(res["recall"])
        m.ndcgs.append(res["ndcg"])
        m.ious.append(res["iou"])
        m.prec_omegas.append(res["prec_omega"])
    return m


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

def _ndcg(top_ids: list[str], relevant: set[str], k: int) -> float:
    """NDCG@k: normalized discounted cumulative gain.

    Relevance is binary (1 if doc_id in relevant, else 0).
    """
    if not relevant:
        return 1.0  # edge case: no relevant docs -> perfect score

    dcg = 0.0
    for i, doc_id in enumerate(top_ids):
        rel = 1.0 if doc_id in relevant else 0.0
        # i is 0-based, rank = i+1
        dcg += rel / math.log2(i + 2)  # log2(rank+1)

    # Ideal DCG: all relevant docs at top
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / ideal if ideal > 0 else 0.0


# ---------------------------------------------------------------------------
#  Content-relevance grading (shared by the regrade harness + tune_scc)
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Fuzzy-match normalizer for content relevance: lowercase, collapse
    hyphens/underscores/whitespace to single spaces, strip punctuation - so
    'rate_limit', 'rate limit', and 'rate-limit' all compare equal."""
    t = (text or "").lower()
    t = re.sub(r"[-_]+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def grade_against_content(
    hits: list[dict],
    expected_content: list[str],
    expected_ids: Optional[list[str]] = None,
    k: int = 10,
) -> tuple[list[tuple[str, float]], set[str], float, float]:
    """Label a result list against a synthetic query's expected facts.

    Relevance is content-based (does a hit's `content` contain an expected
    phrase, fuzzily-normalized) so the synthetic dataset needs no hand id-
    tagging. Falls back to `expected_ids` when nothing content-matches.
    Returns (retrieved[(id, score)], relevant_ids, docket_coverage,
    docket_density):
      - coverage = fraction of expected items found ANYWHERE in hits (did the
        briefing cover the ground - SCC's actual job, not rank-1 judging)
      - density  = fraction of top-k hits carrying any expected content

    NOTE: substring matching is deliberately fuzzy (false-pos on a shared
    phrase, false-neg on paraphrase). Trust the RELATIVE numbers across
    configs; be cautious with absolutes. Mirrors tune_scc.evaluate_scc's
    inline grading - that script can adopt this to delete its copy.
    """
    expected_ids = expected_ids or []
    retrieved = [(h["id"], h.get("score", 0.0)) for h in hits]
    relevant: set[str] = set()
    items_found = 0
    n_exp = len(expected_content)
    for exp in expected_content:
        ne = normalize_text(exp)
        if not ne:
            continue
        for h in hits:
            if ne in normalize_text(h.get("content", "")):
                relevant.add(h["id"])
                items_found += 1
                break  # count each expected item at most once
    density_hits = 0
    for h in hits[:k]:
        nc = normalize_text(h.get("content", ""))
        if any(normalize_text(e) in nc for e in expected_content if normalize_text(e)):
            density_hits += 1
    # Fallback: nothing content-matched -> grade by id against expected_ids.
    if not relevant and expected_ids:
        relevant = set(expected_ids)
        topk_ids = [doc_id for doc_id, _ in retrieved[:k]]
        items_found = len(relevant & set(topk_ids))
        density_hits = sum(1 for i in topk_ids if i in relevant)
    coverage = items_found / n_exp if n_exp > 0 else 0.0
    density = density_hits / min(k, len(retrieved)) if retrieved else 0.0
    return retrieved, relevant, coverage, density
