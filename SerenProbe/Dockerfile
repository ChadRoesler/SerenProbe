# ═══════════════════════════════════════════════════════════════════════
#  Dockerfile — Seren Live Store Environment
#
#  Five services in one image, pre-configured to talk to each other:
#
#     7420  SerenMemory              (right brain — 3-tier chroma)
#     7421  SerenLoci-Vector         (left brain — exact + FTS5 + vector)
#     7422  SerenLoci                (left brain — exact + FTS5 only)
#     7423  SerenCorpusCallosum      (callosum — fans memory + loci-nv)
#     7424  SerenCorpusCallosum-Vector (callosum — fans memory + loci-vector)
#
#  SerenProbe (or any client) connects from the host or another container.
#  The vector instances use sqlite-vec + sentence-transformers so the
#  associative "this smells like that CUDA thing" jump works.
#
#  USAGE
#    docker build -t seren-live-stores .
#    docker run -d \
#        -p 7420:7420 -p 7421:7421 -p 7422:7422 \
#        -p 7423:7423 -p 7424:7424 \
#        --name seren-test  seren-live-stores
#
#  Then point SerenProbe at the mapped ports and run evaluations.
#
#  To seed data before evaluating:
#    docker exec seren-test python -m serenprobe.runner --seed-only
# ═══════════════════════════════════════════════════════════════════════

FROM python:3.11-slim AS base

# ── Install system deps ──────────────────────────────────────────────
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        curl \
        git \
        && \
    rm -rf /var/lib/apt/lists/*

# ── Create the seren user ────────────────────────────────────────────
RUN groupadd -r seren -g 999 && \
    useradd -r -g seren -u 999 -m -d /home/seren -s /bin/bash seren && \
    chmod 0775 /home/seren

# ── Install SerenMemory (port 7420, right brain) ─────────────────────
RUN python3 -m venv /home/seren/venvs/memory && \
    /home/seren/venvs/memory/bin/pip install --no-cache-dir \
        "seren-memory" && \
    /home/seren/venvs/memory/bin/pip check

# ── Install SerenLoci-Vector (port 7421, with associative finder) ────
# Separate venv so the [vector] deps (torch, sentence-transformers)
# don't leak into the NV instance.
RUN python3 -m venv /home/seren/venvs/loci-vector && \
    /home/seren/venvs/loci-vector/bin/pip install --no-cache-dir \
        "seren-loci[vector]" && \
    /home/seren/venvs/loci-vector/bin/pip check

# ── Install SerenLoci (port 7422, embedding-free floor) ──────────────
RUN python3 -m venv /home/seren/venvs/loci && \
    /home/seren/venvs/loci/bin/pip install --no-cache-dir \
        "seren-loci" && \
    /home/seren/venvs/loci/bin/pip check

# ── Install SerenCorpusCallosum × 2 ──────────────────────────────────
# Both NV (7423) and Vector (7424) use the same package — the
# difference is only which stores the YAML points to.  One venv is
# enough; we launch two processes from it with different configs.
RUN python3 -m venv /home/seren/venvs/callosum && \
    /home/seren/venvs/callosum/bin/pip install --no-cache-dir \
        "seren-corpus-callosum" && \
    /home/seren/venvs/callosum/bin/pip check

# ── Write config files ───────────────────────────────────────────────
RUN mkdir -p /home/seren/config && chown -R seren:seren /home/seren/config

# seren-memory.yaml
RUN cat > /home/seren/config/seren-memory.yaml <<'CFG_EOF'
server:
  host: 0.0.0.0
  port: 7420
  bearer_token: ""
storage:
  persist_dir: /home/seren/data/memory
lifetimes:
  short_term_seconds: 691200
  near_term_review_seconds: 2592000
consolidator:
  enabled: false
CFG_EOF

# seren-loci-vector.yaml — vector finder enabled
RUN cat > /home/seren/config/seren-loci-vector.yaml <<'CFG_EOF'
server:
  host: 0.0.0.0
  port: 7421
  bearer_token: ""
storage:
  db_path: /home/seren/data/loci-vector/loci.db
  embedding_model: sentence-transformers/all-MiniLM-L6-v2
  embedding_device: cpu
CFG_EOF

# seren-loci.yaml — nano floor, no vector
RUN cat > /home/seren/config/seren-loci.yaml <<'CFG_EOF'
server:
  host: 0.0.0.0
  port: 7422
  bearer_token: ""
storage:
  db_path: /home/seren/data/loci/loci.db
  embedding_model: null
CFG_EOF

# seren-corpus-callosum.yaml — fans memory + loci-nv (7422)
RUN cat > /home/seren/config/seren-corpus-callosum.yaml <<'CFG_EOF'
server:
  host: 0.0.0.0
  port: 7423
  bearer_token: ""
tls:
  trust_system_store: false
federation:
  k: 60
  n_results: 10
  fetch_multiplier: 2
  per_store_timeout_s: 5.0
  fusion_mode: rrf
  authority_margin: 0.035
  min_per_store: 1
  edges_enabled: true
  edge_budget: 3
  stores:
    - name: memory
      type: seren_memory
      url: http://127.0.0.1:7420
      weight: 1.0
      floor: 0.0
    - name: loci
      type: seren_loci
      url: http://127.0.0.1:7422
      weight: 1.0
      floor: 0.0
CFG_EOF

# seren-corpus-callosum-vector.yaml — fans memory + loci-vector (7421)
RUN cat > /home/seren/config/seren-corpus-callosum-vector.yaml <<'CFG_EOF'
server:
  host: 0.0.0.0
  port: 7424
  bearer_token: ""
tls:
  trust_system_store: false
federation:
  k: 60
  n_results: 10
  fetch_multiplier: 2
  per_store_timeout_s: 5.0
  fusion_mode: rrf
  authority_margin: 0.035
  min_per_store: 1
  edges_enabled: true
  edge_budget: 3
  stores:
    - name: memory
      type: seren_memory
      url: http://127.0.0.1:7420
      weight: 1.0
      floor: 0.0
    - name: loci-vector
      type: seren_loci
      url: http://127.0.0.1:7421
      weight: 1.0
      floor: 0.0
CFG_EOF

# ── Data directories ──────────────────────────────────────────────────
RUN mkdir -p /home/seren/data/memory && \
    mkdir -p /home/seren/data/loci && \
    mkdir -p /home/seren/data/loci-vector && \
    chown -R seren:seren /home/seren/data

# ── Startup script ────────────────────────────────────────────────────
# Launches all five services in the background.  On SIGTERM/SIGINT it
# forwards the signal to all children so a docker stop cleans up cleanly.
RUN cat > /home/seren/start.sh <<'START_EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "==> Starting SerenMemory on :7420 ..."
/home/seren/venvs/memory/bin/python -m seren_memory \
    --config /home/seren/config/seren-memory.yaml &
MEMORY_PID=$!

sleep 1

echo "==> Starting SerenLoci-Vector on :7421 ..."
/home/seren/venvs/loci-vector/bin/python -m seren_loci \
    --config /home/seren/config/seren-loci-vector.yaml &
LOCI_V_PID=$!

sleep 1

echo "==> Starting SerenLoci on :7422 ..."
/home/seren/venvs/loci/bin/python -m seren_loci \
    --config /home/seren/config/seren-loci.yaml &
LOCI_NV_PID=$!

sleep 1

echo "==> Starting SerenCorpusCallosum on :7423 ..."
/home/seren/venvs/callosum/bin/python -m seren_corpus_callosum \
    --config /home/seren/config/seren-corpus-callosum.yaml &
SCC_NV_PID=$!

sleep 1

echo "==> Starting SerenCorpusCallosum-Vector on :7424 ..."
/home/seren/venvs/callosum/bin/python -m seren_corpus_callosum \
    --config /home/seren/config/seren-corpus-callosum-vector.yaml &
SCC_V_PID=$!

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Seren Live Store Environment                            ║"
echo "║                                                          ║"
echo "║  Memory  :7420   Loci-V  :7421   Loci  :7422             ║"
echo "║  SCC-NV  :7423   SCC-V   :7424                           ║"
echo "║                                                          ║"
echo "║  All stores ready for evaluation.                        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

trap 'echo "Shutting down..."; \
    kill $MEMORY_PID $LOCI_V_PID $LOCI_NV_PID $SCC_NV_PID $SCC_V_PID 2>/dev/null; \
    exit 0' SIGTERM SIGINT

wait -n
kill $MEMORY_PID $LOCI_V_PID $LOCI_NV_PID $SCC_NV_PID $SCC_V_PID 2>/dev/null
START_EOF
RUN chmod +x /home/seren/start.sh

# ── Finalize ──────────────────────────────────────────────────────────
RUN chown -R seren:seren /home/seren

USER seren
WORKDIR /home/seren

EXPOSE 7420 7421 7422 7423 7424

CMD ["/home/seren/start.sh"]
