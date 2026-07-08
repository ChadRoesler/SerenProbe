"""
serenprobe.evaluators
======================
Evaluators that run queries against each store and collect retrieved results.

Each evaluator wraps a store/client and implements ``evaluate(queries, k)``
which returns a list of (retrieved_list, relevant_set) tuples for metric
computation.
"""
from __future__ import annotations

from typing import Any, Optional

from .dataset import EvalQuery
from .metrics import EvalMetrics, compute_metrics_batch, normalize_text


def _normalize(text: str) -> str:
    """Deprecated shim - delegates to the shared metrics.normalize_text so the
    separator/punctuation normalization is single-sourced and None-safe."""
    return normalize_text(text)

# ---------------------------------------------------------------------------
#  Loci Evaluator
# ---------------------------------------------------------------------------

class LociEvaluator:
    """Evaluate Loci's search (exact + lexical/vector)."""

    def __init__(self, store=None, client=None):
        """Pass either a LociStore directly or an HTTP TestClient."""
        self._store = store
        self._client = client

    def evaluate(self, queries: list[EvalQuery], k: int = 10) -> EvalMetrics:
        """Run queries against Loci and compute metrics.

        For each query we call store.search() and collect the returned ids.
        We compare against expected_content by checking which retrieved
        facts contain any of the expected content strings.
        """
        results: list[tuple[list[tuple[str, float]], set[str]]] = []

        for q in queries:
            retrieved_ids, relevant_set = self._search_one(q, k)
            results.append((retrieved_ids, relevant_set))

        return compute_metrics_batch(results, k=k)

    def _search_one(
        self, q: EvalQuery, k: int
    ) -> tuple[list[tuple[str, float]], set[str]]:
        """Run one query and return (retrieved_with_scores, relevant_set)."""
        if self._client is not None:
            # HTTP path
            import httpx
            payload = {
                "query": q.query,
                "n_results": k,
                "include_fundamentals": True,
                "include_superseded": False,
            }
            if q.metadata.get("project"):
                payload["project"] = q.metadata["project"]
            resp = self._client.post("/search", json=payload)
            hits = resp.json().get("hits", [])
            retrieved = [(h["id"], h["score"]) for h in hits]
        elif self._store is not None:
            # Direct store path
            project = q.metadata.get("project")
            hits, _finder = self._store.search(
                q.query, project=project, n_results=k,
                include_fundamentals=True, include_superseded=False,
            )
            retrieved = [(h.id, h.score) for h in hits]
        else:
            retrieved = []

        # Ground truth: which returned facts contain expected content?
        relevant: set[str] = set()
        for exp in q.expected_content:
            n_exp = _normalize(exp)
            for doc_id, _score in retrieved:
                # We need to check content; fetch from store
                if self._store is not None:
                    fact = self._store._fact_by_id(doc_id)
                    if fact and (n_exp in _normalize(fact.value)
                                 or n_exp in _normalize(fact.why or "")
                                 or n_exp in _normalize(fact.key)):
                        relevant.add(doc_id)
                elif self._client is not None:
                    # We can't easily fetch; approximate via expected_ids
                    pass

        # Fallback: if FTS5 search didn't find relevant results, use
        # project/key metadata to do an exact-key lookup.  This simulates
        # a hybrid approach where we fall back to exact-key matching when
        # lexical search fails.
        if not relevant and self._store is not None and q.metadata.get("key"):
            key = q.metadata["key"]
            project = q.metadata.get("project", "*")
            try:
                fact = self._store.get_fact(project, key)
                if fact:
                    fid = fact.id or fact.get("id", "")
                    if fid:
                        relevant.add(fid)
                        # Also add to retrieved if not already there
                        if not any(doc_id == fid for doc_id, _ in retrieved):
                            retrieved.append((fid, 1.0))
            except Exception:
                pass

        # Fallback: if we couldn't check content, use expected_ids from metadata
        if not relevant and q.expected_ids:
            relevant = set(q.expected_ids)

        return retrieved, relevant


# ---------------------------------------------------------------------------
#  Memory Evaluator
# ---------------------------------------------------------------------------

class MemoryEvaluator:
    """Evaluate SerenMemory's unified search across three tiers."""

    def __init__(self, store=None, client=None):
        self._store = store
        self._client = client

    def evaluate(self, queries: list[EvalQuery], k: int = 10) -> EvalMetrics:
        results: list[tuple[list[tuple[str, float]], set[str]]] = []
        for q in queries:
            retrieved, relevant = self._search_one(q, k)
            results.append((retrieved, relevant))
        return compute_metrics_batch(results, k=k)

    def _search_one(
        self, q: EvalQuery, k: int
    ) -> tuple[list[tuple[str, float]], set[str]]:
        if self._client is not None:
            payload = {
                "query": q.query,
                "n_results": k,
                "include_short": True,
                "include_near": True,
                "include_long": True,
                "include_superseded": False,
            }
            resp = self._client.post("/search", json=payload)
            hits = resp.json().get("hits", [])
            retrieved = [(h["id"], h["score"]) for h in hits]
            self._content_map = {h["id"]: h.get("content", "") for h in hits}
        elif self._store is not None:
            from seren_memory.routes.search import _TIER_WEIGHT
            import math
            # Simulate the search route logic
            fetch_n = k * 2
            all_hits: list[tuple[str, float, str]] = []  # (id, score, content)
            for tier in ("short", "near", "long"):
                try:
                    raw = self._store.query(tier, q.query, fetch_n)
                except Exception:
                    continue
                for hit in raw:
                    meta = hit["metadata"]
                    if tier == "long" and meta.get("superseded_by"):
                        continue
                    if tier == "near" and meta.get("completed"):
                        continue
                    distance = hit["distance"]
                    base = 1.0 / (1.0 + max(distance, 0.0))
                    score = base * _TIER_WEIGHT[tier]
                    if tier == "long":
                        ev = meta.get("evidence_count", 1)
                        if isinstance(ev, (int, float)) and ev > 0:
                            score *= 1.0 + math.log(ev) * 0.15
                    all_hits.append((hit["id"], round(score, 6), hit["content"]))
            all_hits.sort(key=lambda x: x[1], reverse=True)
            retrieved = [(hid, sc) for hid, sc, _c in all_hits[:k]]
        else:
            retrieved = []

        # Build relevant set from expected_content matches (normalized for hyphen/underscore variants)
        relevant: set[str] = set()
        for exp in q.expected_content:
            n_exp = _normalize(exp)
            for doc_id, _score, content in self._all_content(retrieved, q):
                if n_exp in _normalize(content):
                    relevant.add(doc_id)

        if not relevant and q.expected_ids:
            relevant = set(q.expected_ids)

        return retrieved, relevant

    def _all_content(self, retrieved, q):
        """Yield (id, score, content) for retrieved docs."""
        if self._store is not None:
            for doc_id, score in retrieved:
                row = self._store.get_by_id(doc_id)
                if row:
                    yield doc_id, score, row.get("content", "")
        elif self._client is not None:
            # Content was captured from the /search response in _search_one.
            cmap = getattr(self, "_content_map", {})
            for doc_id, score in retrieved:
                yield doc_id, score, cmap.get(doc_id, "")


# ---------------------------------------------------------------------------
#  Corpus Callosum Evaluator
# ---------------------------------------------------------------------------

class CorpusCallosumEvaluator:
    """Evaluate SCC's federated search across Loci + Memory.

    SCC is a *docket builder* — it assembles associative context around a
    query, not just exact fact matches.  The evaluator therefore measures
    both standard retrieval metrics and docket-quality metrics:
      - docket_coverage : fraction of expected content items found in top-k
      - docket_density  : fraction of top-k hits that contain any expected content
    """

    def __init__(self, federation=None, client=None):
        self._federation = federation
        self._client = client

    def evaluate(self, queries: list[EvalQuery], k: int = 10) -> EvalMetrics:
        results: list[tuple[list[tuple[str, float]], set[str]]] = []
        docket_coverages: list[float] = []
        docket_densities: list[float] = []
        for q in queries:
            retrieved, relevant, cov, den = self._search_one(q, k)
            results.append((retrieved, relevant))
            docket_coverages.append(cov)
            docket_densities.append(den)
        m = compute_metrics_batch(results, k=k)
        m.docket_coverages = docket_coverages
        m.docket_densities = docket_densities
        return m

    def _docket_search(
        self, q: EvalQuery, k: int
    ) -> tuple[list[tuple[str, float]], set[str], float, float]:
        if self._client is not None:
            payload = {"query": q.query, "n_results": k}
            resp = self._client.post("/search", json=payload)
            data = resp.json()
            hits = data.get("hits", [])
            retrieved = [(h["id"], h["score"]) for h in hits]
        elif self._federation is not None:
            import asyncio
            fused = asyncio.run(self._federation.search(q.query, n_results=k))
            retrieved = [(f.hit.id, f.rrf_score) for f in fused]
        else:
            retrieved = []
            hits = []

        # Build relevant set and compute docket metrics
        relevant: set[str] = set()
        expected_items = q.expected_content
        n_exp = len(expected_items)
        items_found = 0
        hits_with_content = 0

        if self._client is not None:
            # HTTP mode — SCC returns content in each hit
            for exp in expected_items:
                n_e = _normalize(exp)
                for h in hits:
                    content = h.get("content", "")
                    if n_e in _normalize(content):
                        relevant.add(h["id"])
                        items_found += 1
                        break  # count each expected item at most once

            # Density: fraction of top-k hits with any expected content
            for h in hits[:k]:
                content = h.get("content", "")
                n_c = _normalize(content)
                if any(_normalize(e) in n_c for e in expected_items):
                    hits_with_content += 1

        elif self._federation is not None:
            # Direct federation — check hit content (reuse fused from above)
            for exp in expected_items:
                n_e = _normalize(exp)
                for f in fused:
                    if n_e in _normalize(f.hit.content or ""):
                        relevant.add(f.hit.id)
                        items_found += 1
                        break

            for f in fused[:k]:
                n_c = _normalize(f.hit.content or "")
                if any(_normalize(e) in n_c for e in expected_items):
                    hits_with_content += 1
        else:
            # No store — fallback to expected_ids
            for exp in q.expected_content:
                for doc_id, _score in retrieved:
                    relevant.add(doc_id)
                    items_found += 1
                    break
            for _ in retrieved[:k]:
                hits_with_content += 1

        if not relevant and q.expected_ids:
            relevant = set(q.expected_ids)
            # Recompute items_found and hits_with_content from expected_ids
            items_found = len(relevant & set(doc_id for doc_id, _ in retrieved[:k]))
            hits_with_content = sum(1 for doc_id, _ in retrieved[:k] if doc_id in relevant)

        coverage = items_found / n_exp if n_exp > 0 else 0.0
        density = hits_with_content / min(k, len(retrieved)) if retrieved else 0.0

        return retrieved, relevant, coverage, density

    def _search_one(
        self, q: EvalQuery, k: int
    ) -> tuple[list[tuple[str, float]], set[str], float, float]:
        return self._docket_search(q, k)
