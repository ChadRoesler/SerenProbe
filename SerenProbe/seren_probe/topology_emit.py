"""
seren_probe.topology_emit — CompiledTopology -> docker-compose.yml.

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


def _build_or_image(kind: str, flags: list[str], image_overrides: dict, name: str) -> dict:
    """build: the shipped basic Dockerfile (default), OR a bring-your-own image:.
    Override keys checked most-specific-first: node name, then kind."""
    if name in image_overrides:
        return {"image": image_overrides[name]}
    if kind in image_overrides:
        return {"image": image_overrides[kind]}
    # Explicit LOWERCASE image tag alongside build: — otherwise compose derives
    # the image name as "<project>-<service>", which Docker rejects the moment a
    # node name has uppercase (repo names must be lowercase). Service/container
    # names + the corpus->store DNS wiring keep the user's casing (DNS resolves
    # case-insensitively); only the image tag is lowercased.
    # context "." resolves to the generated work_dir; write_compose copies the
    # Dockerfiles in beside the compose so the build context is self-contained.
    return {"image": f"seren-probe-{name.lower()}:local",
            "build": {"context": ".", "dockerfile": DOCKERFILE[kind],
                      "args": {"EXTRAS": _extras(kind, flags)}}}


def _healthcheck(port: int) -> dict:
    return {
        "test": ["CMD", "python", "-c",
                 f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/health')"],
        "interval": "3s", "timeout": "3s", "retries": 20,
    }


def _node_service(n: ResolvedNode, embedder: str, image_overrides: dict) -> tuple[str, dict]:
    short = "loci" if n.kind == "seren_loci" else "memory"
    env: dict[str, str] = {f"SEREN_{short.upper()}_PORT": str(n.port), "PYTHONUTF8": "1"}
    if n.kind == "seren_loci" and "vector" in n.flags:
        env["SEREN_LOCI_EMBEDDING_MODEL"] = embedder   # presence = the vector switch
    svc = {
        "container_name": n.name,
        "environment": dict(sorted(env.items())),
        "ports": [f"{n.port}:{n.port}"],          # host-published for the eval
        "healthcheck": _healthcheck(n.port),
    }
    svc.update(_build_or_image(n.kind, n.flags, image_overrides, n.name))
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


def _corpus_service(c: ResolvedCorpus, image_overrides: dict) -> tuple[str, dict]:
    svc = {
        "container_name": c.name,
        "environment": {"PYTHONUTF8": "1", "SEREN_SCC_CONFIG": "/etc/seren/seren-corpus-callosum.yaml"},
        "volumes": [f"./{c.name}.corpus.yaml:/etc/seren/seren-corpus-callosum.yaml:ro"],
        "ports": [f"{c.port}:{c.port}"],
        "depends_on": {s.name: {"condition": "service_healthy"} for s in c.stores},
        "healthcheck": _healthcheck(c.port),
    }
    svc.update(_build_or_image("corpus", c.flags, image_overrides, c.name))
    return c.name, svc


@dataclass
class EmittedCompose:
    compose: dict                    # the docker-compose.yml as a dict
    corpus_files: dict[str, dict]    # {"<corpus>.corpus.yaml": yaml-dict}


def emit_compose(topo: CompiledTopology, image_overrides: dict | None = None) -> EmittedCompose:
    image_overrides = image_overrides or {}
    port_of = {n.name: n.port for n in topo.loci + topo.memory}
    services: dict[str, dict] = {}
    for n in topo.loci + topo.memory:
        name, svc = _node_service(n, DEFAULT_EMBEDDER, image_overrides)
        services[name] = svc
    corpus_files: dict[str, dict] = {}
    for c in topo.corpus:
        name, svc = _corpus_service(c, image_overrides)
        services[name] = svc
        corpus_files[f"{c.name}.corpus.yaml"] = _corpus_yaml(c, port_of)
    compose = {
        "name": "seren-probe-target",
        "services": services,
        "networks": {"default": {"name": "seren-probe-net"}},
    }
    return EmittedCompose(compose=compose, corpus_files=corpus_files)
