# syntax=docker/dockerfile:1
# Basic SerenMemory image for Probe eval targets.
#
# NOTE on extras: sentence-transformers is a CORE dep of seren-memory (chroma
# named embedders / migration need it), so there is NO [vector] extra here —
# Memory always ships with embeddings. EXTRAS only ever adds [mcp].
#
# Bring your own: point the ProbeConfig's image_overrides at a prebuilt image
# (by node name or 'seren_memory' kind) to skip this build.
#
# Host binds 0.0.0.0 by default; the port comes from SEREN_MEMORY_PORT (set by
# the generated compose). Python <3.13 per seren-memory's chroma cap.
FROM python:3.12-slim
ARG EXTRAS=""
RUN pip install --no-cache-dir "seren-memory${EXTRAS}"
CMD ["seren-memory"]
