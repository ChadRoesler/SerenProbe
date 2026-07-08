"""
serenprobe.docker_env
════════════════════════════════════════════════════════════════════════

Manages the Docker lifecycle for Seren live-store test environments.

Provides:
  - ``DockerEnv`` context manager: build → start → wait-healthy → yield
    → stop → remove.  The caller runs evals inside the context.
  - ``launch_and_eval()``: one-shot "spin up, run full eval, tear down".
  - ``container_status()``: lightweight health check without managing
    the container's lifetime (useful when the container is started
    externally).

The image is expected to contain SerenMemory, SerenLoci, and
SerenCorpusCallosum — no eval dashboard.  Eval runs from here.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx
import yaml

logger = logging.getLogger(__name__)

# ── Config management ─────────────────────────────────────────────────
# Docker configs are stored as subdirectories under this path, each
# containing a Dockerfile and optional docker-compose.yml / metadata.
CONFIG_DIR = Path(os.environ.get("SERENPROBE_DOCKER_CONFIG_DIR",
                                 str(Path.home() / ".serenprobe" / "docker_configs")))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

@dataclass
class DockerDeployConfig:
    """A named Docker deployment configuration.

    Each config lives in its own subdirectory under CONFIG_DIR:
        <CONFIG_DIR>/<name>/
            Dockerfile          — required
            docker-compose.yml  — optional (overrides Dockerfile if present)
            metadata.json       — optional {description, tags, created_at}
    """
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    has_compose: bool = False
    valid: bool = False
    validation_errors: list[str] = field(default_factory=list)
    created_at: str = ""


def _discover_configs() -> list[DockerDeployConfig]:
    """Scan CONFIG_DIR for available deployment configs."""
    configs: list[DockerDeployConfig] = []
    if not CONFIG_DIR.is_dir():
        return configs
    for entry in sorted(CONFIG_DIR.iterdir()):
        if not entry.is_dir():
            continue
        df = entry / "Dockerfile"
        meta = entry / "metadata.json"
        compose = entry / "docker-compose.yml"
        cfg = DockerDeployConfig(name=entry.name)
        cfg.has_compose = compose.exists()
        cfg.valid = df.exists() or compose.exists()
        if not cfg.valid:
            cfg.validation_errors.append("Neither Dockerfile nor docker-compose.yml found")
        if meta.exists():
            try:
                import json as _json
                md = _json.loads(meta.read_text())
                cfg.description = md.get("description", "")
                cfg.tags = md.get("tags", [])
                cfg.created_at = md.get("created_at", "")
            except Exception:
                pass
        configs.append(cfg)
    return configs


def check_docker_available() -> dict:
    """Check if Docker is installed and responding.

    Returns dict with keys:
      - installed: bool
      - version: str (empty if not installed)
      - running: bool (whether the daemon is reachable)
      - error: str (if anything went wrong)
    """
    result = {"installed": False, "version": "", "running": False, "error": ""}
    try:
        raw = subprocess.run(
            ["docker", "--version"],
            capture_output=True, timeout=10,
        )
        if raw.returncode == 0:
            result["installed"] = True
            result["version"] = raw.stdout.decode().strip()
        else:
            result["error"] = raw.stderr.decode().strip()
            return result
    except FileNotFoundError:
        result["error"] = "docker command not found on PATH"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result

    # Check daemon is reachable
    try:
        ping = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, timeout=10,
        )
        if ping.returncode == 0:
            result["running"] = True
        else:
            result["running"] = False
            result["error"] = ping.stderr.decode().strip()
    except Exception as e:
        result["running"] = False
        result["error"] = str(e)

    return result


def validate_dockerfile(path: Path) -> tuple[bool, list[str]]:
    """Validate a Dockerfile by running docker build --check (if available)
    or falling back to a basic syntax parse.

    Returns (valid, errors_list).
    """
    if not path.exists():
        return False, ["File not found"]
    if not path.is_file():
        return False, ["Not a regular file"]

    errors: list[str] = []
    # Try the modern --check flag (BuildKit)
    try:
        check = subprocess.run(
            ["docker", "build", "--check", str(path.parent)],
            capture_output=True, timeout=60,
        )
        if check.returncode == 0:
            return True, []
        stderr = check.stderr.decode().strip()
        if stderr:
            errors.append(stderr)
        return False, errors
    except FileNotFoundError:
        errors.append("docker not available")
        return False, errors
    except Exception as e:
        errors.append(str(e))
        return False, errors


def validate_compose(path: Path) -> tuple[bool, list[str]]:
    """Validate a docker-compose.yml by running docker compose config."""
    if not path.exists():
        return False, ["File not found"]
    errors: list[str] = []
    try:
        check = subprocess.run(
            ["docker", "compose", "config", "--quiet", "-f", str(path)],
            capture_output=True, timeout=30,
        )
        if check.returncode == 0:
            return True, []
        stderr = check.stderr.decode().strip()
        if stderr:
            errors.append(stderr)
        return False, errors
    except FileNotFoundError:
        errors.append("docker not available")
        return False, errors
    except Exception as e:
        errors.append(str(e))
        return False, errors


def save_config(name: str, dockerfile_content: str,
                 compose_content: str = "",
                 description: str = "",
                 tags: list[str] | None = None) -> DockerDeployConfig:
    """Save a new Docker deployment config.

    Creates a subdirectory under CONFIG_DIR and writes the files.
    """
    config_dir = CONFIG_DIR / name
    config_dir.mkdir(parents=True, exist_ok=True)

    # Write Dockerfile
    (config_dir / "Dockerfile").write_text(dockerfile_content)

    # Write compose if provided
    compose_path = config_dir / "docker-compose.yml"
    if compose_content:
        compose_path.write_text(compose_content)
    elif compose_path.exists():
        compose_path.unlink()

    # Write metadata
    import json as _json
    from datetime import datetime
    meta = {
        "description": description,
        "tags": tags or [],
        "created_at": datetime.utcnow().isoformat(),
    }
    (config_dir / "metadata.json").write_text(_json.dumps(meta, indent=2))

    # Validate
    cfg = DockerDeployConfig(name=name, description=description,
                              tags=tags or [])
    cfg.has_compose = bool(compose_content)
    if cfg.has_compose:
        valid, errs = validate_compose(config_dir / "docker-compose.yml")
        cfg.valid = valid
        cfg.validation_errors = errs
    else:
        valid, errs = validate_dockerfile(config_dir / "Dockerfile")
        cfg.valid = valid
        cfg.validation_errors = errs
    cfg.created_at = meta["created_at"]
    return cfg


def get_config_path(name: str) -> Path | None:
    """Return the path to a saved config, or None if not found."""
    d = CONFIG_DIR / name
    if not d.is_dir():
        return None
    df = d / "Dockerfile"
    compose = d / "docker-compose.yml"
    if compose.exists():
        return compose
    if df.exists():
        return df
    return None


# ── Defaults ──────────────────────────────────────────────────────────
DEFAULT_IMAGE = "seren-live-stores"
DEFAULT_CONTAINER_NAME = "seren-probe-target"
DEFAULT_MEMORY_PORT = 7420
DEFAULT_LOCI_V_PORT = 7421
DEFAULT_LOCI_NV_PORT = 7422
DEFAULT_SCC_NV_PORT = 7423
DEFAULT_SCC_V_PORT = 7424

# How long to wait for all services to respond before giving up.
HEALTH_CHECK_TIMEOUT = 60.0
# Poll interval while waiting for services to become healthy.
HEALTH_CHECK_INTERVAL = 1.0


@dataclass
class DockerEnvState:
    """Snapshot of a running Docker environment — URLs the eval suite hits.

    All five service URLs are set when the container starts.  The NV
    (no-vector) and V (vector) variants are separate so the eval suite
    can compare side-by-side.
    """
    container_id: str = ""
    container_name: str = ""
    image: str = ""
    memory_url: str = ""
    loci_v_url: str = ""   # SerenLoci with vector (7421)
    loci_nv_url: str = ""  # SerenLoci no-vector (7422)
    scc_nv_url: str = ""   # SCC fans memory + loci-nv (7423)
    scc_v_url: str = ""    # SCC fans memory + loci-v  (7424)
    started_at: float = 0.0
    logs: list[str] = field(default_factory=list)


# ── Low-level Docker helpers ──────────────────────────────────────────

def _run_docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a docker CLI command and return the result. Raises on non-zero exit."""
    cmd = ["docker", *args]
    logger.debug("docker: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode().strip() or proc.stdout.decode().strip()
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {err}")
    return proc


def _container_ip(container_id: str) -> str:
    """Get the container's IP address from docker inspect."""
    raw = _run_docker(
        "inspect", container_id,
        "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
    )
    return raw.stdout.decode().strip()


def _find_free_port() -> int:
    """Ask the OS for a free TCP port (used when port mapping collides)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockport()[1]


# ── Health checks ─────────────────────────────────────────────────────

def _wait_for_service(url: str, label: str, timeout: float) -> str:
    """Poll GET /health until the service responds 200 or timeout expires.

    Returns the final health payload as a string on success; raises on
    timeout.
    """
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{url}/health", timeout=3.0)
            if r.status_code == 200:
                return r.text
        except Exception as e:
            last_err = str(e)
        time.sleep(HEALTH_CHECK_INTERVAL)
    raise TimeoutError(
        f"{label} at {url} did not become healthy in {timeout}s: {last_err}"
    )


def _wait_for_all_healthy(
    memory_url: str,
    loci_v_url: str,
    loci_nv_url: str,
    scc_nv_url: str,
    scc_v_url: str,
    timeout: float = HEALTH_CHECK_TIMEOUT,
) -> None:
    """Block until all five services respond to /health, or raise."""
    logger.info("Waiting for stores to become healthy ...")
    _wait_for_service(memory_url, "SerenMemory", timeout)
    logger.info("  ✓ SerenMemory             at %s", memory_url)
    _wait_for_service(loci_v_url, "SerenLoci-Vector", timeout)
    logger.info("  ✓ SerenLoci-Vector        at %s", loci_v_url)
    _wait_for_service(loci_nv_url, "SerenLoci", timeout)
    logger.info("  ✓ SerenLoci               at %s", loci_nv_url)
    _wait_for_service(scc_nv_url, "SerenCorpusCallosum", timeout)
    logger.info("  ✓ SerenCorpusCallosum     at %s", scc_nv_url)
    _wait_for_service(scc_v_url, "SerenCorpusCallosum-Vector", timeout)
    logger.info("  ✓ SerenCorpusCallosum-Vector at %s", scc_v_url)
    logger.info("All stores healthy.")


# ── Container lifecycle ───────────────────────────────────────────────

def build_image(
    image: str = DEFAULT_IMAGE,
    dockerfile_dir: Optional[str] = None,
    build_args: Optional[dict[str, str]] = None,
) -> str:
    """Build the Docker image.  Returns the image name.

    If *dockerfile_dir* is None, the module looks for ``Dockerfile``
    relative to the package root (``serenprobe/..``).  Pass an explicit
    path to point at a custom Dockerfile location.
    """
    if dockerfile_dir is None:
        # Default: look next to the serenprobe package (repo root).
        dockerfile_dir = str(Path(__file__).resolve().parent.parent)

    cmd = ["build", "-t", image, dockerfile_dir]
    if build_args:
        for k, v in build_args.items():
            cmd += ["--build-arg", f"{k}={v}"]

    _run_docker(*cmd, timeout=300)
    logger.info("Built image %s from %s", image, dockerfile_dir)
    return image


def start_container(
    image: str = DEFAULT_IMAGE,
    container_name: str = DEFAULT_CONTAINER_NAME,
    memory_port: int = DEFAULT_MEMORY_PORT,
    loci_v_port: int = DEFAULT_LOCI_V_PORT,
    loci_nv_port: int = DEFAULT_LOCI_NV_PORT,
    scc_nv_port: int = DEFAULT_SCC_NV_PORT,
    scc_v_port: int = DEFAULT_SCC_V_PORT,
) -> DockerEnvState:
    """Start a container from *image* and map its ports to the host.

    Returns a ``DockerEnvState`` with the container ID and the
    ``http://127.0.0.1:<port>`` URLs for each service.  The container
    is not yet guaranteed healthy — call ``wait_for_healthy()`` or use
    ``DockerEnv`` which does both.
    """
    port_args = [
        "-p", f"{memory_port}:{memory_port}",
        "-p", f"{loci_v_port}:{loci_v_port}",
        "-p", f"{loci_nv_port}:{loci_nv_port}",
        "-p", f"{scc_nv_port}:{scc_nv_port}",
        "-p", f"{scc_v_port}:{scc_v_port}",
    ]

    raw = _run_docker(
        "run", "-d",
        "--name", container_name,
        *port_args,
        image,
    )
    cid = raw.stdout.decode().strip()

    state = DockerEnvState(
        container_id=cid,
        container_name=container_name,
        image=image,
        memory_url=f"http://127.0.0.1:{memory_port}",
        loci_v_url=f"http://127.0.0.1:{loci_v_port}",
        loci_nv_url=f"http://127.0.0.1:{loci_nv_port}",
        scc_nv_url=f"http://127.0.0.1:{scc_nv_port}",
        scc_v_url=f"http://127.0.0.1:{scc_v_port}",
        started_at=time.time(),
    )
    logger.info("Started container %s (%s) from %s", container_name, cid[:12], image)
    return state


def wait_for_healthy(state: DockerEnvState, timeout: float = HEALTH_CHECK_TIMEOUT) -> DockerEnvState:
    """Block until all five services inside *state* respond to /health.

    Populates ``state.logs`` with any startup log lines captured from
    the container.  Returns *state* for chaining.
    """
    _wait_for_all_healthy(
        state.memory_url,
        state.loci_v_url,
        state.loci_nv_url,
        state.scc_nv_url,
        state.scc_v_url,
        timeout=timeout,
    )
    # Grab a few lines of container logs for diagnostics.
    try:
        raw = _run_docker("logs", "--tail", "10", state.container_id)
        state.logs = raw.stdout.decode().strip().splitlines()
    except Exception:
        pass
    return state


def stop_container(state: DockerEnvState, remove: bool = True) -> None:
    """Stop the container and optionally remove it.

    Always best-effort: logs any Docker errors but never raises, so the
    caller can safely call this in a ``finally`` block.
    """
    cid = state.container_id
    if not cid:
        return
    try:
        _run_docker("stop", cid, timeout=30)
        logger.info("Stopped container %s", cid[:12])
    except Exception as e:
        logger.warning("Error stopping container: %s", e)

    if remove:
        try:
            _run_docker("rm", cid, timeout=30)
            logger.info("Removed container %s", cid[:12])
        except Exception as e:
            logger.warning("Error removing container: %s", e)


def container_status(container_name: str = DEFAULT_CONTAINER_NAME) -> dict:
    """Return the status of a container by name, without managing it.

    Useful when the container is started externally (e.g. by a CI job or
    the user's own docker-compose).  Returns a dict with keys:
      - exists: bool
      - running: bool
      - id: str (short)
      - ports: dict or None
      - error: str (if inspect failed)
    """
    result: dict = {"exists": False, "running": False, "id": "", "ports": None}
    try:
        raw = _run_docker(
            "inspect", container_name,
            "--format", "{{.Id}} {{.State.Status}}",
        )
        out = raw.stdout.decode().strip()
        parts = out.split(None, 1)
        if len(parts) == 2:
            result["id"] = parts[0][:12]
            result["exists"] = True
            result["running"] = parts[1] == "running"
        else:
            result["error"] = f"unexpected inspect output: {out}"
            return result

        # Port mappings
        try:
            pr = _run_docker(
                "inspect", container_name,
                "--format", "{{json .NetworkSettings.Ports}}",
            )
            import json as _json
            ports_raw = pr.stdout.decode().strip()
            if ports_raw and ports_raw != "null" and ports_raw != "{}":
                result["ports"] = _json.loads(ports_raw)
        except Exception:
            result["ports"] = None
    except RuntimeError as e:
        result["error"] = str(e)
    return result


# ── Context manager ───────────────────────────────────────────────────

class DockerEnv:
    """Context manager that builds (if needed), starts, and tears down a
    Docker test environment.

    Usage::

        with DockerEnv() as env:
            # env.state has .memory_url, .loci_v_url, .loci_nv_url,
            # .scc_nv_url, .scc_v_url
            results = run_live_evaluation(
                memory_url=env.state.memory_url,
                loci_nv_url=env.state.loci_nv_url,
                loci_v_url=env.state.loci_v_url,
                scc_nv_url=env.state.scc_nv_url,
                scc_v_url=env.state.scc_v_url,
            )

    On exit the container is stopped and removed regardless of errors.
    """

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        container_name: str = DEFAULT_CONTAINER_NAME,
        memory_port: int = DEFAULT_MEMORY_PORT,
        loci_v_port: int = DEFAULT_LOCI_V_PORT,
        loci_nv_port: int = DEFAULT_LOCI_NV_PORT,
        scc_nv_port: int = DEFAULT_SCC_NV_PORT,
        scc_v_port: int = DEFAULT_SCC_V_PORT,
        auto_build: bool = True,
        build_dir: Optional[str] = None,
        health_timeout: float = HEALTH_CHECK_TIMEOUT,
    ):
        self.image = image
        self.container_name = container_name
        self.memory_port = memory_port
        self.loci_v_port = loci_v_port
        self.loci_nv_port = loci_nv_port
        self.scc_nv_port = scc_nv_port
        self.scc_v_port = scc_v_port
        self.auto_build = auto_build
        self.build_dir = build_dir
        self.health_timeout = health_timeout
        self.state: DockerEnvState | None = None

    def __enter__(self) -> DockerEnvState:
        if self.auto_build:
            build_image(self.image, dockerfile_dir=self.build_dir)

        state = start_container(
            image=self.image,
            container_name=self.container_name,
            memory_port=self.memory_port,
            loci_v_port=self.loci_v_port,
            loci_nv_port=self.loci_nv_port,
            scc_nv_port=self.scc_nv_port,
            scc_v_port=self.scc_v_port,
        )
        wait_for_healthy(state, timeout=self.health_timeout)
        self.state = state
        return state

    def __exit__(self, *exc_info) -> None:
        if self.state is not None:
            stop_container(self.state, remove=True)
            self.state = None


# ── One-shot convenience ──────────────────────────────────────────────

def launch_and_eval(
    *,
    image: str = DEFAULT_IMAGE,
    container_name: str = DEFAULT_CONTAINER_NAME,
    memory_port: int = DEFAULT_MEMORY_PORT,
    loci_v_port: int = DEFAULT_LOCI_V_PORT,
    loci_nv_port: int = DEFAULT_LOCI_NV_PORT,
    scc_nv_port: int = DEFAULT_SCC_NV_PORT,
    scc_v_port: int = DEFAULT_SCC_V_PORT,
    build_dir: Optional[str] = None,
    health_timeout: float = HEALTH_CHECK_TIMEOUT,
    run_locomo: bool = False,
    run_longmem: bool = False,
    seed_first: bool = False,
) -> dict:
    """One-shot: build → start → seed → eval → stop → remove.

    Returns the full eval results dict (same shape as ``run_live_evaluation``).

    This is the primary integration point for CI::

        report = launch_and_eval(seed_first=True)
        print(report["aggregate"])
    """
    from .live_eval import run_live_evaluation

    with DockerEnv(
        image=image,
        container_name=container_name,
        memory_port=memory_port,
        loci_v_port=loci_v_port,
        loci_nv_port=loci_nv_port,
        scc_nv_port=scc_nv_port,
        scc_v_port=scc_v_port,
        auto_build=True,
        build_dir=build_dir,
        health_timeout=health_timeout,
    ) as state:
        memory_url = state.memory_url
        loci_nv_url = state.loci_nv_url
        loci_v_url = state.loci_v_url
        scc_nv_url = state.scc_nv_url
        scc_v_url = state.scc_v_url

        # Seed synthetic data if requested.
        if seed_first:
            logger.info("Seeding synthetic data ...")
            from .dataset import seed_synthetic_dataset
            seed_synthetic_dataset(
                memory_url=memory_url,
                loci_url=loci_nv_url,
            )

        # Run the full eval suite — NV and V variants go to separate stores.
        logger.info("Running evaluation ...")
        results = run_live_evaluation(
            memory_url=memory_url,
            loci_nv_url=loci_nv_url,
            loci_v_url=loci_v_url,
            scc_nv_url=scc_nv_url,
            scc_v_url=scc_v_url,
        )

        if run_longmem:
            logger.warning("LongMemEval not yet implemented — skipping")
        if run_locomo:
            logger.warning("LoCoMo sweep not yet implemented — skipping")

    return results


# ── Topology-driven lifecycle (compile → emit → compose up → health-gate) ──
# Supersedes the fixed-5-store single-image path above: the compiler assigns
# every port, the emitter bakes correct-by-construction corpus→store wiring into
# each corpus's yaml, and we `docker compose up` the whole declared
# constellation. The old _find_free_port remap is obsolete under this model —
# ports are declared, not discovered.

@dataclass
class TopologyEnvState:
    """A spun-up topology: the host URLs the eval hits + teardown handles."""
    project_name: str
    work_dir: str
    url_of: dict[str, str]                 # store name -> host URL (127.0.0.1:port)
    loci: list[str] = field(default_factory=list)
    memory: list[str] = field(default_factory=list)
    corpus: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)


def host_url_map(topology) -> dict[str, str]:
    """Every store's HOST-published URL — what the eval (on the host) hits.
    Deterministic from the compiled ports; no container inspection needed.
    (Distinct from the container-DNS URLs baked into the corpus yamls.)"""
    out: dict[str, str] = {}
    for n in topology.loci + topology.memory:
        out[n.name] = f"http://127.0.0.1:{n.port}"
    for c in topology.corpus:
        out[c.name] = f"http://127.0.0.1:{c.port}"
    return out


def topology_work_dir(project_name: str) -> Path:
    """Stable per-project scratch dir (must persist between up and down)."""
    return Path(tempfile.gettempdir()) / f"serenprobe-{project_name}"


def write_compose(emitted, work_dir: Path) -> Path:
    """Write the compose + each corpus's seren-corpus-callosum.yaml to disk."""
    work_dir.mkdir(parents=True, exist_ok=True)
    compose_path = work_dir / "docker-compose.yml"
    compose_path.write_text(yaml.safe_dump(emitted.compose, sort_keys=False), encoding="utf-8")
    for fn, doc in emitted.corpus_files.items():
        (work_dir / fn).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    # Copy the shipped basic Dockerfiles in beside the compose so the build
    # context (".") is self-contained. image: overrides that skip build don't
    # need them; copying is harmless either way.
    import shutil
    dockerfiles = Path(__file__).parent / "dockerfiles"
    if dockerfiles.is_dir():
        for df in dockerfiles.glob("*.Dockerfile"):
            shutil.copy2(df, work_dir / df.name)
    return compose_path


def compose_up(work_dir: Path, project_name: str, build: bool = True) -> None:
    args = ["compose", "-p", project_name, "-f", str(work_dir / "docker-compose.yml"), "up", "-d"]
    if build:
        args.append("--build")
    _run_docker(*args, timeout=600)


def compose_down(work_dir, project_name: str) -> None:
    """Best-effort teardown; never raises (safe in a finally / shutdown)."""
    try:
        _run_docker("compose", "-p", project_name,
                    "-f", str(Path(work_dir) / "docker-compose.yml"), "down", "-v", timeout=120)
    except Exception as e:
        logger.warning("compose down: %s", e)


def wait_for_topology_healthy(url_of: dict[str, str], timeout: float = HEALTH_CHECK_TIMEOUT) -> None:
    """Health-gate EVERY store (N, not hardcoded 5) on its host port."""
    for name, url in url_of.items():
        _wait_for_service(url, name, timeout)


def spin_up_topology(topology, project_name: str = DEFAULT_CONTAINER_NAME,
                     build: bool = True, health_timeout: float = HEALTH_CHECK_TIMEOUT,
                     image_overrides: dict | None = None) -> "TopologyEnvState":
    """emit -> write -> compose up -> health-gate -> state. `topology` is an
    already-compiled CompiledTopology (compile at the route so errors surface to
    the operator before Docker ever runs). image_overrides (node-name or kind ->
    prebuilt image) skips the build for those services (bring-your-own)."""
    from .topology_emit import emit_compose
    emitted = emit_compose(topology, image_overrides=image_overrides)
    work_dir = topology_work_dir(project_name)
    write_compose(emitted, work_dir)
    compose_up(work_dir, project_name, build=build)
    url_of = host_url_map(topology)
    wait_for_topology_healthy(url_of, timeout=health_timeout)
    logs: list[str] = []
    try:
        raw = _run_docker("compose", "-p", project_name,
                          "-f", str(work_dir / "docker-compose.yml"), "logs", "--tail", "20", timeout=30)
        logs = raw.stdout.decode(errors="replace").strip().splitlines()[-20:]
    except Exception:
        pass
    return TopologyEnvState(project_name=project_name, work_dir=str(work_dir), url_of=url_of,
                            loci=[n.name for n in topology.loci],
                            memory=[n.name for n in topology.memory],
                            corpus=[c.name for c in topology.corpus], logs=logs)
