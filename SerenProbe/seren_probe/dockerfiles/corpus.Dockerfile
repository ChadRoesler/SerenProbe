# syntax=docker/dockerfile:1
# Basic SerenCorpusCallosum image for Probe eval targets.
#
# The callosum is embedder-agnostic (RRF over ranks, never magnitudes) — it
# NEVER pulls torch. There is no [vector] extra; EXTRAS only adds [mcp].
# No Python upper bound (nothing in the tree walls at 3.13).
#
# Config (federation.stores) is mounted at
# /etc/seren/seren-corpus-callosum.yaml by the generated compose (SEREN_SCC_CONFIG
# points at it), with container-DNS store URLs baked in — the corpus->store
# wiring is declared, never POSTed, so the SCC->Loci landmine can't form.
#
# Bring your own via image_overrides (by node name or 'corpus' kind).
FROM python:3.12-slim
ARG EXTRAS=""
RUN pip install --no-cache-dir "seren-corpus-callosum${EXTRAS}"
CMD ["seren-corpus-callosum"]
