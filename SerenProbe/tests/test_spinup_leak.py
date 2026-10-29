"""
A failed spin-up leaves nothing running, and Stop finds what it has no record
of (punch list probe-spinup-leak).

19 Oct 2026: a pod came up, one store never answered its health gate, the route
answered 500 before any state was kept, and /docker/stop said 'Nothing running'
while four containers held their ports. Pinned here:

- the health gate failing takes the pod down again and the error carries the
  containers' last log lines;
- Stop with no record looks for containers under the project name by their
  compose label, takes them down, and says what it found;
- Stop with nothing anywhere still says 'Nothing running', and where it looked.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from seren_probe.app import create_app
from seren_probe.config import SerenProbeConfig
from seren_probe.runtime import docker_env


def test_a_pod_that_never_answers_its_health_gate_is_taken_down_again(monkeypatch, tmp_path):
    calls: list[tuple] = []
    import seren_probe.core.topology_emit as emit_mod
    monkeypatch.setattr(emit_mod, "emit_compose", lambda t, image_overrides=None: {"compose": {}})
    monkeypatch.setattr(docker_env, "topology_work_dir", lambda name: tmp_path)
    monkeypatch.setattr(docker_env, "write_compose", lambda emitted, wd: wd / "docker-compose.yml")
    monkeypatch.setattr(docker_env, "compose_up", lambda wd, name, build=True: calls.append(("up", name)))
    monkeypatch.setattr(docker_env, "host_url_map", lambda t: {"memory-a": "http://127.0.0.1:1"})

    def never(url_of, timeout=0):
        raise TimeoutError("memory-a never answered /health")
    monkeypatch.setattr(docker_env, "wait_for_topology_healthy", never)
    monkeypatch.setattr(docker_env, "compose_logs", lambda wd, name, tail=20: ["memory-a | Waiting for application startup"])
    monkeypatch.setattr(docker_env, "compose_down", lambda wd, name: calls.append(("down", name)))

    topology = SimpleNamespace(loci=[], memory=[SimpleNamespace(name="memory-a")], corpus=[])
    with pytest.raises(docker_env.SpinUpError) as e:
        docker_env.spin_up_topology(topology, project_name="seren-probe-target", build=False)
    assert "never answered" in str(e.value) and "taken down again" in str(e.value)
    assert e.value.logs == ["memory-a | Waiting for application startup"]
    assert calls == [("up", "seren-probe-target"), ("down", "seren-probe-target")]


def test_the_start_route_hands_the_logs_back(monkeypatch, tmp_path):
    monkeypatch.setenv("SEREN_PROBE_STATE_DIR", str(tmp_path))
    import seren_probe.routes.docker as route_mod

    def fail(*a, **k):
        raise docker_env.SpinUpError("loci-a never answered; the pod 'p' was taken down again", logs=["loci-a | boom"])
    monkeypatch.setattr(docker_env, "spin_up_topology", fail)
    with TestClient(create_app(SerenProbeConfig())) as c:
        r = c.post("/docker/start", json={"build": False})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["stage"] == "spin_up" and detail["logs"] == ["loci-a | boom"]


def test_stop_with_no_record_finds_the_pod_by_its_label_and_takes_it_down(monkeypatch, tmp_path):
    monkeypatch.setenv("SEREN_PROBE_STATE_DIR", str(tmp_path))
    pods = {"seren-probe-target": [{"name": "seren-probe-target-memory-a-1", "state": "running"},
                                   {"name": "seren-probe-target-loci-a-1", "state": "exited"}]}
    downs: list[str] = []

    def find(name):
        return list(pods.get(name, []))

    def down(name):
        downs.append(name)
        pods.pop(name, None)
    monkeypatch.setattr(docker_env, "find_pod_containers", find)
    monkeypatch.setattr(docker_env, "compose_down_project", down)
    with TestClient(create_app(SerenProbeConfig())) as c:
        r = c.post("/docker/stop").json()
        assert r["ok"] is True and r["torn_down"] == "seren-probe-target"
        assert [f["name"] for f in r["found"]] == ["seren-probe-target-memory-a-1", "seren-probe-target-loci-a-1"]
        assert r["left"] == [] and "no record" in r["note"]
        assert downs == ["seren-probe-target"]
        again = c.post("/docker/stop").json()
        assert again == {"ok": False, "error": "Nothing running", "looked_for": "seren-probe-target"}
