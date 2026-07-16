# syntax=docker/dockerfile:1
# Basic SerenLoci image for Probe eval targets.
#
# EXTRAS (build-arg) selects opt-in features PER INSTANCE so the floor stays
# torch-free (Nano-floor ethos): a plain Loci is exact-key + FTS5 only; a
# 'vector' Loci gets [vector] (sqlite-vec + sentence-transformers); 'mcp' adds
# the MCP surface. The generated compose passes EXTRAS from the ProbeConfig flags.
#
# Bring your own: point the ProbeConfig's image_overrides at a prebuilt image
# (by node name or 'seren_loci' kind) to skip this build entirely.
#
# Host binds 0.0.0.0 by default; the port comes from SEREN_LOCI_PORT (set by
# the generated compose). Python <3.13 per seren-loci's torch/[vector] cap.
#
# NOTE: python:3.12-slim assumes wheels resolve for all deps. If a transitive
# dep ever needs compiling, either add build-essential here or bring your own
# image built FROM python:3.12 (non-slim).
# VERSION pins the published package, e.g. "==1.4.0". This is NOT cosmetic:
# `RUN pip install seren-loci` is a CACHED layer, so publishing a new version and
# rebuilding silently reuses the layer that installed the OLD one - the
# instruction text never changed. You then eval a build you think you upgraded
# and didn't. Pinning busts the cache AND records what was actually graded.
FROM python:3.12-slim
ARG EXTRAS=""
ARG VERSION=""
RUN pip install --no-cache-dir "seren-loci${EXTRAS}${VERSION}"
RUN python -c "import importlib.metadata as m; print('seren-loci', m.version('seren-loci'))"
CMD ["seren-loci"]
