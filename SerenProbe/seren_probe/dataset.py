"""
serenprobe.dataset
==================
Eval dataset: synthetic ground-truth queries for Loci, Memory, and SCC.

A dataset is a list of ``EvalQuery`` entries, each carrying:
  - ``query``: the retrieval query string
  - ``expected_ids``: ground-truth document ids that should be retrieved
  - ``expected_content``: optional content snippets for sanity checks
  - ``source``: which system the query targets ("loci", "memory", "corpus")
  - ``topics``: optional topic tags (for by_topic evaluation)

Also provides a synthetic seed generator that writes facts into Loci
and memories into Memory, then returns an aligned dataset.

Seed sizes (configurable via module-level constants):
  Loci   facts : LOCI_COUNT   = 300
  Short-term   : SHORT_COUNT  = 150
  Near-term    : NEAR_COUNT   = 30
  Long-term    : LONG_COUNT   = 120
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
#  Core data types
# ---------------------------------------------------------------------------

class EvalQuery(BaseModel):
    """One query with ground-truth relevance judgments."""
    query: str
    expected_ids: list[str] = Field(default_factory=list)
    expected_content: list[str] = Field(default_factory=list)
    source: str = "memory"          # "loci" | "memory" | "corpus"
    topics: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalDataset(BaseModel):
    """A set of queries for a RAG evaluation run."""
    name: str = "unnamed"
    description: str = ""
    queries: list[EvalQuery] = Field(default_factory=list)

    def filter_by_source(self, source: str) -> list[EvalQuery]:
        return [q for q in self.queries if q.source == source]

    def __iter__(self):
        return iter(self.queries)

    def __len__(self):
        return len(self.queries)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def normalize_text(t: str) -> str:
    """Collapse whitespace for deterministic matching."""
    import re
    return re.sub(r"\s+", " ", t.strip())


# ---------------------------------------------------------------------------
#  Seed-size constants  (tune these to control corpus size)
# ---------------------------------------------------------------------------
LOCI_COUNT   = 300      # facts written to Loci store
SHORT_COUNT  = 150      # short-term entries
NEAR_COUNT   = 30       # near-term intents
LONG_COUNT   = 120      # long-term entries

# How many queries to generate per system (≤ total entries)
LOCI_QUERY_COUNT  = 30
MEMORY_QUERY_COUNT = 30
CORPUS_QUERY_COUNT = 12


# ---------------------------------------------------------------------------
#  Programmatic seed-data generators
# ---------------------------------------------------------------------------

LOCI_PROJECTS = [
    "seren-memory", "seren-loci", "seren-corpus-callosum",
]

def _make_loci_facts(n: int) -> list[tuple[str, str, str, str]]:
    """Generate *n* diverse fact tuples (project, key, value, why)."""
    facts: list[tuple[str, str, str, str]] = []

    # ── core config facts (always included) ─────────────────────────────
    core = [
        ("seren-memory", "embedding_model", "all-MiniLM-L6-v2",
         "SerenMemory default embedding model; 384-dim vectors."),
        ("seren-memory", "chroma_persist_dir", "~/.seren-memory/chroma",
         "SerenMemory ChromaDB stores all tier data on disk."),
        ("seren-memory", "consolidator_interval", "72000 seconds (~20h)",
         "SerenMemory consolidator runs every ~20 hours by default."),
        ("seren-memory", "max_short_term_age", "8 days",
         "SerenMemory short-term entries older than this get aged out."),
        ("seren-memory", "promote_min_evidence", "3",
         "SerenMemory minimum short-term entries in a cluster before promotion."),
        ("seren-memory", "max_drafts_per_cycle", "3",
         "SerenMemory maximum redrafts before forced selection."),
        ("seren-memory", "near_term_ttl", "30 days",
         "SerenMemory near-term intents expire after 30 days if not completed."),
        ("seren-memory", "safety_net_size", "1000",
         "SerenMemory max pruned entries retained for safety."),
        ("seren-memory", "consolidator_batch_size", "50",
         "SerenMemory entries processed per consolidator tick."),
        ("seren-memory", "chroma_collection_short", "short_term",
         "SerenMemory Chroma collection name for short-term tier."),
        ("seren-memory", "chroma_collection_near", "near_term",
         "SerenMemory Chroma collection name for near-term tier."),
        ("seren-memory", "chroma_collection_long", "long_term",
         "SerenMemory Chroma collection name for long-term tier."),
        ("seren-memory", "embedder_migration_backup", "true",
         "SerenMemory timestamped backup taken before embedder migration."),
        ("seren-memory", "consolidator_auto_approve", "false",
         "SerenMemory draft model review required; auto-approve disabled."),

        ("seren-loci", "store_type", "sqlite3",
         "SerenLoci uses a single sqlite file with WAL mode."),
        ("seren-loci", "fts5_enabled", "true",
         "SerenLoci full-text search via FTS5 on key/value/why columns."),
        ("seren-loci", "supersede_rule", "strict",
         "SerenLoci exactly one live value per (project, key)."),
        ("seren-loci", "finder_default", "lexical (FTS5)",
         "SerenLoci when no embedder is configured, uses FTS5."),
        ("seren-loci", "sqlite_vec_available", "false",
         "SerenLoci sqlite-vec is optional; graceful degradation to FTS5."),
        ("seren-loci", "wal_mode", "true",
         "SerenLoci write-ahead log for concurrent reads."),
        ("seren-loci", "synchronous", "NORMAL",
         "SerenLoci balance durability vs. write throughput."),
        ("seren-loci", "cache_size_mb", "64",
         "SerenLoci SQLite page cache in megabytes."),
        ("seren-loci", "fts5_tokenizer", "porter",
         "SerenLoci stemming tokenizer for full-text search."),
        ("seren-loci", "fact_history_retention", "90 days",
         "SerenLoci how long superseded facts remain queryable."),

        ("seren-corpus-callosum", "fusion_mode", "rrf",
         "SerenCorpusCallosum Reciprocal Rank Fusion with k=60, embedder-agnostic."),
        ("seren-corpus-callosum", "authority_margin", "0.05",
         "SerenCorpusCallosum most-confident-store promotion margin for exact-key hits."),
        ("seren-corpus-callosum", "min_per_store", "1",
         "SerenCorpusCallosum each contributing store gets at least 1 seat in the merged packet."),
        ("seren-corpus-callosum", "edges_enabled", "true",
         "SerenCorpusCallosum topic-association edges appended after similarity search."),
        ("seren-corpus-callosum", "rrf_k", "60",
         "SerenCorpusCallosum RRF constant controlling score saturation."),
        ("seren-corpus-callosum", "timeout_ms", "5000",
         "SerenCorpusCallosum per-store query timeout before fallback."),
        ("seren-corpus-callosum", "store_registry_max", "10",
         "SerenCorpusCallosum maximum stores registered in the federation."),
        ("seren-corpus-callosum", "edge_max_per_hit", "5",
         "SerenCorpusCallosum max topic edges attached to one result."),

        ("*", "seren_brand", "Seren",
         "All services share the 'Seren' prefix."),
        ("*", "nano_floor_ethos", "graceful degradation",
         "Every service works without vector search."),
        ("*", "bearer_auth_pattern", "shared resolver via seren_meninges",
         "All services resolve bearer tokens through Meninges."),
        ("*", "versioning", "setuptools-scm",
         "Package version derived from git tags."),
        ("*", "logging_level", "INFO",
         "Default log level across all services."),
        ("*", "health_check_interval", "30",
         "Seconds between health-check pings."),
        ("*", "metrics_exporter", "prometheus",
         "OpenMetrics / Prometheus format."),
        ("*", "tracing_enabled", "false",
         "Distributed tracing off by default."),
    ]
    facts.extend(core)

    # ── project-specific configuration facts ────────────────────────────
    extra_per_project = {
        "seren-memory": [
            ("short_term_ttl_days", "8", "SerenMemory TTL for short-term tier."),
            ("consolidator_draft_model", "gpt-4o-mini",
             "SerenMemory model used for draft review and critique."),
            ("consolidator_cluster_threshold", "0.65",
             "SerenMemory cosine-similarity threshold for topic clustering."),
            ("max_draft_retries", "3", "SerenMemory max redrafts per consolidation cycle."),
            ("consolidator_auto_promote", "false",
             "SerenMemory requires model approval for long-term promotion."),
            ("chroma_persist_client", "PersistentClient",
             "SerenMemory Chroma client type for on-disk storage."),
            ("short_term_max_entries", "5000",
             "SerenMemory hard cap on short-term collection size."),
            ("near_term_max_entries", "2000",
             "SerenMemory hard cap on near-term collection size."),
            ("long_term_max_entries", "10000",
             "SerenMemory hard cap on long-term collection size."),
            ("consolidator_aging_batch", "200",
             "SerenMemory entries aged out per consolidator tick."),
            ("pruned_safety_net_ttl", "14 days",
             "SerenMemory how long pruned entries stay in the safety net."),
            ("consolidator_draft_timeout", "300",
             "SerenMemory seconds to wait for model draft review."),
            ("embedder_migration_retries", "3",
             "SerenMemory retries before falling back to backup restore."),
            ("safety_net_collection", "pruned",
             "SerenMemory Chroma collection for pruned entries."),
            ("consolidator_metrics_export", "true",
             "SerenMemory emit consolidation latency and throughput metrics."),
        ],
        "seren-loci": [
            ("fts5_rank_function", "bm25",
             "SerenLoci BM25 ranking for FTS5 search results."),
            ("fts5_tokenizer_porter", "true",
             "SerenLoci Porter stemmer enabled for FTS5 tokenization."),
            ("sqlite_journal_mode", "WAL",
             "SerenLoci write-ahead journal for concurrent access."),
            ("sqlite_page_size", "4096",
             "SerenLoci SQLite page size in bytes."),
            ("sqlite_cache_size", "65536",
             "SerenLoci cache size in kilobytes."),
            ("fact_upsert_max_retries", "3",
             "SerenLoci retries on concurrent write conflicts."),
            ("search_default_n_results", "10",
             "SerenLoci default number of search results returned."),
            ("search_include_fundamentals", "true",
             "SerenLoci include cross-project facts in search results."),
            ("search_include_superseded", "false",
             "SerenLoci exclude superseded facts from search."),
            ("fact_history_retention_count", "100",
             "SerenLoci max historical entries kept per (project, key)."),
            ("fts5_virtual_table", "facts_fts",
             "SerenLoci name of the FTS5 virtual table."),
            ("loci_db_lock_timeout", "5000",
             "SerenLoci SQLite busy timeout in milliseconds."),
            ("search_lexical_weight", "0.7",
             "SerenLoci weight for lexical score in hybrid search."),
            ("search_vector_weight", "0.3",
             "SerenLoci weight for vector score when embedder available."),
        ],
        "seren-corpus-callosum": [
            ("rrf_score_floor", "0.001",
             "Minimum RRF score to include a hit."),
            ("parallel_fanout", "true",
             "Fan-out queries to stores in parallel."),
            ("fusion_cache_ttl", "60",
             "Seconds to cache fused results."),
            ("store_timeout_grace", "2.0",
             "Seconds to wait after timeout for stragglers."),
            ("edge_topic_min_similarity", "0.5",
             "Min similarity for topic-edge attachment."),
            ("fusion_retry_count", "2",
             "Retries per store before marking it failed."),
            ("store_failure_log", "true",
             "Log store failures for observability."),
            ("federation_health_poll", "10",
             "Seconds between store health polls."),
            ("rrf_reciprocal_k", "60",
             "Reciprocal rank constant K."),
            ("edges_max_hits", "20",
             "Max hits to attach topic edges to."),
        ],
    }

    for proj, extras in extra_per_project.items():
        for key, value, why in extras:
            facts.append((proj, key, value, why))

    # ── cross-project / meta facts ──────────────────────────────────────
    meta_facts = [
        ("*", "deployment_env", "production",
         "Seren current deployment environment label."),
        ("*", "config_version", "2.4.3",
         "Seren schema version for configuration files."),
        ("*", "log_format", "json",
         "Seren structured JSON logging format."),
        ("*", "otel_service_name", "seren",
         "Seren OpenTelemetry service name."),
        ("*", "otel_sample_rate", "0.1",
         "Seren tracing sample rate for performance monitoring."),
        ("*", "cpu_cores_allocated", "4",
         "Seren expected CPU cores for scheduling decisions."),
        ("*", "memory_limit_mb", "1024",
         "Seren soft memory limit per service instance."),
        ("*", "disk_scrub_interval_days", "7",
         "Seren interval for log and cache rotation."),
        ("*", "secret_rotation_days", "90",
         "Seren bearer-token secret rotation interval."),
        ("*", "rate_limit_per_minute", "1000",
         "Seren global API rate-limit threshold."),
        ("*", "maintenance_window", "Sunday 03:00 UTC",
         "Seren weekly maintenance window."),
        ("*", "alarm_threshold_p99_latency", "500",
         "Seren P99 latency in ms above which alarms fire."),
        ("*", "alarm_threshold_error_rate", "0.05",
         "Seren error rate above 5% triggers pager."),
        ("*", "backup_retention_days", "30",
         "Seren retention period for daily backups."),
        ("*", "audit_log_enabled", "true",
         "Seren all config mutations are audit-logged."),
    ]
    facts.extend(meta_facts)

    # ── Pad to n with synthetic-but-plausible facts ──────────────────────
    #  We already have about 60+ facts; generate the rest programmatically.
    pad_count = n - len(facts)
    _topics = ["embedding", "search", "storage", "cache", "auth",
               "observability", "scheduling", "migration", "backup",
               "rate-limit", "fusion", "consolidation", "draft", "pruning",
               "health-check", "logging", "tracing", "secret", "deploy",
               "alarm"]
    _detail_templates = [
        "configured for {} with a default timeout of 30000ms",
        "enables {} across all service instances in the cluster",
        "sets the {} threshold to 0.75 for optimal recall",
        "controls {} behavior: enabled by default, tunable per environment",
        "defines the {} strategy for cross-service coordination",
        "specifies the {} endpoint with retry policy (3 attempts, exponential backoff)",
        "regulates {} capacity: soft limit of 1000, hard limit of 5000",
        "stores {} credentials in the shared keyring with 90-day rotation",
        "configures {} tracing: sampling rate 10%, export endpoint otel.example.com",
        "tunes {} for low-latency: batch size 100, flush interval 500ms",
    ]
    for i in range(pad_count):
        proj = LOCI_PROJECTS[i % len(LOCI_PROJECTS)]
        topic = _topics[(i + len(facts)) % len(_topics)]
        key = f"conf_{topic}_{i:03d}"
        value = f"value_{topic}_{i:03d}"
        tpl = _detail_templates[(i + len(facts)) % len(_detail_templates)]
        why = f"Seren {tpl.format(topic)}."
        facts.append((proj, key, value, why))

    return facts[:n]


MEMORY_TOPICS = [
    "rag-eval", "consolidator", "draft-review", "loci",
    "memory", "migration", "chroma", "corpus-callosum",
    "fusion", "rrf", "edges", "topic", "viewer", "drafts",
    "ui", "tiers", "near-term", "triggers", "long-term",
    "ranking", "architecture", "search", "lexical",
    "embedder", "backup", "pruning", "safety-net",
    "intents", "alarms", "observability",
]

def _make_memory_short(n: int) -> list[dict[str, str]]:
    """Generate *n* short-term entries with diverse topics."""
    entries: list[dict[str, str]] = []

    templates = [
        "Today we worked on {topic}: {detail}",
        "Discussed {topic} improvements: {detail}",
        "Noticed that {topic} has {detail}",
        "The {topic} module now supports {detail}",
        "Updated the {topic} pipeline: {detail}",
        "Reviewed {topic} performance: {detail}",
        "Implemented {topic} feature: {detail}",
        "Debugged {topic} issue: {detail}",
        "Finalized {topic} design: {detail}",
        "Explored {topic} alternatives: {detail}",
        "Architected {topic} solution: {detail}",
        "Prototyped {topic} integration: {detail}",
        "Validated {topic} correctness: {detail}",
        "Deployed {topic} configuration: {detail}",
        "Monitored {topic} behavior: {detail}",
        "Documented {topic} API: {detail}",
        "Refactored {topic} internals: {detail}",
        "Benchmarked {topic} throughput: {detail}",
        "Secured {topic} access: {detail}",
        "Migrated {topic} data: {detail}",
    ]
    details = [
        "supports hit rate, MRR, precision@k, recall@k, and NDCG metrics",
        "model approves or rejects with critique, up to 3 redrafts before forced selection",
        "FTS5 lexical search degrades gracefully when no embedder is configured",
        "re-embeds all entries in-place with a timestamped backup for rollback safety",
        "rank-only scoring so changing an embedder never corrupts the merged ranking",
        "topic-association edges surface entries sharing a tag even when vector similarity misses them",
        "the draft review queue shows approve/reject/select buttons",
        "three tiers each have their own Chroma collection under one PersistentClient",
        "near-term intents support TIME, EVENT, and ALWAYS trigger types",
        "long-term entries carry an evidence_count that boosts recall proportionally to log(evidence)",
        "the nano-floor ethos ensures graceful degradation when vector search is unavailable",
        "consolidator clusters short-term entries by topic before promoting to long-term",
        "pruned entries stay in a safety-net collection for 14 days before permanent deletion",
        "embedder migration takes a timestamped backup and falls back on failure",
        "RRF fusion uses rank-only scoring and is embedder-agnostic",
        "bearer auth is resolved through a shared seren_meninges resolver",
        "the health-check endpoint returns 200 with version and uptime",
        "metrics are exported in Prometheus format via the OpenMetrics exporter",
        "the audit log records every config mutation with a timestamp and actor",
        "rate-limiting is applied per minute with a configurable threshold",
        "the consolidation draft cycle runs up to 3 redrafts before forced selection",
        "topic clustering uses cosine similarity with a 0.65 threshold",
        "the safety net retains up to 1000 pruned entries at any time",
        "consolidator metrics are exported for latency and throughput observability",
        "the SCC federation fans out queries in parallel with per-store timeouts",
        "fusion results are cached for 60 seconds to reduce redundant computation",
        "store failures are logged and the federation retries up to 2 times",
        "edge topic attachment requires a minimum similarity of 0.5",
        "the RRF constant k=60 controls score saturation across stores",
        "the store registry supports up to 10 concurrent store adapters",
        "consolidator draft timeout prevents infinite model review loops",
        "near-term intent completion triggers cleanup of expired triggers",
        "the viewer UI renders the full docket with provenance badges",
        "chroma collections are isolated per tier to avoid cross-contamination",
        "superseded facts remain queryable through fact history for 90 days",
        "the bearer token validation is cached for 60 seconds to reduce keyring load",
        "rate-limit utilization is exported as a Prometheus gauge for dashboarding",
        "config versions are tracked and audit-logged for compliance purposes",
        "the OpenTelemetry trace sampling rate is configurable per environment",
        "the consolidation aging batch removes up to 200 expired entries per cycle",
    ]

    for i in range(n):
        topic = MEMORY_TOPICS[i % len(MEMORY_TOPICS)]
        tpl = templates[i % len(templates)]
        det = details[i % len(details)]
        content = tpl.format(topic=topic, detail=det)
        entries.append({
            "content": content,
            "topic": topic,
        })
    return entries


def _make_memory_near(n: int) -> list[dict[str, str]]:
    """Generate *n* near-term intents."""
    intents = [
        "Complete the RAG evaluation integration and run a full eval sweep",
        "Review the consolidation draft gate and verify 3-draft max works",
        "Add a new adapter for a custom store type to SCC's registry",
        "Investigate FTS5 ranking performance on large fact sets",
        "Write migration docs for ChromaDB embedder changes",
        "Set up Prometheus alerting for consolidation latency spikes",
        "Implement the near-term intent trigger dispatch loop",
        "Add a CLI tool for manual fact inspection in Loci",
        "Create a dashboard showing per-tier memory usage",
        "Benchmark RRF fusion latency across 10 concurrent stores",
        "Write a design doc for the nano-floor ethos across all services",
        "Refactor the consolidator clustering algorithm for better topic separation",
        "Build a synthetic data generator for RAG evaluation benchmarks",
        "Create a Grafana dashboard for SCC fusion performance metrics",
        "Write integration tests for the bearer auth rotation policy",
        "Benchmark sqlite-vec hybrid search against pure FTS5 on 100k facts",
        "Build a docket quality scoring tool for SCC eval",
        "Review the safety net pruning policy for edge cases",
        "Add a health-check aggregator endpoint to the SCC federation",
        "Write a migration guide for embedder model changes in Memory",
    ]
    out = []
    for i in range(n):
        out.append({
            "intent": intents[i % len(intents)],
            "topic": MEMORY_TOPICS[i % len(MEMORY_TOPICS)],
        })
    return out


def _make_memory_long(n: int) -> list[dict[str, Any]]:
    """Generate *n* long-term entries with evidence counts."""
    entries: list[dict[str, Any]] = []

    architectures = [
        {"content": "SerenLoci uses a sqlite3 fact store with strict supersede rule: one live value per (project, key). FTS5 provides lexical search; sqlite-vec provides optional vector search.",
         "topic": "loci,architecture", "evidence_count": 5},
        {"content": "SerenMemory has three tiers: ShortTerm (working memory, ~8 day TTL), NearTerm (open loops with triggers), and LongTerm (consolidated knowledge, gated writes through consolidator).",
         "topic": "memory,architecture", "evidence_count": 8},
        {"content": "SerenCorpusCallosum fans out queries to all configured stores in parallel, applies per-store relevance floors, then RRF-fuses results. Topic edges are appended after similarity search for associative recall.",
         "topic": "corpus-callosum,architecture", "evidence_count": 6},
        {"content": "The consolidator runs a dream cycle: reads brief, handles forget-flags, clusters short-term entries by topic, promotes clusters to long-term or drafts them for model review, ages out old entries, and sweeps the pruned safety net.",
         "topic": "consolidator,architecture", "evidence_count": 7},
        {"content": "Embedder migration in SerenMemory is done through ChromaDB's API: delete_collection, recreate with new EF, re-add all documents. A timestamped backup is taken first; on failure the live dir is restored.",
         "topic": "memory,migration", "evidence_count": 4},
        {"content": "The SCC federation uses RRF with k=60, parallel fan-out, per-store timeouts, and topic-edge attachment for associative recall. Stores register via adapters and the federation health-polls every 10 seconds.",
         "topic": "corpus-callosum,federation", "evidence_count": 6},
        {"content": "Loci's FTS5 search uses BM25 ranking with a Porter stemmer tokenizer. When sqlite-vec is available, hybrid search weights lexical at 0.7 and vector at 0.3.",
         "topic": "loci,search", "evidence_count": 4},
        {"content": "The consolidator draft model (gpt-4o-mini) reviews clusters and can approve, reject with critique, or request up to 3 redrafts. If the model fails to decide, the system forces selection from the best cluster.",
         "topic": "consolidator,draft-review", "evidence_count": 7},
        {"content": "Near-term intents support three trigger types: TIME (absolute or relative), EVENT (webhook or system signal), and ALWAYS (persistent reminder). Expired intents are aged out after 30 days.",
         "topic": "memory,near-term", "evidence_count": 5},
        {"content": "The safety net retains pruned entries for 14 days with a max of 1000 entries. The consolidator sweeps the net each cycle, permanently deleting entries past the retention window.",
         "topic": "memory,pruning", "evidence_count": 3},
        {"content": "Bearer auth is resolved through seren_meninges, a shared auth service. All Seren services delegate token validation to Meninges, ensuring consistent policy across Loci, Memory, and SCC.",
         "topic": "auth,architecture", "evidence_count": 5},
        {"content": "Observability infrastructure: Prometheus metrics, structured JSON logging, OpenTelemetry tracing (sampled at 10%), and health-check endpoints on every service.",
         "topic": "observability,architecture", "evidence_count": 4},
        {"content": "Configuration management: all services read from YAML files with environment-override support. Config versions are tracked and audit-logged for compliance.",
         "topic": "config,architecture", "evidence_count": 3},
        {"content": "Rate limiting is applied globally at 1000 requests per minute per API key. The rate-limiter uses a token-bucket algorithm and exports current utilization as a Prometheus gauge.",
         "topic": "rate-limit,architecture", "evidence_count": 3},
        {"content": "Bearer token secrets rotate every 90 days via the secret rotation policy enforced by seren_meninges. Rotation is timestamped, backed up, and audited for compliance. The bearer auth token is validated by Meninges across all services.",
         "topic": "auth,security", "evidence_count": 3},
        {"content": "The FTS5 AND-vs-OR heuristic uses AND for 3 or fewer tokens and OR for more than 3, tuned to balance precision and recall across different query lengths.",
         "topic": "loci,fts5", "evidence_count": 4},
        {"content": "The consolidate promote gate requires a minimum of 3 entries in a cluster before promoting to long-term, preventing premature consolidation of sparse topics.",
         "topic": "consolidator,promotion", "evidence_count": 3},
        {"content": "Memory tiered search first queries long-term, then near-term, then short-term, with deduplication across tiers to avoid duplicate results in the merged set.",
         "topic": "memory,search", "evidence_count": 5},
        {"content": "The SCC federation store adapter for SerenMemory maps the /search response to a common Hit shape, normalizing scores and metadata from the tier-weighted results.",
         "topic": "corpus-callosum,adapters", "evidence_count": 4},
        {"content": "Consolidator aging removes entries past the max short-term age of 8 days in batches of 200 per cycle, using a cursor-based sweep to avoid blocking reads.",
         "topic": "consolidator,aging", "evidence_count": 3},
        {"content": "The viewer UI renders the full SCC docket with provenance badges per store, showing RRF score, store rank, and base relevance for each hit in the packet.",
         "topic": "viewer,ui", "evidence_count": 4},
        {"content": "Loci's fact history keeps superseded values queryable for 90 days with a max of 100 historical entries per (project, key), enabling audit trails for config changes.",
         "topic": "loci,history", "evidence_count": 3},
        {"content": "The near-term intent completion endpoint marks an intent done, cleans up its triggers, and moves the entry to long-term if it has accumulated enough evidence.",
         "topic": "memory,near-term", "evidence_count": 4},
        {"content": "SCC's per-store timeout grace allows 2 seconds of extra wait for straggler stores before the fan proceeds without them, preventing a single slow store from blocking the merge.",
         "topic": "corpus-callosum,federation", "evidence_count": 3},
        {"content": "Bearer token resolution is cached for 60 seconds with a bloom-filter revocation check, balancing security against keyring load across high-throughput request paths.",
         "topic": "auth,caching", "evidence_count": 4},
        {"content": "The consolidator auto-promote flag bypasses model review for low-risk clusters, promoting them directly to long-term when the cluster confidence exceeds 0.85.",
         "topic": "consolidator,auto-promote", "evidence_count": 3},
        {"content": "Memory migration stamping records the current embedder model name on each entry, so a consistency check after migration can verify all entries were re-embedded correctly.",
         "topic": "memory,migration", "evidence_count": 4},
        {"content": "The SCC fusion cache stores fused results keyed by query text with a 60-second TTL, reducing redundant computation for repeated queries within the cache window.",
         "topic": "corpus-callosum,caching", "evidence_count": 3},
        {"content": "Loci's FTS5 virtual table indexes the key, value, and why columns with a Porter stemmer tokenizer, enabling cross-column full-text search in a single query.",
         "topic": "loci,fts5", "evidence_count": 5},
        {"content": "The safety net sweep permanently deletes entries past 14 days of retention, using a batched delete to minimize ChromaDB write amplification during consolidation.",
         "topic": "memory,pruning", "evidence_count": 3},
    ]

    # Pad to n with synthetic long-term entries
    topics_pool = [
        "memory,consolidation", "loci,fts5", "corpus-callosum,rrf",
        "memory,tiers", "auth,bearer", "observability,metrics",
        "config,audit", "rate-limit,bucket", "memory,migration",
        "corpus-callosum,edges", "loci,supersession", "memory,drafts",
        "consolidator,clustering", "memory,safety-net", "loci,vector",
        "memory,search-tiers", "consolidator,draft-review",
        "corpus-callosum,adapters", "auth,caching", "viewer,ui",
        "loci,history", "memory,pruning-sweep", "consolidator,aging",
        "corpus-callosum,timeout", "memory,auto-promote",
        "loci,fts5-and-or", "memory,migration-stamping",
        "corpus-callosum,caching", "loci,fts5-index",
        "memory,near-term-completion",
    ]
    for i in range(n):
        if i < len(architectures):
            entries.append(architectures[i])
        else:
            idx = (i - len(architectures)) % len(topics_pool)
            entries.append({
                "content": f"Long-term consolidated knowledge about {topics_pool[idx]}: detailed architectural notes with cross-references to related subsystems.",
                "topic": topics_pool[idx],
                "evidence_count": (i % 8) + 1,
            })
    return entries


# ── Pre-built lists (generated at module load time) ──────────────────────
LOCI_FACTS    = _make_loci_facts(LOCI_COUNT)
MEMORY_SHORT  = _make_memory_short(SHORT_COUNT)
MEMORY_NEAR   = _make_memory_near(NEAR_COUNT)
MEMORY_LONG   = _make_memory_long(LONG_COUNT)


def seed_synthetic_dataset(
    loci_store=None,
    memory_store=None,
) -> EvalDataset:
    """Generate a synthetic eval dataset and optionally seed the stores.

    Returns an EvalDataset with queries aligned to the seeded data.
    Call with stores=None to just get the dataset definition.
    """
    queries: list[EvalQuery] = []

    # ── Loci seeding ─────────────────────────────────────────────────────
    if loci_store is not None:
        from seren_loci.models.schemas import FactWrite
        for project, key, value, why in LOCI_FACTS:
            loci_store.set_fact(FactWrite(
                project=project, key=key, value=value, why=why,
            ))

    # ── Loci queries ─────────────────────────────────────────────────────
    #  Pick a diverse subset of facts to query for.
    loci_fact_keys = [
        ("seren-memory", "embedding_model", "all-MiniLM-L6-v2"),
        ("seren-corpus-callosum", "fusion_mode", "rrf"),
        ("seren-loci", "supersede_rule", "strict"),
        ("seren-loci", "finder_default", "lexical (FTS5)"),
        ("*", "nano_floor_ethos", "graceful degradation"),
        ("*", "bearer_auth_pattern", "shared resolver via seren_meninges"),
        ("seren-memory", "promote_min_evidence", "3"),
        ("seren-loci", "store_type", "sqlite3"),
        ("seren-memory", "max_drafts_per_cycle", "3"),
        ("seren-corpus-callosum", "authority_margin", "0.05"),
        ("seren-corpus-callosum", "rrf_k", "60"),
        ("*", "logging_level", "INFO"),
        ("seren-loci", "fts5_tokenizer", "porter"),
        ("seren-memory", "consolidator_auto_approve", "false"),
        ("*", "metrics_exporter", "prometheus"),
        ("*", "rate_limit_per_minute", "1000"),
        ("seren-corpus-callosum", "store_timeout_grace", "2.0"),
        ("seren-loci", "search_lexical_weight", "0.7"),
        ("seren-memory", "short_term_ttl_days", "8"),
        ("seren-corpus-callosum", "edges_max_hits", "20"),
        ("*", "secret_rotation_days", "90"),
        ("seren-memory", "consolidator_draft_model", "gpt-4o-mini"),
        ("seren-loci", "fts5_rank_function", "bm25"),
        ("*", "otel_sample_rate", "0.1"),
        ("seren-corpus-callosum", "fusion_cache_ttl", "60"),
        ("seren-memory", "consolidator_interval", "72000 seconds (~20h)"),
        ("*", "alarm_threshold_error_rate", "0.05"),
        ("seren-loci", "cache_size_mb", "64"),
        ("seren-memory", "safety_net_size", "1000"),
        ("*", "deployment_env", "production"),
    ]

    # Build richer queries: include terms from the fact's why/value so
    # that FTS5 lexical search has a better chance of matching content.
    _loci_why_map = {}
    for p, k, v, w in LOCI_FACTS:
        _loci_why_map[(p, k)] = w

    loci_queries = []
    for proj, key, val in loci_fact_keys:
        why = _loci_why_map.get((proj, key), "")
        # Construct a query that includes key terms and descriptive text
        # from the 'why' field so FTS5 has more content to match.
        query_parts = [key.replace("_", " ")]
        if why:
            # Add a few meaningful terms from the why field
            words = why.split()
            keywords = [w for w in words if len(w) > 3 and w not in ("the", "and", "for", "with", "that")]
            if keywords:
                query_parts.extend(keywords[:4])
        query = " ".join(query_parts)
        loci_queries.append(EvalQuery(
            query=query,
            expected_content=[val],
            source="loci",
            metadata={"project": proj, "key": key, "value": val},
        ))
    queries.extend(loci_queries)

    # ── Memory seeding ───────────────────────────────────────────────────
    if memory_store is not None:
        from seren_memory.models.schemas import (
            ShortTermEntry, NearTermEntry, LongTermEntry, Source,
        )
        # Seed short-term
        for item in MEMORY_SHORT:
            memory_store.add_short(ShortTermEntry(
                content=item["content"], topic=item["topic"],
                source=Source.ASSISTANT,
            ))
        # Seed near-term
        for item in MEMORY_NEAR:
            memory_store.add_near(NearTermEntry(
                intent=item["intent"], topic=item["topic"],
                source=Source.ASSISTANT,
            ))
        # Seed long-term
        for item in MEMORY_LONG:
            memory_store.add_long(LongTermEntry(
                content=item["content"], topic=item["topic"],
                evidence_count=item["evidence_count"],
                source=Source.CONSOLIDATOR,
            ))

    # ── Memory queries ───────────────────────────────────────────────────
    memory_queries = [
        EvalQuery(
            query="RAG evaluation metrics support",
            expected_content=["hit rate", "MRR", "precision@k", "recall@k", "NDCG"],
            source="memory",
            topics=["rag-eval"],
        ),
        EvalQuery(
            query="consolidator draft review cycle",
            expected_content=["approves", "rejects", "critique", "3 redrafts", "forced selection"],
            source="memory",
            topics=["draft-review", "consolidator"],
        ),
        EvalQuery(
            query="three memory tiers purposes",
            expected_content=["ShortTerm", "NearTerm", "LongTerm"],
            source="memory",
            topics=["memory", "architecture"],
        ),
        EvalQuery(
            query="SCC fusion works across stores RRF Reciprocal Rank Fusion rank-only embedder-agnostic",
            expected_content=["RRF", "Reciprocal Rank Fusion", "rank-only", "embedder-agnostic"],
            source="memory",
            topics=["corpus-callosum", "fusion"],
        ),
        EvalQuery(
            query="near-term intents trigger types",
            expected_content=["TIME", "EVENT", "ALWAYS"],
            source="memory",
            topics=["near-term", "triggers"],
        ),
        EvalQuery(
            query="evidence_count long-term recall ranking",
            expected_content=["evidence_count", "log", "boost"],
            source="memory",
            topics=["long-term", "ranking"],
        ),
        EvalQuery(
            query="ChromaDB migration embedder changes",
            expected_content=["delete_collection", "recreate", "re-add", "backup"],
            source="memory",
            topics=["memory", "migration"],
        ),
        EvalQuery(
            query="consolidator dream cycle",
            expected_content=["brief", "forget-flags", "cluster", "promote", "age out", "pruned"],
            source="memory",
            topics=["consolidator", "architecture"],
        ),
        EvalQuery(
            query="Complete the RAG evaluation integration",
            expected_content=["RAG evaluation", "eval sweep"],
            source="memory",
            topics=["rag-eval"],
            metadata={"intent_match": True},
        ),
        EvalQuery(
            query="Add a new adapter for a custom store type",
            expected_content=["new adapter", "custom store type"],
            source="memory",
            topics=["adapters"],
            metadata={"intent_match": True},
        ),
        EvalQuery(
            query="nano floor ethos graceful degradation",
            expected_content=["graceful degradation", "vector search unavailable"],
            source="memory",
            topics=["loci", "search"],
        ),
        EvalQuery(
            query="topic clustering cosine similarity threshold",
            expected_content=["cosine similarity", "0.65", "cluster"],
            source="memory",
            topics=["consolidator", "clustering"],
        ),
        EvalQuery(
            query="safety net pruned entries retention",
            expected_content=["safety net", "14 days", "1000", "pruned"],
            source="memory",
            topics=["memory", "pruning"],
        ),
        EvalQuery(
            query="bearer auth shared resolver meninges",
            expected_content=["seren_meninges", "bearer", "shared resolver"],
            source="memory",
            topics=["auth"],
        ),
        EvalQuery(
            query="consolidator metrics export latency throughput",
            expected_content=["consolidator", "latency", "throughput", "metrics"],
            source="memory",
            topics=["observability", "consolidator"],
        ),
        EvalQuery(
            query="RRF fusion parallel fanout per-store timeouts",
            expected_content=["RRF", "parallel", "fan-out", "timeout"],
            source="memory",
            topics=["corpus-callosum", "fusion"],
        ),
        EvalQuery(
            query="rate limiting token bucket Prometheus gauge",
            expected_content=["rate limiting", "token-bucket", "Prometheus"],
            source="memory",
            topics=["rate-limit"],
        ),
        EvalQuery(
            query="FTS5 BM25 ranking Porter stemmer",
            expected_content=["BM25", "Porter", "stemmer", "FTS5"],
            source="memory",
            topics=["loci", "search"],
        ),
        EvalQuery(
            query="draft model review gpt-4o-mini approve reject redraft",
            expected_content=["gpt-4o-mini", "approve", "reject", "redraft", "forced selection"],
            source="memory",
            topics=["consolidator", "draft-review"],
        ),
        EvalQuery(
            query="configuration audit log compliance version tracking",
            expected_content=["audit log", "config version", "compliance"],
            source="memory",
            topics=["config", "audit"],
        ),
        EvalQuery(
            query="OpenTelemetry tracing sampled health check endpoints",
            expected_content=["OpenTelemetry", "tracing", "health-check", "Prometheus"],
            source="memory",
            topics=["observability"],
        ),
        EvalQuery(
            query="near-term trigger dispatch loop intents",
            expected_content=["trigger dispatch", "TIME", "EVENT", "ALWAYS"],
            source="memory",
            topics=["near-term", "triggers"],
        ),
        EvalQuery(
            query="fact inspection CLI tool Loci",
            expected_content=["CLI", "fact inspection", "Loci"],
            source="memory",
            topics=["loci", "tools"],
        ),
        EvalQuery(
            query="per-tier memory usage dashboard",
            expected_content=["dashboard", "per-tier", "memory usage"],
            source="memory",
            topics=["ui", "observability"],
        ),
        EvalQuery(
            query="RRF fusion latency benchmark concurrent stores",
            expected_content=["RRF", "latency", "benchmark", "concurrent"],
            source="memory",
            topics=["corpus-callosum", "performance"],
        ),
        EvalQuery(
            query="secret rotation bearer token security 90 days bearer token rotation",
            expected_content=["secret rotation", "90 days", "bearer token"],
            source="memory",
            topics=["auth", "security"],
        ),
        EvalQuery(
            query="Prometheus alerting consolidation latency spikes",
            expected_content=["Prometheus", "alerting", "consolidation", "latency"],
            source="memory",
            topics=["observability", "alarms"],
        ),
        EvalQuery(
            query="ChromaDB embedder migration backup restore fallback",
            expected_content=["timestamped backup", "restore", "fallback", "migration"],
            source="memory",
            topics=["memory", "migration"],
        ),
        EvalQuery(
            query="FTS5 ranking performance large fact sets",
            expected_content=["FTS5", "ranking", "performance", "large fact sets"],
            source="memory",
            topics=["loci", "search", "performance"],
        ),
        EvalQuery(
            query="consolidator cluster promote threshold cosine",
            expected_content=["cluster", "promote", "cosine", "threshold", "0.65"],
            source="memory",
            topics=["consolidator", "clustering"],
        ),
    ]
    queries.extend(memory_queries)

    # ── Corpus Callosum queries (docket-building eval) ────────────────────
    #  SCC is a *docket builder*: it should return not just the exact fact
    #  but a rich associative packet — gotchas, related experiences, cross-
    #  references, configuration context.  Each expected_content list
    #  includes both core terms (the direct answer) and context terms
    #  (associated knowledge that a good docket would contain).
    corpus_queries = [
        EvalQuery(
            query="How does Seren handle memory and facts?",
            expected_content=[
                # Core: system names
                "SerenLoci", "SerenMemory", "SerenCorpusCallosum",
                # Context: how they work together
                "FTS5", "chroma", "tiers", "consolidation",
                "search", "retrieval", "federation",
                # Gotchas / related
                "safe-mode", "graceful degradation", "embedder",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="What is the consolidation and draft review process?",
            expected_content=[
                # Core
                "consolidator", "draft", "approve", "reject", "cluster", "synthesize",
                # Context: the memory tiers involved
                "short-term", "long-term", "near-term", "pruned",
                "auto_promote", "draft_retries", "gpt-4o-mini",
                # Gotchas
                "consolidator_aging_batch", "consolidator_draft_timeout",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does the system handle embedder changes and migration?",
            expected_content=[
                # Core
                "embedder", "migration", "re-embed", "stamp", "safe-mode",
                # Context: what gets migrated
                "delete_collection", "backup", "restore",
                "PersistentClient", "chroma",
                # Gotchas
                "embedder_migration_retries", "migration",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="What search and retrieval mechanisms exist across all services?",
            expected_content=[
                # Core
                "FTS5", "vector", "exact", "RRF", "topic edges",
                # Context: how they combine
                "lexical", "hybrid", "reciprocal rank fusion",
                "bm25", "porter", "similarity",
                # Cross-service
                "Loci", "Memory", "CorpusCallosum",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does bearer auth work across the Seren ecosystem?",
            expected_content=[
                # Core
                "seren_meninges", "bearer", "shared resolver", "token validation",
                # Context: auth infrastructure
                "bearer_auth_pattern", "secret rotation",
                "rate limit", "token-bucket",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="What observability and monitoring infrastructure is shared?",
            expected_content=[
                # Core
                "Prometheus", "OpenTelemetry", "health-check", "JSON logging",
                # Context: what's monitored
                "metrics_exporter", "otel_service_name",
                "alarm_threshold_p99_latency", "alarm_threshold_error_rate",
                "health_check_interval",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How is configuration managed and audited across services?",
            expected_content=[
                # Core
                "YAML", "environment override", "audit log", "config version",
                # Context: config infrastructure
                "versioning", "audit_log_enabled", "config_version",
                "deployment_env", "maintenance_window",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="What rate limiting and security measures are in place? rate limit token-bucket secret rotation bearer auth",
            expected_content=[
                # Core
                "rate limit", "token-bucket", "secret rotation", "bearer auth",
                # Context: security infrastructure
                "rate_limit_per_minute", "secret_rotation_days",
                "bearer_auth_pattern", "seren_meninges",
                # Gotchas
                "alarm_threshold_error_rate",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does the consolidator dream cycle integrate with the memory tiers?",
            expected_content=[
                # Core
                "consolidator", "dream cycle", "short-term", "long-term", "pruned",
                # Context: tier mechanics
                "near-term", "auto_promote", "cluster_threshold",
                "consolidator_aging_batch", "consolidator_draft_timeout",
                # Gotchas
                "pruned_safety_net_ttl", "safety_net_collection",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does RRF fusion combine results from Loci and Memory?",
            expected_content=[
                # Core
                "RRF", "reciprocal rank fusion", "Loci", "Memory", "rank-only",
                # Context: fusion mechanics
                "rrf_k", "rrf_score_floor", "fusion_cache_ttl",
                "parallel_fanout", "store_timeout_grace",
                # Cross-service
                "CorpusCallosum", "federation",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="What happens during embedder migration and how is data preserved?",
            expected_content=[
                # Core
                "embedder migration", "backup", "re-embed", "delete_collection", "restore",
                # Context: migration mechanics
                "embedder_migration_retries", "safe-mode",
                "PersistentClient", "chroma",
                # Gotchas
                "stamp", "restore",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How do topic-association edges enhance cross-store retrieval?",
            expected_content=[
                # Core
                "topic edges", "associative recall", "similarity", "tags",
                # Context: edge mechanics
                "edge_topic_min_similarity", "edges_max_hits",
                "edge_max_per_hit", "topic edges",
                # Cross-service
                "Loci", "Memory", "CorpusCallosum",
            ],
            source="corpus",
        ),
    ]

    # ── expand to 30 corpus queries (was 12) ──────────────────────────────
    corpus_queries.extend([
        EvalQuery(
            query="How does the runtime overlay system manage dynamic store registration? runtime overlay add-store remove-store managed base",
            expected_content=[
                "runtime overlay", "runtime-stores.json", "managed stores", "base stores",
                "add-store", "remove-store", "overlay", "federation",
                "CorpusCallosum", "stores endpoint",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="What is the Bridge viewer web UI and how do you manage stores through it? bridge viewer stores tab add form managed base delete",
            expected_content=[
                "bridge viewer", "stores tab", "add-store form", "managed stores",
                "base stores", "remove", "federation",
                "CorpusCallosum", "viewer", "ui",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does graceful degradation work when a store is down or slow during SCC federation? timeout grace degradation skipped slow down",
            expected_content=[
                "graceful degradation", "per-store timeout", "skipped", "slow store",
                "store_timeout_grace", "federation", "parallel_fanout",
                "CorpusCallosum",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How do per-store weight and floor settings affect RRF fusion ranking? weight floor rrf ranking per-store tuning fusion",
            expected_content=[
                "weight", "floor", "per-store", "RRF", "fusion",
                "rrf_k", "rrf_score_floor", "federation",
                "CorpusCallosum",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does the dynamic runtime configuration endpoint POST /configure work? configure dynamic runtime federation knobs k fusion_mode edges",
            expected_content=[
                "POST /configure", "dynamic runtime configuration", "federation knobs",
                "fusion_mode", "k", "authority_margin", "min_per_store",
                "edges_enabled", "n_results", "fetch_multiplier",
                "CorpusCallosum",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does the Seren constellation architecture integrate Loci, Memory, and SCC? constellation family left-brain right-brain callosum federation",
            expected_content=[
                "SerenLoci", "SerenMemory", "SerenCorpusCallosum",
                "left brain", "right brain", "callosum",
                "federation", "fan-out", "family",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does Loci's strict-supersede model work with fact history and audit trails? strict-supersede fact history superseded audit trail PARTIAL UNIQUE INDEX",
            expected_content=[
                "strict supersede", "fact history", "superseded",
                "PARTIAL UNIQUE INDEX", "live value", "audit trail",
                "project", "key", "SerenLoci",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does the Memory tier lifecycle work — promotion, aging, forgetting, and the pruned safety net? promotion aging forgetting safety-net pruned tiers lifecycle",
            expected_content=[
                "short-term", "near-term", "long-term", "consolidator",
                "promotion", "aging", "forgetting", "pruned",
                "safety net", "promote gate", "aging batch",
                "SerenMemory",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="What is the nano-floor ethos and how does it ensure graceful degradation without torch or GPU? nano-floor floor free no torch no gpu 4gb laptop graceful degradation",
            expected_content=[
                "nano-floor", "floor", "no torch", "no GPU", "4GB laptop",
                "graceful degradation", "FTS5", "lexical", "sqlite",
                "vector optional", "SerenLoci",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does service health checking work and what do the status bar indicators show? health-check status bar reachable unreachable ping retry",
            expected_content=[
                "health-check", "status bar", "reachable", "unreachable",
                "ping", "retry", "liveness",
                "SerenLoci", "SerenMemory", "SerenCorpusCallosum",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does bearer token security work with secret rotation and OS keychain storage? bearer token secret rotation keychain OS keychain token validation Meninges",
            expected_content=[
                "bearer token", "secret rotation", "OS keychain", "keychain",
                "token validation", "Meninges", "seren_meninges",
                "secret_rotation_days", "bearer auth",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How do the three Seren services wire together — Loci facts, Memory episodes, and SCC federation? cross-service integration wiring Loci Memory SCC federation fan-out",
            expected_content=[
                "SerenLoci", "SerenMemory", "SerenCorpusCallosum",
                "federation", "fan-out", "adapter", "cross-service",
                "RRF", "fusion", "recall",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does the draft review queue work — approve, reject with critique, and forced selection? draft review approve reject critique forced selection redraft queue",
            expected_content=[
                "draft review", "approve", "reject", "critique",
                "forced selection", "redraft", "draft queue",
                "consolidator", "draft chain", "pending",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="What is the Memory viewer UI — halls.html, tabs, read-only interface? memory viewer halls viewer tabs short near long search read-only",
            expected_content=[
                "memory viewer", "halls.html", "viewer tabs",
                "short-term", "near-term", "long-term", "search",
                "read-only", "SerenMemory",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does Loci's FTS5 lexical search work with BM25 ranking and Porter stemmer? FTS5 BM25 Porter stemmer lexical search tokenizer AND OR heuristic",
            expected_content=[
                "FTS5", "BM25", "Porter stemmer", "lexical search",
                "tokenizer", "AND OR heuristic", "fts5_virtual_table",
                "SerenLoci",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does memory consolidation cluster entries by topic and promote based on evidence? consolidation clustering topic evidence promotion promote gate cluster threshold",
            expected_content=[
                "consolidator", "clustering", "topic", "evidence",
                "promotion", "promote gate", "cluster_threshold",
                "short-term", "long-term", "draft",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does the SCC store registry and adapter architecture support multiple store types? store registry adapter architecture seren_loci seren_memory dispatch type",
            expected_content=[
                "store registry", "adapter", "seren_loci", "seren_memory",
                "dispatch", "type", "CorpusCallosum",
                "federation", "store_registry_max",
            ],
            source="corpus",
        ),
        EvalQuery(
            query="How does cross-service observability work with Prometheus metrics, OpenTelemetry tracing, and health checks? observability Prometheus OpenTelemetry tracing health-check metrics exporter",
            expected_content=[
                "Prometheus", "OpenTelemetry", "tracing", "health-check",
                "metrics exporter", "otel_service_name", "otel_sample_rate",
                "JSON logging", "alarm threshold",
            ],
            source="corpus",
        ),
    ])

    queries.extend(corpus_queries)

    return EvalDataset(
        name="seren-synthetic-eval",
        description=(
            f"Synthetic RAG evaluation set. "
            f"Loci: {LOCI_COUNT} facts, Memory: {SHORT_COUNT} short + {NEAR_COUNT} near + {LONG_COUNT} long."
        ),
        queries=queries,
    )


def load_dataset(path: str | Path) -> EvalDataset:
    """Convenience wrapper around EvalDataset.load."""
    return EvalDataset.load(path)


# ---------------------------------------------------------------------------
#  Export helpers
# ---------------------------------------------------------------------------

def export_synthetic_dataset_json(path: str | Path) -> None:
    """Generate the synthetic dataset (without live stores) and write JSON."""
    ds = seed_synthetic_dataset()
    ds.save(path)
    print(f"[serenprobe] Dataset exported to {Path(path).resolve()}")


def export_dataset_only() -> EvalDataset:
    """Return the synthetic dataset without requiring any stores.

    This is useful for pre-built dataset reuse or offline evaluation.
    """
    return seed_synthetic_dataset()