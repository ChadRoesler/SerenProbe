"""
seren_probe.topology_emit - CompiledTopology -> docker-compose.yml.

Every Seren service reads config from mounted yaml + SEREN_*_* env; NONE takes a
port CLI flag. All three default host=0.0.0.0, so host-published ports reach them.

  Loci   : env SEREN_LOCI_PORT; 'vector' flag == SEREN_LOCI_EMBEDDING_MODEL
           (presence IS the switch) AND the [vector] build extra; 'mcp' -> [mcp].
  Memory : env SEREN_MEMORY_PORT; vector is CORE (no extra); 'mcp' -> [mcp].
  Corpus : mount a generated seren-corpus-callosum.yaml (federation.stores,
           container-DNS urls); 'mcp' -> [mcp].

Images: ship 3 BASIC Dockerfiles (seren_probe/dockerfiles/) that pip-install the
published packages; the EXTRAS build-arg selects opt-in features per instance so
the floor never pulls torch. Bring-your-own via image_overrides (by node name or
by kind) -> uses a prebuilt `image:` instead of building.

Two URL spaces (the silent breaker, handled here):
  - corpus->store wiring uses container-DNS  http://<service>:<port>   (in-compose)
  - the eval (host, in Probe) reaches everything via published host ports 127.0.0.1:<port>
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from .topology import CompiledTopology, ResolvedNode, ResolvedCorpus

DEFAULT_EMBEDDER = "all-MiniLM-L6-v2"   # what a 'vector' Loci gets unless overridden

DOCKERFILE = {
    "seren_loci":   "loci.Dockerfile",
    "seren_memory": "memory.Dockerfile",
    "corpus":       "corpus.Dockerfile",
}


def _svc_dns(name: str, port: int) -> str:
    """In-compose URL a corpus uses to reach a store: service-name DNS, not loopback."""
    return f"http://{name}:{port}"


def _extras(kind: str, flags: list[str]) -> str:
    """pip extras string for the EXTRAS build-arg, e.g. '[vector,mcp]' or ''.
    vector applies only to Loci (Memory's is core; SCC has none)."""
    parts = []
    if kind == "seren_loci" and "vector" in flags:
        parts.append("vector")
    if "mcp" in flags:
        parts.append("mcp")
    return f"[{','.join(parts)}]" if parts else ""


def _build_sig(kind: str, flags: list[str], version: str = "") -> str:
    """The identity of the IMAGE this service needs -- NOT the identity of the service.

    A build is determined entirely by (dockerfile, EXTRAS, VERSION). Two services with
    the same three are asking for a byte-identical image, and there is exactly one of it.

    This distinction was free to ignore on a 5-store topology and FATAL on a 113-store
    one. Every service used to get `image: seren-probe-<its own name>:local` PLUS its own
    `build:` block, so a 22-character realm asked BuildKit to build the same four images
    a hundred and thirteen times, in parallel -- resolving python:3.12-slim 113 times,
    pulling docker/dockerfile:1 113 times, exporting 113 near-identical manifests at once.
    The build session died and took every other target down with it:

        target loc_crossroads-mem: NotFound: forwarding Ping: no such job 84nwmmoe8i9...

    ...under sixty lines of CANCELED, which is what a cascade looks like from the outside.
    """
    short = {"seren_loci": "loci", "seren_memory": "memory", "corpus": "corpus"}[kind]
    extras = _extras(kind, flags).strip("[]").replace(",", "-")
    ver = re.sub(r"[^a-z0-9._-]+", "-", (version or "").lower()).strip("-.")
    return "-".join(p for p in (short, extras, ver) if p)


def _build_or_image(kind: str, flags: list[str], image_overrides: dict, name: str,
                    version: str = "", claimed: set | None = None) -> dict:
    """build: the shipped basic Dockerfile (default), OR a bring-your-own image:.
    Override keys checked most-specific-first: node name, then kind.

    ONE `build:` PER IMAGE SIGNATURE. The first service that needs a given image carries
    the build block; every other service with the same signature just names the image.
    Compose builds everything with a `build:` before it creates any container, so the
    other 109 find the image sitting there locally. Four builds, not a hundred and
    thirteen.

    The image tag is LOWERCASE and derived from the SIGNATURE, not the node name --
    otherwise compose derives "<project>-<service>", which Docker rejects the moment a
    node name has uppercase in it (repo names must be lowercase).
    context "." resolves to the generated work_dir; write_compose copies the Dockerfiles
    in beside the compose so the build context is self-contained.
    """
    if name in image_overrides:
        return {"image": image_overrides[name]}
    if kind in image_overrides:
        return {"image": image_overrides[kind]}

    sig = _build_sig(kind, flags, version)
    svc: dict = {"image": f"seren-probe-{sig}:local"}
    if claimed is None or sig not in claimed:
        if claimed is not None:
            claimed.add(sig)
        svc["build"] = {"context": ".", "dockerfile": DOCKERFILE[kind],
                        "args": {"EXTRAS": _extras(kind, flags), "VERSION": version}}
    return svc


# Every service that loads a model downloads it on FIRST boot. On a 5-store topology
# that is two downloads. On a 22-tenant realm it is 44 containers all reaching for the
# same all-MiniLM-L6-v2 at the same instant -- 44 identical downloads, against a rate-
# limited host, inside the health-check grace window. Give them ONE shared cache.
#
# Mounted at /root/.cache because that single path covers all three caches these images
# actually use: huggingface (~/.cache/huggingface), sentence-transformers
# (~/.cache/torch/...), and chroma's ONNX MiniLM (~/.cache/chroma). One volume, one
# mount, no per-library env plumbing to keep in sync.
#
# HONEST DISCLOSURE: on a COLD cache all of them miss at once and race to populate it.
# huggingface_hub takes file locks and is safe; chroma's onnx fetch is download-then-
# extract and is less obviously so. The 300s start_period gives a loser room to retry.
# If a first boot ever comes back with a half-written model, warm the cache with a
# single-node topology first -- that is the cure, not a smaller timeout.
MODEL_CACHE_VOLUME = "seren-probe-model-cache"
MODEL_CACHE_MOUNT = "/root/.cache"


def _healthcheck(port: int, slow_start: bool = False) -> dict:
    """Compose healthcheck for one service.

    start_period is LOAD-BEARING and its absence is a real bug we shipped: inside
    it, a failing check does NOT count toward `retries` and does NOT mark the
    container unhealthy. Without it, the clock starts the instant the container
    does - and a 'vector' Loci spends that budget DOWNLOADING its embedder from
    HuggingFace before uvicorn ever binds a socket. It would then come up perfectly
    healthy... some time after compose had already declared it dead and torn down
    every corpus that depends on it. (Observed: mycelium-loci-v answering /health
    in 0ms while compose insisted it was unhealthy.)

    So: a node that must fetch + load a model gets a long grace window; everything
    else gets a short one. The retries budget after that window is for a service
    that has genuinely broken, not one that is merely honest about being big.
    """
    return {
        "test": ["CMD", "python", "-c",
                 f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/health')"],
        "interval": "3s", "timeout": "3s", "retries": 20,
        # first boot pulls the embedder over the network; later boots hit the cache
        "start_period": "300s" if slow_start else "20s",
    }


def _node_service(n: ResolvedNode, embedder: str, image_overrides: dict,
                  versions: dict | None = None, claimed: set | None = None) -> tuple[str, dict]:
    short = "loci" if n.kind == "seren_loci" else "memory"
    env: dict[str, str] = {f"SEREN_{short.upper()}_PORT": str(n.port), "PYTHONUTF8": "1"}
    vector = n.kind == "seren_loci" and "vector" in n.flags
    if vector:
        env["SEREN_LOCI_EMBEDDING_MODEL"] = embedder   # presence = the vector switch
    svc = {
        "container_name": n.name,
        "environment": dict(sorted(env.items())),
        "ports": [f"{n.port}:{n.port}"],          # host-published for the eval
        # a vector node downloads + loads a sentence-transformers model before it
        # serves anything: it needs the long grace window, or compose kills it mid-download
        "healthcheck": _healthcheck(n.port, slow_start=vector),
    }
    # Anything that loads a model shares the cache. A vector Loci pulls the
    # sentence-transformers embedder; Memory pulls chroma's ONNX MiniLM. A lexical Loci
    # pulls nothing and gets no mount.
    if vector or n.kind == "seren_memory":
        svc["volumes"] = [f"{MODEL_CACHE_VOLUME}:{MODEL_CACHE_MOUNT}"]
    kind_key = "loci" if n.kind == "seren_loci" else "memory"
    svc.update(_build_or_image(n.kind, n.flags, image_overrides, n.name,
                               (versions or {}).get(kind_key, ""), claimed))
    return n.name, svc


def _corpus_yaml(c: ResolvedCorpus, port_of: dict[str, int]) -> dict:
    return {
        "server": {"host": "0.0.0.0", "port": c.port},
        "federation": {
            "stores": [
                {"name": s.name, "type": s.kind, "url": _svc_dns(s.name, port_of[s.name]),
                 "weight": s.weight, "floor": 0.0}
                for s in c.stores
            ]
        },
    }


def _corpus_service(c: ResolvedCorpus, image_overrides: dict,
                    versions: dict | None = None, claimed: set | None = None) -> tuple[str, dict]:
    svc = {
        "container_name": c.name,
        "environment": {"PYTHONUTF8": "1", "SEREN_SCC_CONFIG": "/etc/seren/seren-corpus-callosum.yaml"},
        "volumes": [f"./{c.name}.corpus.yaml:/etc/seren/seren-corpus-callosum.yaml:ro"],
        "ports": [f"{c.port}:{c.port}"],
        "depends_on": {s.name: {"condition": "service_healthy"} for s in c.stores},
        "healthcheck": _healthcheck(c.port),
    }
    svc.update(_build_or_image("corpus", c.flags, image_overrides, c.name,
                               (versions or {}).get("corpus", ""), claimed))
    return c.name, svc


@dataclass
class EmittedCompose:
    compose: dict                    # the docker-compose.yml as a dict
    corpus_files: dict[str, dict]    # {"<corpus>.corpus.yaml": yaml-dict}


def emit_compose(topo: CompiledTopology, image_overrides: dict | None = None) -> EmittedCompose:
    image_overrides = image_overrides or {}
    versions = getattr(topo, "versions", None) or {}
    port_of = {n.name: n.port for n in topo.loci + topo.memory}
    # Which image signatures already carry a `build:`. Shared across BOTH loops on
    # purpose: it is one set of images for the whole topology, not one per kind.
    claimed: set = set()
    services: dict[str, dict] = {}
    for n in topo.loci + topo.memory:
        name, svc = _node_service(n, DEFAULT_EMBEDDER, image_overrides, versions, claimed)
        services[name] = svc
    corpus_files: dict[str, dict] = {}
    for c in topo.corpus:
        name, svc = _corpus_service(c, image_overrides, versions, claimed)
        services[name] = svc
        corpus_files[f"{c.name}.corpus.yaml"] = _corpus_yaml(c, port_of)
    compose = {
        "name": "seren-probe-target",
        "services": services,
        "networks": {"default": {"name": "seren-probe-net"}},
        "volumes": {MODEL_CACHE_VOLUME: {}},
    }
    return EmittedCompose(compose=compose, corpus_files=corpus_files)
