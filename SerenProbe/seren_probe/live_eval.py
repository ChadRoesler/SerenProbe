"""
Live evaluation against running HTTP services.

Services (expected running):
  Memory:          http://localhost:7420
  Loci (no-vector): http://localhost:7422
  Loci (vector):    http://localhost:7421
  SCC (loci-no-vec): http://localhost:7423
  SCC (loci-vec):   http://localhost:7424

Seeds all stores with synthetic data, then runs evaluation queries
via HTTP, computes metrics, and prints a report.
"""
from __future__ import annotations

import sys
import json
import time

import httpx
from .dataset import (
    LOCI_FACTS,
    MEMORY_SHORT,
    MEMORY_NEAR,
    MEMORY_LONG,
    seed_synthetic_dataset,
)

# Build a combined MEMORY_FACTS list with tier annotations
MEMORY_FACTS: list[dict] = []
for sf in MEMORY_SHORT:
    MEMORY_FACTS.append({**sf, "tier": "short"})
for nf in MEMORY_NEAR:
    MEMORY_FACTS.append({**nf, "tier": "near"})
for lf in MEMORY_LONG:
    MEMORY_FACTS.append({**lf, "tier": "long"})
from .metrics import compute_metrics_batch, normalize_text


# ── Service URLs ──────────────────────────────────────────────────────────
MEMORY_URL      = "http://localhost:7420"
LOCI_NO_VEC_URL = "http://localhost:7422"
LOCI_VEC_URL    = "http://localhost:7421"
SCC_NO_VEC_URL  = "http://localhost:7423"
SCC_VEC_URL     = "http://localhost:7424"


# ── Helpers ───────────────────────────────────────────────────────────────

def post(url: str, path: str, body: dict) -> dict:
    resp = httpx.post(f"{url}{path}", json=body, timeout=30.0)
    resp.raise_for_status()
    return resp.json() if resp.content else {}

def get(url: str, path: str) -> dict:
    resp = httpx.get(f"{url}{path}", timeout=30.0)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


# ── Seed Loci ─────────────────────────────────────────────────────────────

def seed_loci(url: str):
    """POST /fact for each LOCI_FACT."""
    print(f"\n  Seeding Loci ({url})...")
    count = 0
    for project, key, value, why in LOCI_FACTS:
        post(url, "/fact", {
            "project": project,
            "key": key,
            "value": value,
            "why": why,
        })
        count += 1
    print(f"  Seeded {count} facts.")
    # Verify
    c = get(url, "/counts")
    print(f"  Counts: {c}")


# ── Seed Memory ───────────────────────────────────────────────────────────

def seed_memory(url: str):
    """POST /short, /near, and promote short→long for MEMORY_FACTS."""
    print(f"\n  Seeding Memory ({url})...")

    # Classify facts by tier
    short_facts = [(f["content"], f.get("topic", ""), f["tier"])
                    for f in MEMORY_FACTS if f["tier"] == "short"]
    near_facts  = [(f["intent"], f.get("topic", ""))
                    for f in MEMORY_FACTS if f["tier"] == "near"]
    long_facts  = [f for f in MEMORY_FACTS if f["tier"] == "long"]

    # Short-term
    short_ids: list[str] = []
    for content, topic, _tier in short_facts:
        body = {"content": content}
        if topic:
            body["topic"] = topic
        resp = post(url, "/short", body)
        short_ids.append(resp.get("id", ""))
    print(f"  Seeded {len(short_ids)} short-term entries.")

    # Near-term
    near_ids: list[str] = []
    for intent, topic in near_facts:
        body = {"intent": intent}
        if topic:
            body["topic"] = topic
        resp = post(url, "/near", body)
        near_ids.append(resp.get("id", ""))
    print(f"  Seeded {len(near_ids)} near-term entries.")

    # Long-term: promote short entries to long, then delete the short copy
    # so the short-term pool stays at its expected size (50) and the
    # consolidator doesn't over-create long entries from these stubs.
    long_seeded = 0
    for i, lf in enumerate(long_facts):
        body = {"content": lf["content"]}
        if lf.get("topic"):
            body["topic"] = lf["topic"]
        resp = post(url, "/short", body)
        sid = resp.get("id", "")
        if sid:
            # Promote to long
            post(url, f"/short/{sid}/promote", {})
            # Delete the short copy so it doesn't inflate the short pool
            httpx.delete(f"{url}/short/{sid}", timeout=10.0)
            long_seeded += 1
    print(f"  Seeded {long_seeded} long-term entries (via short->promote, short copies cleaned).")

    # Verify
    c = get(url, "/health")
    print(f"  Health: {c}")


# ── Seed SCC ──────────────────────────────────────────────────────────────

def seed_scc(scc_url: str, loci_url: str, memory_url: str):
    """POST /stores to register backends."""
    print(f"\n  Configuring SCC ({scc_url})...")
    # Remove existing stores first
    stores = get(scc_url, "/stores")
    for s in stores.get("stores", []):
        name = s.get("name", "")
        if name:
            httpx.delete(f"{scc_url}/stores/{name}", timeout=10.0)

    # Add Loci backend
    post(scc_url, "/stores", {
        "name": "loci",
        "type": "seren_loci",
        "url": loci_url,
        "weight": 1.0,
        "floor": 0.1,
    })
    # Add Memory backend
    post(scc_url, "/stores", {
        "name": "memory",
        "type": "seren_memory",
        "url": memory_url,
        "weight": 1.0,
        "floor": 0.1,
    })
    print(f"  Registered backends: loci({loci_url}), memory({memory_url})")
    stores = get(scc_url, "/stores")
    print(f"  Stores: {stores.get('stores', [])}")


# ── Run queries ───────────────────────────────────────────────────────────

def run_loci_queries(url: str, queries: list, k: int = 10):
    """POST /search for each query, return (retrieved_list, relevant_set)."""
    results = []
    for q in queries:
        payload = {
            "query": q.query,
            "n_results": k,
            "include_fundamentals": True,
            "include_superseded": False,
        }
        if q.metadata.get("project"):
            payload["project"] = q.metadata["project"]
        resp = post(url, "/search", payload)
        hits = resp.get("hits", [])
        retrieved = [(h["id"], h["score"]) for h in hits]

        # Verify content by fetching each hit
        relevant: set[str] = set()
        for exp in q.expected_content:
            for h in hits:
                hid = h["id"]
                # Fetch fact details via GET /fact
                try:
                    fact_resp = httpx.get(
                        f"{url}/fact",
                        params={"project": q.metadata.get("project", "*"),
                                "key": q.metadata.get("key", "")},
                        timeout=10.0,
                    )
                    if fact_resp.status_code == 200:
                        fact_data = fact_resp.json()
                        value = fact_data.get("value", "")
                        why = fact_data.get("why", "")
                        key = fact_data.get("key", "")
                        n_exp = normalize_text(exp)
                        if (n_exp in normalize_text(value)
                            or n_exp in normalize_text(why)
                            or n_exp in normalize_text(key)):
                            relevant.add(hid)
                except Exception:
                    pass

        # Fallback: exact-key lookup via GET /fact
        if not relevant and q.metadata.get("key"):
            try:
                fact_resp = httpx.get(
                    f"{url}/fact",
                    params={"project": q.metadata.get("project", "*"),
                            "key": q.metadata["key"]},
                    timeout=10.0,
                )
                if fact_resp.status_code == 200:
                    fact_data = fact_resp.json()
                    fid = fact_data.get("id", "")
                    if fid:
                        relevant.add(fid)
                        if not any(doc_id == fid for doc_id, _ in retrieved):
                            retrieved.append((fid, 1.0))
            except Exception:
                pass

        results.append((retrieved, relevant))
    return results


def run_memory_queries(url: str, queries: list, k: int = 10):
    """POST /search for Memory queries."""
    results = []
    for q in queries:
        payload = {
            "query": q.query,
            "n_results": k,
            "include_short": True,
            "include_near": True,
            "include_long": True,
            "include_superseded": False,
        }
        resp = post(url, "/search", payload)
        hits = resp.get("hits", [])
        retrieved = [(h["id"], h["score"]) for h in hits]

        # Check content from hit content field (normalized for hyphen/underscore variants)
        relevant: set[str] = set()
        for exp in q.expected_content:
            n_exp = normalize_text(exp)
            for h in hits:
                content = h.get("content", "")
                if n_exp in normalize_text(content):
                    relevant.add(h["id"])

        if not relevant and q.expected_ids:
            relevant = set(q.expected_ids)

        results.append((retrieved, relevant))
    return results


def run_scc_queries(url: str, queries: list, k: int = 10):
    """POST /search for SCC queries (docket-building evaluation).

    Returns (results, docket_coverages, docket_densities).
    """
    results: list[tuple[list[tuple[str, float]], set[str]]] = []
    docket_coverages: list[float] = []
    docket_densities: list[float] = []
    for q in queries:
        payload = {"query": q.query, "n_results": k}
        resp = post(url, "/search", payload)
        hits = resp.get("hits", [])
        retrieved = [(h["id"], h["score"]) for h in hits]

        # SCC hits include content from each store
        relevant: set[str] = set()
        expected_items = q.expected_content
        n_exp = len(expected_items)
        items_found = 0
        hits_with_content = 0

        for exp in expected_items:
            ne = normalize_text(exp)
            if not ne:
                continue
            for h in hits:
                content = h.get("content", "")
                if ne in normalize_text(content):
                    relevant.add(h["id"])
                    items_found += 1
                    break  # count each expected item at most once

        # Density: fraction of top-k hits with any expected content
        for h in hits[:k]:
            content = h.get("content", "")
            if any(normalize_text(e) and normalize_text(e) in normalize_text(content)
                   for e in expected_items):
                hits_with_content += 1

        if not relevant and q.expected_ids:
            relevant = set(q.expected_ids)
            items_found = len(relevant & set(doc_id for doc_id, _ in retrieved[:k]))
            hits_with_content = sum(1 for doc_id, _ in retrieved[:k] if doc_id in relevant)

        results.append((retrieved, relevant))
        docket_coverages.append(items_found / n_exp if n_exp > 0 else 0.0)
        docket_densities.append(hits_with_content / min(k, len(retrieved)) if retrieved else 0.0)
    return results, docket_coverages, docket_densities


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("Seren RAG Live Evaluation")
    print("=" * 72)

    ds = seed_synthetic_dataset()
    loci_queries  = ds.filter_by_source("loci")
    memory_queries = ds.filter_by_source("memory")
    corpus_queries = ds.filter_by_source("corpus")
    print(f"\nDataset: {len(ds.queries)} queries total")
    print(f"  Loci:   {len(loci_queries)}")
    print(f"  Memory: {len(memory_queries)}")
    print(f"  Corpus: {len(corpus_queries)}")

    # ── Seed all stores (skip if already seeded) ─────────────────────────
    print("\n── Seeding Stores ──")

    loci_nv_counts = get(LOCI_NO_VEC_URL, "/counts")
    if loci_nv_counts.get("live", 0) == 0:
        seed_loci(LOCI_NO_VEC_URL)
    else:
        print(f"  Loci (no-vector) already seeded: {loci_nv_counts.get('live', 0)} facts")

    loci_v_counts = get(LOCI_VEC_URL, "/counts")
    if loci_v_counts.get("live", 0) == 0:
        seed_loci(LOCI_VEC_URL)
    else:
        print(f"  Loci (vector) already seeded: {loci_v_counts.get('live', 0)} facts")

    mem_health = get(MEMORY_URL, "/health")
    # Only count actual memory tiers (short, near, long), exclude metadata
    # counters like runs, pruned, briefs, drafts.
    tiers = mem_health.get("tiers", {})
    total_mem = tiers.get("short", 0) + tiers.get("near", 0) + tiers.get("long", 0)
    if total_mem == 0:
        seed_memory(MEMORY_URL)
    else:
        print(f"  Memory already seeded: {total_mem} total entries")

    # SCC stores are pre-configured via YAML
    print(f"  SCC stores are pre-configured; verifying...")
    for scc_url, label in [(SCC_NO_VEC_URL, "no-vector"), (SCC_VEC_URL, "vector")]:
        stores = get(scc_url, "/stores")
        names = [s["name"] for s in stores.get("stores", [])]
        print(f"    SCC ({label}): stores={names}")

    # ── Evaluate ─────────────────────────────────────────────────────────
    print("\n── Evaluation ──")
    report: dict[str, dict] = {}

    # Loci (no-vector)
    print(f"\n  Loci (no-vector @ {LOCI_NO_VEC_URL})...")
    loci_nv_results = run_loci_queries(LOCI_NO_VEC_URL, loci_queries, k=10)
    m_nv = compute_metrics_batch(loci_nv_results, k=10).aggregate()
    report["loci_no_vector"] = m_nv
    print(f"    hit_rate={m_nv.get('hit_rate', 0):.3f}  mrr={m_nv.get('mrr', 0):.3f}")
    print(f"    precision={m_nv.get('precision', 0):.3f}  recall={m_nv.get('recall', 0):.3f}  ndcg={m_nv.get('ndcg', 0):.3f}  iou={m_nv.get('iou', 0):.3f}  p-omega={m_nv.get('prec_omega', 0):.3f}")

    # Loci (with-vector)
    print(f"\n  Loci (vector @ {LOCI_VEC_URL})...")
    loci_v_results = run_loci_queries(LOCI_VEC_URL, loci_queries, k=10)
    m_v = compute_metrics_batch(loci_v_results, k=10).aggregate()
    report["loci_vector"] = m_v
    print(f"    hit_rate={m_v.get('hit_rate', 0):.3f}  mrr={m_v.get('mrr', 0):.3f}")
    print(f"    precision={m_v.get('precision', 0):.3f}  recall={m_v.get('recall', 0):.3f}  ndcg={m_v.get('ndcg', 0):.3f}  iou={m_v.get('iou', 0):.3f}  p-omega={m_v.get('prec_omega', 0):.3f}")

    # Memory
    print(f"\n  Memory @ {MEMORY_URL}...")
    mem_results = run_memory_queries(MEMORY_URL, memory_queries, k=10)
    m_mem = compute_metrics_batch(mem_results, k=10).aggregate()
    report["memory"] = m_mem
    print(f"    hit_rate={m_mem.get('hit_rate', 0):.3f}  mrr={m_mem.get('mrr', 0):.3f}")
    print(f"    precision={m_mem.get('precision', 0):.3f}  recall={m_mem.get('recall', 0):.3f}  ndcg={m_mem.get('ndcg', 0):.3f}  iou={m_mem.get('iou', 0):.3f}  p-omega={m_mem.get('prec_omega', 0):.3f}")

    # SCC (loci no-vector backend)
    print(f"\n  SCC (loci-no-vec @ {SCC_NO_VEC_URL})...")
    scc_nv_results, scc_nv_cov, scc_nv_den = run_scc_queries(SCC_NO_VEC_URL, corpus_queries, k=10)
    m_scc_nv = compute_metrics_batch(scc_nv_results, k=10).aggregate()
    m_scc_nv["docket_coverage"] = sum(scc_nv_cov) / len(scc_nv_cov) if scc_nv_cov else 0.0
    m_scc_nv["docket_density"] = sum(scc_nv_den) / len(scc_nv_den) if scc_nv_den else 0.0
    report["scc_no_vector"] = m_scc_nv
    print(f"    hit_rate={m_scc_nv.get('hit_rate', 0):.3f}  mrr={m_scc_nv.get('mrr', 0):.3f}")
    print(f"    precision={m_scc_nv.get('precision', 0):.3f}  recall={m_scc_nv.get('recall', 0):.3f}  ndcg={m_scc_nv.get('ndcg', 0):.3f}  iou={m_scc_nv.get('iou', 0):.3f}  p-omega={m_scc_nv.get('prec_omega', 0):.3f}  docket_cov={m_scc_nv.get('docket_coverage', 0):.3f}  docket_den={m_scc_nv.get('docket_density', 0):.3f}")

    # SCC (loci vector backend)
    print(f"\n  SCC (loci-vec @ {SCC_VEC_URL})...")
    scc_v_results, scc_v_cov, scc_v_den = run_scc_queries(SCC_VEC_URL, corpus_queries, k=10)
    m_scc_v = compute_metrics_batch(scc_v_results, k=10).aggregate()
    m_scc_v["docket_coverage"] = sum(scc_v_cov) / len(scc_v_cov) if scc_v_cov else 0.0
    m_scc_v["docket_density"] = sum(scc_v_den) / len(scc_v_den) if scc_v_den else 0.0
    report["scc_vector"] = m_scc_v
    print(f"    hit_rate={m_scc_v.get('hit_rate', 0):.3f}  mrr={m_scc_v.get('mrr', 0):.3f}")
    print(f"    precision={m_scc_v.get('precision', 0):.3f}  recall={m_scc_v.get('recall', 0):.3f}  ndcg={m_scc_v.get('ndcg', 0):.3f}  iou={m_scc_v.get('iou', 0):.3f}  p-omega={m_scc_v.get('prec_omega', 0):.3f}  docket_cov={m_scc_v.get('docket_coverage', 0):.3f}  docket_den={m_scc_v.get('docket_density', 0):.3f}")

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for name, m in report.items():
        base = f"  {name:25s}  hit={m.get('hit_rate',0):.3f}  mrr={m.get('mrr',0):.3f}  "
        base += f"prec={m.get('precision',0):.3f}  recall={m.get('recall',0):.3f}  "
        base += f"ndcg={m.get('ndcg',0):.3f}  iou={m.get('iou',0):.3f}  p-omega={m.get('prec_omega',0):.3f}"
        cov = m.get('docket_coverage')
        den = m.get('docket_density')
        if cov is not None and den is not None:
            base += f"  docket_cov={cov:.3f}  docket_den={den:.3f}"
        print(base)

    # Save report
    out = {
        "timestamp": time.time(),
        "systems": report,
        "aggregate": {
            "mean_hit_rate": sum(m.get("hit_rate", 0) for m in report.values()) / len(report),
            "mean_mrr": sum(m.get("mrr", 0) for m in report.values()) / len(report),
        }
    }
    out_path = "/home/caesar/serenprobe/live_eval_report.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nReport saved to {out_path}")


# ── Programmatic API ─────────────────────────────────────────────────────
def run_live_evaluation(
    memory_url: str = MEMORY_URL,
    loci_nv_url: str = LOCI_NO_VEC_URL,
    loci_v_url: str = LOCI_VEC_URL,
    scc_nv_url: str = SCC_NO_VEC_URL,
    scc_v_url: str = SCC_VEC_URL,
    k: int = 10,
) -> dict:
    """Run the full evaluation suite and return results as a dict.

    Args:
        memory_url: SerenMemory base URL.
        loci_nv_url: SerenLoci (no-vector) base URL.
        loci_v_url: SerenLoci (vector) base URL.
        scc_nv_url: SerenCorpusCallosum (loci-no-vec) base URL.
        scc_v_url: SerenCorpusCallosum (loci-vec) base URL.
        k: evaluation depth.

    Returns:
        {stores: {store_name: {aggregate: {...}, per_query: {...}}},
         query_count: int, date: str}
    """
    from datetime import datetime
    from .dataset import LOCI_COUNT, SHORT_COUNT, NEAR_COUNT, LONG_COUNT, seed_synthetic_dataset

    ds = seed_synthetic_dataset()
    loci_queries   = ds.filter_by_source("loci")
    memory_queries = ds.filter_by_source("memory")
    corpus_queries = ds.filter_by_source("corpus")
    n_queries = len(ds.queries)

    # ── Seed stores if empty ──────────────────────────────────────────────
    loci_nv_counts = get(loci_nv_url, "/counts")
    if loci_nv_counts.get("live", 0) == 0:
        seed_loci(loci_nv_url)

    loci_v_counts = get(loci_v_url, "/counts")
    if loci_v_counts.get("live", 0) == 0:
        seed_loci(loci_v_url)

    mem_status = get(memory_url, "/")
    tiers = mem_status.get("tiers", {})
    total_mem = tiers.get("short", 0) + tiers.get("near", 0) + tiers.get("long", 0)
    if total_mem == 0:
        seed_memory(memory_url)

    # ── Evaluate ──
    report: dict[str, dict] = {}

    # Loci (no-vector)
    loci_nv_results = run_loci_queries(loci_nv_url, loci_queries, k=k)
    m_nv = compute_metrics_batch(loci_nv_results, k=k).snapshot()
    m_nv["corpus_size"] = LOCI_COUNT
    m_nv["k"] = k
    report["loci_no_vector"] = m_nv

    # Loci (vector)
    loci_v_results = run_loci_queries(loci_v_url, loci_queries, k=k)
    m_v = compute_metrics_batch(loci_v_results, k=k).snapshot()
    m_v["corpus_size"] = LOCI_COUNT
    m_v["k"] = k
    report["loci_vector"] = m_v

    # Memory
    mem_results = run_memory_queries(memory_url, memory_queries, k=k)
    m_mem = compute_metrics_batch(mem_results, k=k).snapshot()
    m_mem["corpus_size"] = SHORT_COUNT + NEAR_COUNT + LONG_COUNT
    m_mem["k"] = k
    report["memory"] = m_mem

    scc_corpus_size = LOCI_COUNT + SHORT_COUNT + NEAR_COUNT + LONG_COUNT

    # SCC (loci no-vector backend)
    scc_nv_results, scc_nv_cov, scc_nv_den = run_scc_queries(scc_nv_url, corpus_queries, k=k)
    m_scc_nv = compute_metrics_batch(scc_nv_results, k=k).snapshot()
    agg_nv = m_scc_nv.get("aggregate", {})
    agg_nv["docket_coverage"] = sum(scc_nv_cov) / len(scc_nv_cov) if scc_nv_cov else 0.0
    agg_nv["docket_density"] = sum(scc_nv_den) / len(scc_nv_den) if scc_nv_den else 0.0
    m_scc_nv["aggregate"] = agg_nv
    m_scc_nv["docket_coverages"] = scc_nv_cov
    m_scc_nv["docket_densities"] = scc_nv_den
    m_scc_nv["corpus_size"] = scc_corpus_size
    m_scc_nv["k"] = k
    report["scc_no_vector"] = m_scc_nv

    # SCC (loci vector backend)
    scc_v_results, scc_v_cov, scc_v_den = run_scc_queries(scc_v_url, corpus_queries, k=k)
    m_scc_v = compute_metrics_batch(scc_v_results, k=k).snapshot()
    agg_v = m_scc_v.get("aggregate", {})
    agg_v["docket_coverage"] = sum(scc_v_cov) / len(scc_v_cov) if scc_v_cov else 0.0
    agg_v["docket_density"] = sum(scc_v_den) / len(scc_v_den) if scc_v_den else 0.0
    m_scc_v["aggregate"] = agg_v
    m_scc_v["docket_coverages"] = scc_v_cov
    m_scc_v["docket_densities"] = scc_v_den
    m_scc_v["corpus_size"] = scc_corpus_size
    m_scc_v["k"] = k
    report["scc_vector"] = m_scc_v

    return {
        "stores": report,
        "query_count": n_queries,
        "corpus_size": scc_corpus_size,
        "k": k,
        "date": datetime.utcnow().isoformat(),
    }


# ── Topology-driven evaluation (N dynamic columns, uploaded questions) ──
# The reshape: instead of the hardcoded 5 stores + synthetic dataset.py corpus,
# this evals EVERY store in a compiled topology as its own column, seeding via
# seed_stores and scoring the uploaded questions with honest ground truth:
#   expect_key     -> Loci canonical id via GET /fact (retrieval-independent)
#   expect_ref     -> Memory minted id via the SeedResult (retrieval-independent)
#   expect_content -> substring on hits (relative-only, same as live_eval)

def _delete(url: str, path: str) -> None:
    httpx.delete(f"{url}{path}", timeout=10.0)


def _get_params(url: str, path: str, params: dict) -> dict:
    resp = httpx.get(f"{url}{path}", params=params, timeout=15.0)
    return resp.json() if (resp.status_code == 200 and resp.content) else {}


def _search_payload(kind: str, query: str, k: int) -> dict:
    if kind == "seren_loci":
        return {"query": query, "n_results": k,
                "include_fundamentals": True, "include_superseded": False}
    if kind == "seren_memory":
        return {"query": query, "n_results": k, "include_short": True,
                "include_near": True, "include_long": True, "include_superseded": False}
    return {"query": query, "n_results": k}   # corpus


def _loci_haystack(h: dict) -> str:
    return " ".join([str(h.get("value", "")), str(h.get("why", "") or ""), str(h.get("key", ""))])


def _grade(hits, q, kind, resolve_key, resolve_ref, k):
    """(retrieved, relevant, coverage, density) for one query vs one store.
    Honest ground truth from expect_key (loci canonical id) + expect_ref (memory
    minted id); content-match (relative) adds matched hit ids on top."""
    retrieved = [(h["id"], h.get("score", 0.0)) for h in hits]
    relevant: set[str] = set()
    for pk in q.expect_key:
        rid = resolve_key(pk)
        if rid:
            relevant.add(rid)
    for ref in q.expect_ref:
        rid = resolve_ref(ref)
        if rid:
            relevant.add(rid)
    items_found = 0
    n_exp = len(q.expect_content)
    for exp in q.expect_content:
        ne = normalize_text(exp)
        if not ne:
            continue
        for h in hits:
            hay = _loci_haystack(h) if kind == "seren_loci" else str(h.get("content", ""))
            if ne in normalize_text(hay):
                relevant.add(h["id"])
                items_found += 1
                break
    density_hits = 0
    if q.expect_content:
        for h in hits[:k]:
            hay = _loci_haystack(h) if kind == "seren_loci" else str(h.get("content", ""))
            nh = normalize_text(hay)
            if any(normalize_text(e) and normalize_text(e) in nh for e in q.expect_content):
                density_hits += 1
    coverage = items_found / n_exp if n_exp > 0 else 0.0
    density = density_hits / min(k, len(retrieved)) if retrieved else 0.0
    return retrieved, relevant, coverage, density


def run_topology_evaluation(topology, url_of, questions, *, seed_dataset=None, seed_result=None,
                            k: int = 10, post=post, delete=_delete, get_params=_get_params,
                            seed: bool = True) -> dict:
    """Eval every store in a compiled topology as a dynamic column against the
    uploaded questions. Seeds via seed_stores first (unless a seed_result is
    passed or seed=False). Transport is injectable for testing; defaults hit the
    real services over httpx.

    Returns {stores: {name: snapshot+kind+flags}, question_count, topology, k, date}.
    """
    from datetime import datetime
    from .seed_dataset import seed_stores

    if seed and seed_result is None and seed_dataset is not None:
        seed_result = seed_stores(topology, seed_dataset, url_of, post=post, delete=delete)
    ref_to_id = seed_result.ref_to_id if seed_result else {}

    by_kind = {"loci": [], "memory": [], "corpus": []}
    for q in questions:
        if q.asks in by_kind:
            by_kind[q.asks].append(q)

    def eval_store(name, url, kind, qs):
        _key_cache: dict[str, str] = {}
        def resolve_key(pk):
            if pk in _key_cache:
                return _key_cache[pk]
            project, key = pk.split("/", 1) if "/" in pk else ("*", pk)
            data = get_params(url, "/fact", {"project": project, "key": key})
            rid = data.get("id", "") if isinstance(data, dict) else ""
            _key_cache[pk] = rid
            return rid
        def resolve_ref(ref):
            return ref_to_id.get(f"{name}:{ref}") or ref_to_id.get(ref) or ""

        results, coverages, densities = [], [], []
        for q in qs:
            resp = post(url, "/search", _search_payload(kind, q.query, k))
            hits = resp.get("hits", []) if isinstance(resp, dict) else []
            retrieved, relevant, cov, den = _grade(hits, q, kind, resolve_key, resolve_ref, k)
            results.append((retrieved, relevant)); coverages.append(cov); densities.append(den)
        m = compute_metrics_batch(results, k=k)
        if kind == "corpus":
            m.docket_coverages = coverages
            m.docket_densities = densities
        snap = m.snapshot()
        snap["kind"] = kind; snap["question_count"] = len(qs); snap["k"] = k
        return snap

    report: dict[str, dict] = {}
    for n in topology.loci:
        report[n.name] = eval_store(n.name, url_of[n.name], "seren_loci", by_kind["loci"])
        report[n.name]["flags"] = n.flags
    for n in topology.memory:
        report[n.name] = eval_store(n.name, url_of[n.name], "seren_memory", by_kind["memory"])
        report[n.name]["flags"] = n.flags
    for c in topology.corpus:
        report[c.name] = eval_store(c.name, url_of[c.name], "corpus", by_kind["corpus"])
        report[c.name]["flags"] = c.flags
        report[c.name]["is_catchall"] = c.is_catchall

    return {"stores": report, "question_count": len(questions),
            "topology": {"loci": [n.name for n in topology.loci],
                         "memory": [n.name for n in topology.memory],
                         "corpus": [c.name for c in topology.corpus]},
            "k": k, "date": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    main()
