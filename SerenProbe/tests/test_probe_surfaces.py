"""
The surfaces nobody tested: the MCP tools, the dead Docker route, the honest
counts, and the shipped examples' ports.

run_evaluation over MCP imported a function that had been deleted on purpose,
so every call was an ImportError; POST /docker/run-eval called a retired
function that raised on purpose, so every call was a 500; both READMEs and
the dashboard pointed at it. Two routes reported "stores": 5 as a literal.
The template published harness containers onto the live family's ports.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seren_probe.app import create_app
from seren_probe.config import SerenProbeConfig
from seren_probe.core.topology import compile_topology
from seren_probe.mcp.tools import ProbeToolImpl
from seren_probe.runtime import eval_run

PKG = Path(__file__).parent.parent
FAMILY_BAND = {6361, 7777} | set(range(7420, 7431))


@pytest.fixture
def client():
    with TestClient(create_app(SerenProbeConfig())) as c:
        yield c


def _topo():
    return compile_topology({"ProbeConfig": {
        "StartingPort": 7520,
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "L", "Port": 7521}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "M", "Port": 7525}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "C", "Port": 7527, "Stores": [{"Store": "L"}, {"Store": "M"}]}]},
    }})


def _pretend_topology_is_up(app, monkeypatch, *, seeded=True):
    """A compiled topology and its state on app.state, with every disk write and
    the real evaluator stubbed. Nothing here touches ~/.seren-probe or a socket."""
    topo = _topo()
    app.state.compiled_topology = topo
    app.state.topology_state = {
        "project_name": "probe-test", "seeded": seeded,
        "url_of": {n.name: f"http://127.0.0.1:{n.port}" for n in topo.loci + topo.memory}
                  | {c.name: f"http://127.0.0.1:{c.port}" for c in topo.corpus},
    }
    from seren_probe.runtime import docker_env, live_eval
    from seren_probe.core import resolve as _resolve
    monkeypatch.setattr(docker_env, "save_topology_state", lambda state: None)
    monkeypatch.setattr(docker_env, "load_topology_state", lambda: {})
    monkeypatch.setattr(docker_env, "save_eval_results", lambda *a, **k: None)

    class _Q:
        def __init__(self, q): self.query, self.asks, self.expect_content = q, "loci", ["x"]

    def _inputs(topology, body=None, **kw):
        return _resolve.EvalInputs(questions=[_Q("what colour")], seed_by_store=None,
                                   warnings=[], seed=False, questions_by_store=None)
    monkeypatch.setattr(_resolve, "resolve_eval_inputs", _inputs)
    calls = []

    def _fake_eval(topology, url_of, questions, **kw):
        calls.append(kw)
        return {"stores": {"L": {"hit_rate": 1.0, "q_detail": [{"q": "what colour"}]}},
                "query_count": len(questions), "date": "2026-09-22"}
    monkeypatch.setattr(live_eval, "run_topology_evaluation", _fake_eval)
    return calls


# ══ MCP: run_evaluation ════════════════════════════════════════════════

def test_run_evaluation_with_no_topology_refuses_instead_of_ImportError(client):
    impl = ProbeToolImpl(client.app.state)
    out = asyncio.run(impl.run_evaluation())
    assert out["ok"] is False
    assert "No topology is running" in out["error"]


def test_run_evaluation_goes_through_the_same_path_as_the_route(client, monkeypatch):
    calls = _pretend_topology_is_up(client.app, monkeypatch)
    impl = ProbeToolImpl(client.app.state)
    out = asyncio.run(impl.run_evaluation(max_parallel_stores=2))
    assert out["ok"] is True, out
    assert calls and calls[0]["max_parallel_stores"] == 2
    assert calls[0]["seed"] is False, "an already-seeded pod is scored as-is"
    assert "q_detail" not in out["results"]["stores"]["L"], "the tool returns the lean shape"
    assert "seed_skipped" in out["results"]


def test_get_eval_results_sees_what_the_route_scored(client, monkeypatch):
    """The old private state dict went stale after the first run because the
    route REPLACES app.state.eval_results. The tool reads app.state itself now."""
    _pretend_topology_is_up(client.app, monkeypatch)
    impl = ProbeToolImpl(client.app.state)
    assert impl.get_eval_results()["stores"] == {}
    r = client.post("/eval/run", json={})
    assert r.status_code == 200, r.text
    got = impl.get_eval_results()
    assert got["stores"]["L"]["hit_rate"] == 1.0
    assert "q_detail" not in got["stores"]["L"]


def test_the_route_and_the_tool_share_one_implementation(client, monkeypatch):
    _pretend_topology_is_up(client.app, monkeypatch)
    seen = []

    async def _spy(state, body=None):
        seen.append(body)
        raise eval_run.NoTopologyRunning()
    monkeypatch.setattr(eval_run, "run_topology_eval", _spy)
    import seren_probe.routes.eval as route_mod
    import seren_probe.mcp.tools as tool_mod
    monkeypatch.setattr(route_mod, "run_topology_eval", _spy)
    monkeypatch.setattr(tool_mod, "run_topology_eval", _spy)
    assert client.post("/eval/run", json={"reseed": True}).status_code == 400
    asyncio.run(ProbeToolImpl(client.app.state).run_evaluation(reseed=True))
    assert len(seen) == 2 and all(b["reseed"] for b in seen)


def test_get_store_config_reports_the_topology_not_a_literal_five(client, monkeypatch):
    impl = ProbeToolImpl(client.app.state)
    before = impl.get_store_config()
    assert before["stores"] == 0 and before["topology_running"] is False
    _pretend_topology_is_up(client.app, monkeypatch)
    after = impl.get_store_config()
    assert after["topology_running"] is True
    assert after["topology_stores"] == ["C", "L", "M"]


# ══ HTTP: the dead route, the honest counts ════════════════════════════

def test_docker_run_eval_is_gone(client):
    assert client.post("/docker/run-eval").status_code in (404, 405)


def test_nothing_shipped_points_at_the_dead_route():
    for rel in ("seren_probe/viewer/ui/body.html", "seren_probe/viewer/ui/scripts.js",
                "README.md", "../README.md"):
        assert "docker/run-eval" not in (PKG / rel).read_text(encoding="utf-8"), rel
    assert "docker-run-eval" not in (PKG / "seren_probe/viewer/ui/body.html").read_text(encoding="utf-8")


def test_the_retired_one_shot_is_gone_from_the_runtime():
    from seren_probe.runtime import docker_env
    assert not hasattr(docker_env, "launch_and_eval")
    import seren_probe
    for name in seren_probe.__all__:
        assert hasattr(seren_probe, name), f"__all__ names {name}, which the package does not define"


def test_root_and_eval_config_count_what_is_configured(client):
    assert client.get("/").json()["stores"] == 0
    assert client.get("/eval/config").json()["stores"] == 0
    client.post("/eval/config", json={"memory_url": "http://127.0.0.1:7520"})
    assert client.get("/eval/config").json()["stores"] == 1


# ══ The shipped examples stay out of the family's band ═════════════════

@pytest.mark.parametrize("rel", ["ProbeConfig.template.yml", "seren_probe/ProbeConfig.yml"])
def test_shipped_probeconfigs_publish_outside_the_live_family_band(rel):
    """A harness container published on 7420 is a fake Memory sitting where the
    real one listens. Whatever is pointed at 127.0.0.1:7420 next reads the fake."""
    import yaml
    topo = compile_topology(yaml.safe_load((PKG / rel).read_text(encoding="utf-8")))
    ports = {n.port for n in topo.loci + topo.memory} | {c.port for c in topo.corpus}
    clash = sorted(ports & FAMILY_BAND)
    assert not clash, f"{rel} publishes harness containers on live family ports {clash}"


def test_the_readme_examples_stay_out_of_the_band_too():
    for rel in ("README.md", "../README.md"):
        text = (PKG / rel).read_text(encoding="utf-8")
        bad = sorted({int(p) for p in re.findall(r"Port: (\d{4})", text) if int(p) in FAMILY_BAND})
        assert not bad, f"{rel} hands the reader harness ports on the live band: {bad}"


def test_the_sample_is_named_what_everything_loads():
    """config.py, __main__, the unit sample and the installer all say
    seren-probe.yaml; the sample was the one file spelled serenprobe."""
    assert (PKG / "seren-probe.yaml.sample").is_file()
    assert not (PKG / "serenprobe.yaml.sample").exists()
    unit = (PKG / "seren-probe.service.sample").read_text(encoding="utf-8")
    assert "python -m seren_probe " in unit and "-m serenprobe" not in unit
    assert "SERENPROBE_" not in unit


def test_the_sample_ships_no_live_store_address():
    sample = (PKG / "seren-probe.yaml.sample").read_text(encoding="utf-8")
    live = [ln for ln in sample.splitlines()
            if re.match(r"\s*\w+_url:\s*http", ln) and not ln.lstrip().startswith("#")]
    assert not live, f"the sample hands out live store addresses: {live}"


def test_the_template_compiles_and_its_example_refs_resolve(monkeypatch):
    """The copy-me file used the top-level `Questions:` key the compiler refuses,
    and pointed at an examples/ directory that did not exist. Both are the first
    thing a new operator touches."""
    import yaml
    from seren_probe.core.resolve import resolve_eval_inputs
    monkeypatch.chdir(PKG)
    topo = compile_topology(yaml.safe_load((PKG / "ProbeConfig.template.yml").read_text(encoding="utf-8")))
    assert topo.warnings == [], topo.warnings
    ei = resolve_eval_inputs(topo, {})
    assert ei.questions, "DefaultQuestions did not resolve to any questions"
    assert ei.seed and ei.seed_by_store, "the default seeds did not resolve"
    decoy = [n for n in topo.loci if n.name == "decoy"]
    assert decoy and decoy[0].name in ei.seed_by_store, "the decoy store has no seed"
    assert not any("could not" in w.lower() or "missing" in w.lower() for w in ei.warnings), ei.warnings


def test_the_tool_impl_imports_without_the_mcp_extra(tmp_path):
    """CI installs the package without [mcp]. The impl class has no use for the
    SDK - only register_tools does - so importing it must not require one. It
    did, for a type annotation, and collection of this very file died in CI."""
    import subprocess, sys
    (tmp_path / "mcp.py").write_text('raise ImportError("mcp is not installed (simulated CI)")\n',
                                     encoding="utf-8")
    env = {**__import__("os").environ, "PYTHONPATH": str(tmp_path)}
    r = subprocess.run([sys.executable, "-c",
                        "from seren_probe.mcp.tools import ProbeToolImpl; print('ok')"],
                       capture_output=True, text=True, env=env, cwd=str(PKG))
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr
