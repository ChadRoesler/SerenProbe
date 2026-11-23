"""
The keys Probe holds for outside models (runtime/keys.py, routes/keys.py), and
how a pod is handed one by name (core/keynames.py, the emitter, spin-up).

What these pin down, before anything is built on them:
  - write-only: no route, listing, error or log carries a value back
  - the yaml's pointer wins and can't be shadowed or deleted from here
  - a damaged key file refuses a write instead of being replaced by one key
  - a pod asks by name; the compose file holds an interpolation, never the value;
    a key that isn't held refuses the spin-up before anything is written
  - a bundle built from datasets/ can't carry the key file
"""
from __future__ import annotations

import json
import logging
import os
import stat
import sys
import tarfile

import pytest
import yaml
from fastapi.testclient import TestClient

from seren_probe.app import create_app
from seren_probe.config import KeyRef, SerenProbeConfig, load_config
from seren_probe.core.keynames import interp_var
from seren_probe.core.topology import TopologyError, compile_topology
from seren_probe.core.topology_emit import emit_compose
from seren_probe.runtime import docker_env, keys
from seren_probe.runtime.docker_env import configure_state_dir

SECRET = "sk-test-0123456789abcdef-NEVER-ECHO"
OTHER = "sk-test-fedcba9876543210-ALSO-SECRET"


@pytest.fixture(autouse=True)
def state_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("SEREN_PROBE_STATE_DIR", str(tmp_path / "state"))
    configure_state_dir("")
    yield tmp_path / "state"
    configure_state_dir("")


def _cfg(**refs) -> SerenProbeConfig:
    return SerenProbeConfig(keys={name: KeyRef(**ref) for name, ref in refs.items()})


@pytest.fixture
def client():
    with TestClient(create_app(SerenProbeConfig())) as c:
        yield c


# ── the holder ─────────────────────────────────────────────────────────

def test_put_resolve_list_delete_round_trip(state_in_tmp):
    row = keys.put("anthropic", "  " + SECRET + "  ", note="hosted answerers")
    assert row["name"] == "anthropic" and row["set"] and row["fingerprint"] == keys.fingerprint(SECRET)
    assert keys.resolve("anthropic") == SECRET, "held stripped"
    listed = keys.listing()
    assert [r["name"] for r in listed] == ["anthropic"]
    assert SECRET not in json.dumps(listed) and SECRET not in json.dumps(row)
    keys.delete("anthropic")
    assert keys.resolve("anthropic") is None and keys.listing() == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_key_file_is_owner_only(state_in_tmp):
    keys.put("anthropic", SECRET)
    assert stat.S_IMODE(os.stat(keys.key_file()).st_mode) == 0o600


def test_replacing_a_key_changes_its_fingerprint_not_its_name():
    a = keys.put("anthropic", SECRET)
    b = keys.put("anthropic", OTHER)
    assert a["fingerprint"] != b["fingerprint"] and keys.resolve("anthropic") == OTHER
    assert len(keys.listing()) == 1


@pytest.mark.parametrize("name", ["", "Anthropic", "-lead", "a b", "a/b", "x" * 65, "ключ"])
def test_bad_names_refused(name):
    with pytest.raises(keys.KeysError) as e:
        keys.put(name, SECRET)
    assert e.value.status == 400 and SECRET not in str(e.value)


@pytest.mark.parametrize("value", ["", "   ", "two\nlines", "nul\x00here", "x" * 9000])
def test_bad_values_refused_without_echo(value):
    with pytest.raises(keys.KeysError) as e:
        keys.put("anthropic", value)
    assert e.value.status == 400 and (not value.strip() or value not in str(e.value))


def test_a_damaged_key_file_refuses_a_write_instead_of_replacing_it(state_in_tmp):
    keys.put("anthropic", SECRET)
    keys.key_file().write_text("{ not json", encoding="utf-8")
    with pytest.raises(keys.KeysError) as e:
        keys.put("other", OTHER)
    assert e.value.status == 500
    assert keys.key_file().read_text(encoding="utf-8") == "{ not json", "left as found, for mending"


# ── the yaml's pointers ─────────────────────────────────────────────────

def test_yaml_pointer_resolves_and_is_read_only(monkeypatch):
    monkeypatch.setenv("PROBE_TEST_KEY", SECRET)
    cfg = _cfg(anthropic={"key_env": "PROBE_TEST_KEY", "note": "from the box"})
    assert keys.resolve("anthropic", cfg) == SECRET
    [row] = keys.listing(cfg)
    assert row["source"] == "config" and row["via"] == "env" and row["pointer"] == "PROBE_TEST_KEY"
    assert row["set"] and not row["editable"] and SECRET not in json.dumps(row)
    with pytest.raises(keys.KeysError) as e:
        keys.put("anthropic", OTHER, config=cfg)
    assert e.value.status == 409
    with pytest.raises(keys.KeysError) as e:
        keys.delete("anthropic", config=cfg)
    assert e.value.status == 409


def test_unset_pointer_lists_as_not_set(monkeypatch):
    monkeypatch.delenv("PROBE_TEST_KEY", raising=False)
    cfg = _cfg(anthropic={"key_env": "PROBE_TEST_KEY"})
    assert keys.resolve("anthropic", cfg) is None
    assert keys.listing(cfg)[0]["set"] is False


def test_a_held_key_the_yaml_later_names_is_shadowed_and_can_be_let_go(monkeypatch):
    keys.put("anthropic", OTHER)
    monkeypatch.setenv("PROBE_TEST_KEY", SECRET)
    cfg = _cfg(anthropic={"key_env": "PROBE_TEST_KEY"})
    assert keys.resolve("anthropic", cfg) == SECRET, "the yaml wins"
    held = [r for r in keys.listing(cfg) if r["source"] == "probe"]
    assert held and held[0]["shadowed"] and held[0]["editable"]
    keys.delete("anthropic", config=cfg)
    assert keys.resolve("anthropic", cfg) == SECRET


def test_yaml_keys_block_loads(tmp_path):
    p = tmp_path / "seren-probe.yaml"
    p.write_text(yaml.safe_dump({"keys": {"anthropic": {"key_env": "X_KEY", "note": "n"},
                                          "bogus": "not a mapping"}}), encoding="utf-8")
    cfg = load_config(str(p))
    assert set(cfg.keys) == {"anthropic"} and cfg.keys["anthropic"].key_env == "X_KEY"


# ── the routes ─────────────────────────────────────────────────────────

def test_routes_are_write_only(client, caplog):
    caplog.set_level(logging.DEBUG)
    r = client.put("/keys/anthropic", json={"value": SECRET, "note": "judge"})
    assert r.status_code == 200 and SECRET not in r.text
    assert r.json()["key"]["fingerprint"] == keys.fingerprint(SECRET)
    r = client.get("/keys")
    assert r.status_code == 200 and r.json()["count"] == 1 and SECRET not in r.text
    assert SECRET not in caplog.text, "never logged"
    assert client.delete("/keys/anthropic").status_code == 200
    assert client.get("/keys").json()["count"] == 0
    assert client.delete("/keys/anthropic").status_code == 404


def test_malformed_bodies_never_echo_the_key(client):
    for body in ({"value": 12345}, {"valu": SECRET}, [SECRET], SECRET):
        r = client.put("/keys/anthropic", content=json.dumps(body), headers={"content-type": "application/json"})
        assert r.status_code == 400 and SECRET not in r.text
    r = client.put("/keys/anthropic", content=b"not json " + SECRET.encode())
    assert r.status_code == 400 and SECRET not in r.text
    r = client.put("/keys/Bad Name", json={"value": SECRET})
    assert r.status_code == 400 and SECRET not in r.text


# ── a pod asks by name ─────────────────────────────────────────────────

def _pc(**extra):
    pc = {"StartingPort": 7520,
          "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "L", "Port": 7521}]},
          "Memory": {"MemoryCount": 1, "DefaultKeys": {"ANTHROPIC_API_KEY": "anthropic"},
                     "MemoryConfigs": [{"Name": "M", "Port": 7525,
                                        "Keys": {"OPENAI_API_KEY": "openai.probe"}}]},
          "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
              {"Name": "C", "Port": 7527, "Stores": [{"Store": "L"}, {"Store": "M"}]}]}}
    pc.update(extra)
    return {"ProbeConfig": pc}


def test_interpolation_names_are_one_to_one():
    names = ["a-b", "a.b", "a_b", "a__b", "a_-b", "a-_b", "ab"]
    assert len({interp_var(n) for n in names}) == len(names)
    assert interp_var("anthropic") == "SEREN_PROBE_KEY_ANTHROPIC"


def test_keys_parse_and_merge_default_under_node():
    t = compile_topology(_pc())
    [m] = t.memory
    assert m.keys == {"ANTHROPIC_API_KEY": "anthropic", "OPENAI_API_KEY": "openai.probe"}
    assert t.loci[0].keys == {} and t.corpus[0].keys == {}


@pytest.mark.parametrize("bad,why", [
    ({"lower_case": "anthropic"}, "environment variable name"),
    ({"SEREN_MEMORY_PORT": "anthropic"}, "set by Probe itself"),
    ({"HF_TOKEN": "anthropic"}, "set by Probe itself"),
    ({"API_KEY": "Not A Name"}, "isn't a key name"),
])
def test_bad_keys_refused_at_compile(bad, why):
    pc = _pc()
    pc["ProbeConfig"]["Loci"]["LociConfigs"][0]["Keys"] = bad
    with pytest.raises(TopologyError) as e:
        compile_topology(pc)
    assert any(why in err for err in e.value.errors)


def test_compose_carries_interpolations_never_values(monkeypatch):
    monkeypatch.setenv("PROBE_TEST_KEY", SECRET)
    em = emit_compose(compile_topology(_pc()))
    env = em.compose["services"]["M"]["environment"]
    assert env["ANTHROPIC_API_KEY"] == "${SEREN_PROBE_KEY_ANTHROPIC}"
    assert env["OPENAI_API_KEY"] == "${" + interp_var("openai.probe") + "}"
    assert "ANTHROPIC_API_KEY" not in em.compose["services"]["L"]["environment"]
    assert em.key_refs == {interp_var("anthropic"): "anthropic", interp_var("openai.probe"): "openai.probe"}
    assert SECRET not in yaml.safe_dump(em.compose)


def _stub_docker(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(docker_env, "compose_up",
                        lambda work_dir, project, build=True, env=None: seen.update(env=env, work_dir=work_dir))
    monkeypatch.setattr(docker_env, "wait_for_topology_healthy", lambda url_of, timeout=0: None)
    monkeypatch.setattr(docker_env, "compose_logs", lambda *a, **k: [])
    return seen


def test_spin_up_hands_compose_the_values_and_the_file_only_names(monkeypatch):
    seen = _stub_docker(monkeypatch)
    monkeypatch.setenv("PROBE_TEST_KEY", SECRET)
    keys.put("openai.probe", OTHER)
    cfg = _cfg(anthropic={"key_env": "PROBE_TEST_KEY"})
    docker_env.spin_up_topology(compile_topology(_pc()), project_name="probe-keys-test", build=False, config=cfg)
    assert seen["env"] == {interp_var("anthropic"): SECRET, interp_var("openai.probe"): OTHER}
    written = "".join(p.read_text(encoding="utf-8") for p in seen["work_dir"].iterdir() if p.is_file())
    assert "${SEREN_PROBE_KEY_ANTHROPIC}" in written
    assert SECRET not in written and OTHER not in written
    assert SECRET not in os.environ.get(interp_var("anthropic"), ""), "Probe's own environment untouched"


def test_a_key_not_held_refuses_before_anything_is_written(monkeypatch, tmp_path):
    seen = _stub_docker(monkeypatch)
    monkeypatch.setattr(docker_env, "write_compose", lambda *a, **k: seen.update(wrote=True))
    with pytest.raises(keys.KeysError) as e:
        docker_env.spin_up_topology(compile_topology(_pc()), project_name="probe-keys-test", build=False)
    assert e.value.status == 400 and "anthropic" in str(e.value) and "openai.probe" in str(e.value)
    assert "wrote" not in seen and "env" not in seen


def test_start_route_answers_400_naming_the_missing_key(client, monkeypatch):
    seen = _stub_docker(monkeypatch)
    r = client.post("/docker/start", json={"probe_config": yaml.safe_dump(_pc()), "build": False})
    assert r.status_code == 400 and r.json()["detail"]["stage"] == "keys"
    assert "anthropic" in r.json()["detail"]["error"] and "env" not in seen


# ── nothing travels ────────────────────────────────────────────────────

def test_a_bundle_never_carries_the_key_file(tmp_path):
    from seren_probe.runtime import bundles
    keys.put("anthropic", SECRET)
    d = tmp_path / "ds"
    d.mkdir()
    (d / "loci.yaml").write_text("- {key: colour, value: blue, why: the test}\n", encoding="utf-8")
    (d / "memory.yaml").write_text("- {content: the kettle whistled, ref: m1}\n", encoding="utf-8")
    (d / "questions.yaml").write_text("- {asks: loci, query: what colour, expect_content: [blue]}\n", encoding="utf-8")
    (d / "ProbeConfig.yml").write_text(yaml.safe_dump(_pc(DefaultQuestions="questions.yaml",
                                                          DefaultLociSeed="loci.yaml",
                                                          DefaultMemorySeed="memory.yaml")), encoding="utf-8")
    out = tmp_path / "ds.iqz"
    bundles.pack_dataset(d, out, name="ds", configs=bundles.discover_configs(d))
    bid = bundles.install_archive(out)["id"]
    exp = tmp_path / "ds-export.iqz"
    bundles.export_bundle(bid, exp, with_runs=True)
    for archive in (out, exp):
        assert SECRET.encode() not in archive.read_bytes()
        with tarfile.open(str(archive), "r:*") as tar:
            assert not any(n.endswith("keys.json") for n in tar.getnames())
    assert bundles.datasets_dir() not in keys.key_file().parents, "the key file sits beside datasets/, never in it"
