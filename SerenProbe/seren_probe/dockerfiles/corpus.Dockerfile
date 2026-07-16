# syntax=docker/dockerfile:1
# Basic SerenCorpusCallosum image for Probe eval targets.
#
# The callosum is embedder-agnostic (RRF over ranks, never magnitudes) - it
# NEVER pulls torch. There is no [vector] extra; EXTRAS only adds [mcp].
# No Python upper bound (nothing in the tree walls at 3.13).
#
# Config (federation.stores) is mounted at
# /etc/seren/seren-corpus-callosum.yaml by the generated compose (SEREN_SCC_CONFIG
# points at it), with container-DNS store URLs baked in - the corpus->store
# wiring is declared, never POSTed, so the SCC->Loci landmine can't form.
#
# Bring your own via image_overrides (by node name or 'corpus' kind).
#
# VERSION is load-bearing, not cosmetic. `RUN pip install seren-corpus-callosum`
# is a CACHED layer: publish a new version to PyPI and rebuild, and Docker happily
# reuses the layer that installed the OLD one - the instruction text never changed.
# You then eval against a package you think you upgraded and didn't. (Observed: an
# SCC that had `hops` on PyPI, and a container that had never heard of it.)
# Pinning both busts the cache AND puts the tested version on the record - an eval
# harness that can't say which build it graded isn't an eval harness.
FROM python:3.12-slim
ARG EXTRAS=""
ARG VERSION=""
RUN pip install --no-cache-dir "seren-corpus-callosum${EXTRAS}${VERSION}"
RUN python -c "import importlib.metadata as m; print('seren-corpus-callosum', m.version('seren-corpus-callosum'))"
CMD ["seren-corpus-callosum"]
