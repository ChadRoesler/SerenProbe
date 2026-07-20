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

# BAKE THE EMBEDDER INTO THE IMAGE. A vector Loci must not touch the network to boot.
#
# The runtime path was: try HuggingFace, and on failure fall back to downloading the
# model from a GitHub release. Neither branch ever populated the shared cache volume --
# proven by inspecting it after a full night of boots: /root/.cache/huggingface held
# 12MB of Xet STAGING directories and no hub/ tree at all, while the Memory containers'
# chroma ONNX model sat next to it fully cached at 166MB. So every vector Loci
# re-downloaded ~90MB on EVERY boot, and the cache that was supposed to prevent that
# had never once been written by a Loci.
#
# At five stores that is slow. At twenty-two it is fatal: they all reach at once, from
# one source IP, inside the health-check grace window, and compose tears down every
# corpus depending on them. The failure scales with topology width, so it gets worse
# exactly as the eval gets more interesting.
#
# Baking it removes the class of bug rather than tuning it: no cache to miss, no rate
# limit to hit, no stampede to stagger, no grace window to widen, and it works
# airgapped. Same discipline as pinning the Jetson wheels instead of resolving them at
# runtime -- fetch the artifact once, at build time, where a failure is loud and cheap.
#
# ARG-gated so a lexical Loci stays torch-free and small (the Nano-floor ethos): this
# only runs when EXTRAS actually asked for [vector].
#
# BAKED OUTSIDE /root/.cache ON PURPOSE. The compose mounts the shared cache volume at
# /root/.cache, and a named volume that ALREADY has content does not get seeded from
# the image -- it SHADOWS it. Baking to /root/.cache would therefore be invisible at
# runtime behind the very mount it exists to make unnecessary, and would look like the
# bake silently didn't work. /opt/seren-models is image-owned and nothing mounts over
# it, so what is built is what runs.
#
# FETCHED FROM THE SERENLOCI RELEASE, NOT FROM HUGGINGFACE. Pulling the embedder from
# HF at build time hits the same wall as pulling it at runtime -- anonymous per-IP rate
# limiting, plus the Xet transfer layer, which between them left a night of boots with
# 12MB of staging directories and no model. The release tarball is a plain asset on a
# CDN: no auth, no rate limit, no Xet, and PINNED -- the eval knows exactly which
# weights it graded against, which HF's floating model ids never guaranteed.
#
# urllib + tarfile rather than curl/wget: python:3.12-slim ships neither, and adding
# them is a layer and an apt cache for something the interpreter already does.
ARG EMBEDDER="all-MiniLM-L6-v2"
ARG EMBEDDER_URL="https://github.com/ChadRoesler/SerenLoci/releases/download/v1.3.1/all-MiniLM-L6-v2.tar.gz"
ENV SEREN_MODEL_DIR=/opt/seren-models \
    HF_HUB_OFFLINE=1
RUN echo "EXTRAS=[$EXTRAS]" && case "$EXTRAS" in \
      *vector*) python -c "\
import urllib.request, tarfile, io, os, shutil; \
os.makedirs('/opt/seren-models', exist_ok=True); \
print('fetching ${EMBEDDER_URL}'); \
blob=urllib.request.urlopen('${EMBEDDER_URL}', timeout=300).read(); \
print('got', len(blob), 'bytes'); \
tarfile.open(fileobj=io.BytesIO(blob)).extractall('/opt/seren-models'); \
d=os.path.join('/opt/seren-models', '${EMBEDDER}'); \
[shutil.rmtree(os.path.join(d, x), ignore_errors=True) for x in ('onnx', 'openvino')]; \
[os.remove(os.path.join(d, x)) for x in ('pytorch_model.bin', 'rust_model.ot', 'tf_model.h5', 'train_script.py') if os.path.exists(os.path.join(d, x))]; \
print('baked embedder ${EMBEDDER} ->', sorted(os.listdir(d)))" ;; \
      *) echo 'lexical build - no embedder baked' ;; \
    esac

# PRUNED IN THE SAME LAYER, not a later one. The release tarball ships every weight
# format the model was ever published in -- onnx (498MB), openvino (113MB),
# pytorch_model.bin, rust_model.ot and tf_model.h5 (~90MB each) -- roughly 1GB, of
# which sentence-transformers reads exactly ONE: model.safetensors. Deleting them in a
# separate RUN would not help at all: layers are append-only, so the bytes stay in the
# image and the delete just adds a whitespace marker on top. Same layer or nothing.
#
# What is kept is the full sentence-transformers contract: modules.json, 1_Pooling/,
# config_sentence_transformers.json, sentence_bert_config.json, the tokenizer set, and
# model.safetensors. That is a loadable local model directory, which is what the
# runtime env points at.

CMD ["seren-loci"]
