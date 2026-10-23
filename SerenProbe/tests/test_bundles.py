"""
The inquisition bundle (21 Oct 2026): a dataset travels whole, so a ProbeConfig
sent to a Probe on another box finds its seed and question files. Pinned:

- pack builds a verified archive from a folder: the configs and the files they
  reference, a manifest of kind probe, a content hash that does not change
  between packs of the same dataset
- inspect reads the manifest and the configs out of the archive, compiles
  them, and refuses a ref that is missing or reaches outside; nothing is written
- install lands under datasets/<name>-<hash8>, verifies every sha256 (a
  tampered file is refused and nothing is kept), and the same bundle twice is
  one folder
- the loaders with a base open refs under it and only there; a topology out of
  a bundle resolves its plan under its base from any working directory
- the routes: upload, list, get, activate (the active config names its bundle,
  and a run started on it carries the bundle and config), delete refuses while
  runs or the active config name it, unless forced

Everything runs against a throwaway state dir in tmp_path.
"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seren_probe.app import create_app
from seren_probe.config import SerenProbeConfig
from seren_probe.core.resolve import resolve_plan
from seren_probe.core.seed_dataset import SeedError, load_questions, load_seed_items
from seren_probe.core.topology import load_probe_config
from seren_probe.runtime import bundles, ledger
from seren_probe.runtime.docker_env import configure_state_dir


@pytest.fixture(autouse=True)
def state_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("SEREN_PROBE_STATE_DIR", str(tmp_path / "state"))
    configure_state_dir("")
    yield tmp_path / "state"
    configure_state_dir("")


CONFIG = """ProbeConfig:
  StartingPort: 7520
  DefaultQuestions: {questions}
  DefaultLociSeed: {loci}
  DefaultMemorySeed: memory.yaml
  Loci:
    LociCount: 1
    LociConfigs: [{{Name: L, Port: 7521}}]
  Memory:
    MemoryCount: 1
    MemoryConfigs: [{{Name: M, Port: 7525}}]
  Corpus:
    CorpusCount: 1
    CorpusConfigs: [{{Name: C, Port: 7527, Stores: [{{Store: L}}, {{Store: M}}]}}]
"""


@pytest.fixture
def dataset(tmp_path) -> Path:
    """A dataset folder: two configs (default and hard), the files they
    reference, and a stray file no config names."""
    d = tmp_path / "ds"
    (d / "hard").mkdir(parents=True)
    (d / "loci.yaml").write_text("- {key: colour, value: blue, why: the test}\n", encoding="utf-8")
    (d / "memory.yaml").write_text("- {content: the kettle whistled, ref: m1}\n", encoding="utf-8")
    (d / "questions.yaml").write_text("- {asks: loci, query: what colour, expect_content: [blue]}\n", encoding="utf-8")
    (d / "hard" / "questions.yaml").write_text(
        "- {asks: loci, query: what colour was it, expect_content: [blue]}\n"
        "- {asks: memory, query: what whistled, expect_content: [kettle]}\n", encoding="utf-8")
    (d / "ProbeConfig.yml").write_text(CONFIG.format(questions="questions.yaml", loci="loci.yaml"), encoding="utf-8")
    (d / "ProbeConfig_hard.yml").write_text(CONFIG.format(questions="hard/questions.yaml", loci="loci.yaml"),
                                            encoding="utf-8")
    (d / "notes.txt").write_text("not referenced by any config\n", encoding="utf-8")
    return d


def _members(archive: Path) -> set[str]:
    with tarfile.open(str(archive), "r:*") as tar:
        return {n.split("/", 1)[1] for n in tar.getnames() if "/" in n}


def test_pack_carries_the_configs_and_what_they_reference(dataset, tmp_path):
    out = tmp_path / "ds.iqz"
    rec = bundles.pack_dataset(dataset, out, name="ds", configs=bundles.discover_configs(dataset), note="a test set")
    assert out.is_file() and rec["id"].startswith("ds-") and len(rec["hash"]) == 64
    assert [c["name"] for c in rec["configs"]] == ["default", "hard"], "named by suffix"
    assert _members(out) == {"manifest.json", "ProbeConfig.yml", "ProbeConfig_hard.yml", "loci.yaml",
                             "memory.yaml", "questions.yaml", "hard/questions.yaml"}, "the closure, not the folder"
    rep = bundles.inspect_archive(out)
    assert rep["ok"] and rep["problems"] == [] and rep["id"] == rec["id"] and rep["installed"] is False
    assert rep["configs"][1]["loci"] == ["L"] and rep["configs"][1]["corpus"] == ["C"]
    again = bundles.pack_dataset(dataset, tmp_path / "again.iqz", name="ds", configs=bundles.discover_configs(dataset),
                                 note="a different note")
    assert again["hash"] == rec["hash"], "the hash is the content, not the pack or the note"
    everything = bundles.pack_dataset(dataset, tmp_path / "all.iqz", name="ds",
                                      configs=bundles.discover_configs(dataset), include_all=True)
    assert "notes.txt" in _members(tmp_path / "all.iqz") and everything["hash"] != rec["hash"]


def test_a_ref_that_is_missing_or_reaches_outside_is_refused(dataset, tmp_path, state_in_tmp):
    (dataset / "ProbeConfig_out.yml").write_text(CONFIG.format(questions="questions.yaml", loci="../loci.yaml"),
                                                 encoding="utf-8")
    (dataset / "ProbeConfig_gone.yml").write_text(CONFIG.format(questions="nowhere.yaml", loci="loci.yaml"),
                                                  encoding="utf-8")
    with pytest.raises(bundles.BundleError) as e:
        bundles.pack_dataset(dataset, tmp_path / "bad.iqz", name="ds", configs=bundles.discover_configs(dataset))
    text = "\n".join(e.value.problems)
    assert "reaches outside" in text and "nowhere.yaml" in text and not (tmp_path / "bad.iqz").exists()

    # An archive that LISTS a config whose ref the archive does not hold: refused
    # by inspect, and install writes nothing.
    from seren_sinew.stores import pack_snapshot_to
    folder = tmp_path / "hand" / "hand"
    folder.mkdir(parents=True)
    (folder / "ProbeConfig.yml").write_text(CONFIG.format(questions="questions.yaml", loci="loci.yaml"),
                                            encoding="utf-8")
    sha = bundles._sha256(folder / "ProbeConfig.yml")
    (folder / "manifest.json").write_text(json.dumps({
        "format": 1, "kind": "probe", "name": "hand", "configs": [{"name": "default", "path": "ProbeConfig.yml"}],
        "files": [{"path": "ProbeConfig.yml", "bytes": 1, "sha256": sha}]}), encoding="utf-8")
    pack_snapshot_to(folder, tmp_path / "hand.iqz", compress="gz")
    rep = bundles.inspect_archive(tmp_path / "hand.iqz")
    assert rep["ok"] is False and any("not in the bundle" in p for p in rep["problems"])
    with pytest.raises(bundles.BundleError):
        bundles.install_archive(tmp_path / "hand.iqz")
    assert not list((state_in_tmp / "datasets").glob("hand-*")) if (state_in_tmp / "datasets").exists() else True

    # Not a bundle at all.
    assert bundles.inspect_archive(b"this is not a tar")["ok"] is False
    (folder / "manifest.json").write_text(json.dumps({"kind": "loci", "files": []}), encoding="utf-8")
    pack_snapshot_to(folder, tmp_path / "kind.iqz", compress="gz")
    assert any("not an inquisition bundle" in p for p in bundles.inspect_archive(tmp_path / "kind.iqz")["problems"])


def test_install_verifies_and_keeps_one_folder_per_content(dataset, tmp_path, state_in_tmp):
    out = tmp_path / "ds.iqz"
    rec = bundles.pack_dataset(dataset, out, name="ds", configs=bundles.discover_configs(dataset))

    # Tampered: the bytes changed after the manifest was written. The manifest
    # still reads clean (so inspect passes), Sinew's verify refuses the bytes,
    # and nothing is kept.
    work = tmp_path / "tamper"
    with tarfile.open(str(out), "r:*") as tar:
        tar.extractall(work)
    top = next(work.iterdir())
    (top / "loci.yaml").write_text("- {key: colour, value: red}\n", encoding="utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(top, arcname="tampered")
    tampered = buf.getvalue()
    assert bundles.inspect_archive(tampered)["ok"] is True, "the manifest alone cannot tell"
    with pytest.raises(bundles.BundleError) as e:
        bundles.install_archive(tampered)
    assert "did not verify" in str(e.value)
    assert not list((state_in_tmp / "datasets").glob("ds-*")) and not list((state_in_tmp / "datasets").glob("tampered-*"))

    first = bundles.install_archive(out)
    assert first["installed"] is True and first["already"] is False and first["id"] == rec["id"]
    folder = state_in_tmp / "datasets" / rec["id"]
    assert (folder / "manifest.json").is_file() and (folder / "hard" / "questions.yaml").is_file()
    second = bundles.install_archive(out.read_bytes())
    assert second["installed"] is False and second["already"] is True
    # The same manifest tampered AFTER the install is "already installed": the
    # copy on disk is the verified one, and the upload never touches it.
    assert bundles.install_archive(tampered)["already"] is True
    assert (folder / "loci.yaml").read_text(encoding="utf-8").startswith("- {key: colour, value: blue")
    rows = bundles.list_bundles()
    assert [r["id"] for r in rows] == [rec["id"]] and rows[0]["runs"] == 0 and rows[0]["files"] == 6
    incoming = state_in_tmp / "datasets" / ".incoming"
    assert not incoming.exists() or not list(incoming.iterdir()), "the upload temp is cleaned"

    got = bundles.get_bundle(rec["id"], details=True)
    assert got["configs"][0]["loci"] == ["L"] and got["configs"][0]["errors"] == []
    text, entry = bundles.config_text(rec["id"], "hard")
    assert "hard/questions.yaml" in text and entry["name"] == "hard"
    assert bundles.config_text(rec["id"], "nope") is None and bundles.bundle_dir("../etc") is None


def test_the_loaders_open_refs_under_the_base_and_nowhere_else(dataset, tmp_path, monkeypatch):
    assert len(load_seed_items("loci.yaml", "loci", base=dataset)) == 1
    assert len(load_questions(["questions.yaml", "hard/questions.yaml"], base=dataset)) == 3
    for bad in ("../loci.yaml", str((tmp_path / "ds" / "loci.yaml").resolve()), "/loci.yaml", "a/../../loci.yaml"):
        with pytest.raises(SeedError) as e:
            load_seed_items(bad, "loci", base=dataset)
        assert "outside" in str(e.value)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    text = (dataset / "ProbeConfig_hard.yml").read_text(encoding="utf-8")
    with pytest.raises(SeedError):
        resolve_plan(load_probe_config(text))      # without a base the old rule holds: the cwd, where nothing is
    plan = resolve_plan(load_probe_config(text, base_dir=dataset))
    assert len(plan.seed_by_store["L"]) == 1 and len(plan.seed_by_store["M"]) == 1 and len(plan.questions) == 2


@pytest.fixture
def client():
    with TestClient(create_app(SerenProbeConfig())) as c:
        yield c


def test_the_routes_install_activate_and_guard_deletion(client, dataset, tmp_path, state_in_tmp, monkeypatch):
    out = tmp_path / "ds.iqz"
    bundles.pack_dataset(dataset, out, name="ds", configs=bundles.discover_configs(dataset))
    data = out.read_bytes()
    octet = {"Content-Type": "application/octet-stream"}

    assert client.post("/bundles", content=b"", headers=octet).status_code == 400
    r = client.post("/bundles", content=b"garbage", headers=octet)
    assert r.status_code == 400 and r.json()["detail"]["problems"]
    assert client.post("/bundles/inspect", content=data, headers=octet).json()["ok"] is True
    assert not list((state_in_tmp / "datasets").glob("ds-*")), "inspect writes nothing"

    r = client.post("/bundles", content=data, headers=octet)
    assert r.status_code == 200 and r.json()["installed"] is True, r.text
    bid = r.json()["id"]
    assert client.get("/bundles").json()["count"] == 1
    one = client.get(f"/bundles/{bid}").json()
    assert [c["name"] for c in one["configs"]] == ["default", "hard"] and one["configs"][1]["loci"] == ["L"]
    assert client.get(f"/bundles/{bid}/configs/hard").json()["summary"]["corpus"] == ["C"]
    assert client.get("/bundles/nope-00000000").status_code == 404

    r = client.post(f"/bundles/{bid}/activate", json={})
    assert r.status_code == 400 and "which config" in r.json()["detail"]
    r = client.post(f"/bundles/{bid}/activate", json={"config": "hard"})
    assert r.status_code == 200 and r.json()["active"] and r.json()["bundle"] == bid, r.text
    assert r.json()["lint"]["ok"] is not None, "the lint found the bundle's files"
    pc = client.get("/docker/probeconfig").json()
    assert pc["source"] == "bundle" and pc["bundle"] == bid and pc["config_name"] == "hard"
    assert client.delete(f"/bundles/{bid}").status_code == 409, "the active config's source"

    # A plain upload takes over, and is cwd-relative like before.
    r = client.post("/docker/probeconfig", json={"probe_config": (dataset / "ProbeConfig.yml").read_text()})
    assert r.status_code == 200 and r.json()["bundle"] is None
    assert client.get("/docker/probeconfig").json()["source"] == "uploaded"

    # A run started on the bundle names it, and the ledger filters by it.
    try:
        from test_ledger import _pretend_topology_is_up
    except ImportError:
        from tests.test_ledger import _pretend_topology_is_up
    _pretend_topology_is_up(client.app, monkeypatch)
    client.app.state.topology_state.update({"bundle": bid, "config_name": "hard"})
    assert client.post("/eval/run", json={}).status_code == 200
    runs = client.get("/eval/runs", params={"bundle": bid}).json()
    assert runs["count"] == 1 and runs["runs"][0]["config_name"] == "hard"
    assert client.get("/bundles").json()["bundles"][0]["runs"] == 1

    client.app.state.topology_state.pop("bundle")
    r = client.delete(f"/bundles/{bid}")
    assert r.status_code == 409 and "run(s)" in r.json()["detail"]["problems"][0]
    assert client.delete(f"/bundles/{bid}", params={"force": "true"}).status_code == 200
    assert client.get(f"/bundles/{bid}").status_code == 404 and client.get("/bundles").json()["count"] == 0
    assert client.get("/eval/runs", params={"bundle": bid}).json()["count"] == 1, "the run keeps its numbers"


def test_a_bundled_active_config_survives_a_restart(dataset, tmp_path, state_in_tmp):
    out = tmp_path / "ds.iqz"
    bundles.pack_dataset(dataset, out, name="ds", configs=bundles.discover_configs(dataset))
    bid = bundles.install_archive(out)["id"]
    with TestClient(create_app(SerenProbeConfig())) as c:
        assert c.post(f"/bundles/{bid}/activate", json={"config": "hard"}).status_code == 200
        assert (state_in_tmp / "active_config.json").is_file()
    with TestClient(create_app(SerenProbeConfig())) as c:
        pc = c.get("/docker/probeconfig").json()
        assert pc["source"] == "bundle" and pc["bundle"] == bid and pc["config_name"] == "hard", "re-read from the bundle on disk"
        assert "hard/questions.yaml" in pc["yaml"]
        r = c.post("/docker/probeconfig", json={"probe_config": (dataset / "ProbeConfig.yml").read_text(encoding="utf-8")})
        assert r.status_code == 200
        assert not (state_in_tmp / "active_config.json").exists(), "a plain upload forgets the bundled choice"
    with TestClient(create_app(SerenProbeConfig())) as c:
        assert c.get("/docker/probeconfig").json()["source"] == "shipped-example", "an uploaded text never outlived the process"


def test_export_carries_the_baseline_runs_and_install_keeps_them_apart(dataset, tmp_path, state_in_tmp, monkeypatch):
    try:
        from test_ledger import _results
    except ImportError:
        from tests.test_ledger import _results
    out = tmp_path / "ds.iqz"
    bundles.pack_dataset(dataset, out, name="ds", configs=bundles.discover_configs(dataset))
    bid = bundles.install_archive(out)["id"]
    a = ledger.record_run("eval", _results(0.5, {"short": (4, 2)}), bundle=bid, config_name="hard", question_hash="q1")
    b = ledger.record_run("regrade", {"stores": {}, "sweep": [1]}, bundle=bid, config_name="hard", question_hash="q1")
    ledger.record_run("eval", _results(0.9, {"short": (1, 1)}), question_hash="q9")       # not the bundle's
    exp = tmp_path / "ds-export.iqz"
    rec = bundles.export_bundle(bid, exp, with_runs=True)
    assert rec["id"] == bid and rec["runs"] == 2, "the runs ride outside the content hash"
    rep = bundles.inspect_archive(exp)
    assert rep["ok"] and rep["id"] == bid and {r["id"] for r in rep["runs"]} == {a["id"], b["id"]}
    bare = bundles.export_bundle(bid, tmp_path / "bare.iqz", with_runs=False)
    assert bare["runs"] == 0 and not any(m.startswith("runs/") for m in _members(tmp_path / "bare.iqz"))

    # The receiver: a different Probe, an empty ledger.
    monkeypatch.setenv("SEREN_PROBE_STATE_DIR", str(tmp_path / "theirs"))
    configure_state_dir("")
    got = bundles.install_archive(exp)
    assert got["installed"] is True and got["runs_imported"] == 2 and got["baseline_runs"] == 2
    rows = ledger.list_runs(bundle=bid)
    assert len(rows) == 2 and all(r["origin"] == "imported" and r["imported_from"] == bid for r in rows)
    assert len(ledger.list_runs()) == 2, "the run that did not name the bundle did not travel"
    assert bundles.install_archive(exp)["runs_imported"] == 0, "already here, not doubled"
    assert bundles.list_bundles()[0]["runs"] == 2
    mine = ledger.record_run("eval", _results(0.75, {"short": (4, 3)}), bundle=bid, config_name="hard", question_hash="q1")
    c = ledger.compare_runs(ledger.load_run(a["id"]), mine)
    assert c["same_questions"] is True and c["stores"]["L"]["hit_rate"]["delta"] == 0.25
    assert c["by_tag"]["L"]["short"]["delta"] == 0.25


def test_an_edited_bundled_config_keeps_the_bundles_files(dataset, tmp_path, state_in_tmp):
    out = tmp_path / "ds.iqz"
    bundles.pack_dataset(dataset, out, name="ds", configs=bundles.discover_configs(dataset))
    bid = bundles.install_archive(out)["id"]
    edited = (dataset / "ProbeConfig_hard.yml").read_text(encoding="utf-8") + "\n# a knob turned\n"
    with TestClient(create_app(SerenProbeConfig())) as c:
        assert c.post(f"/bundles/{bid}/activate", json={"config": "hard"}).status_code == 200
        r = c.post("/docker/probeconfig", json={"probe_config": edited, "bundle": bid, "config_name": "hard"})
        assert r.status_code == 200 and r.json()["bundle"] == bid and r.json()["edited"] is True, r.text
        assert r.json()["lint"]["ok"] is not None, "the lint found the bundle's files for the edited text"
        pc = c.get("/docker/probeconfig").json()
        assert pc["edited"] is True and "a knob turned" in pc["yaml"]
        assert c.post("/docker/probeconfig", json={"probe_config": edited, "bundle": bid, "config_name": "nope"}).status_code == 400
    with TestClient(create_app(SerenProbeConfig())) as c:
        pc = c.get("/docker/probeconfig").json()
        assert pc["bundle"] == bid and pc["edited"] is True and "a knob turned" in pc["yaml"], "the edited text survives too"


def test_clear_frees_the_bundle_and_delete_names_every_holder(client, dataset, tmp_path, state_in_tmp):
    out = tmp_path / "ds.iqz"
    bundles.pack_dataset(dataset, out, name="ds", configs=bundles.discover_configs(dataset))
    bid = bundles.install_archive(out)["id"]
    assert client.post(f"/bundles/{bid}/activate", json={"config": "hard"}).status_code == 200
    r = client.delete(f"/bundles/{bid}")
    assert r.status_code == 409 and r.json()["detail"]["reasons"] == ["active_config"]

    r = client.delete("/docker/probeconfig")
    assert r.status_code == 200 and r.json()["cleared"] == {"source": "bundle", "bundle": bid, "config_name": "hard"}
    pc = client.get("/docker/probeconfig").json()
    assert pc["source"] == "shipped-example" and pc["bundle"] is None and pc["active"] is False
    assert not (state_in_tmp / "active_config.json").exists(), "a restart does not bring it back"
    assert client.delete("/docker/probeconfig").json()["cleared"] is None, "clearing nothing is fine"

    # a pod started from it, and a run naming it: both named, with the active config, in one refusal
    assert client.post(f"/bundles/{bid}/activate", json={"config": "hard"}).status_code == 200
    ledger.record_run("eval", {"stores": {}}, bundle=bid, config_name="hard", question_hash="q")
    client.app.state.topology_state = {"project_name": "probe-test", "bundle": bid, "config_name": "hard"}
    r = client.delete(f"/bundles/{bid}")
    assert r.status_code == 409 and r.json()["detail"]["reasons"] == ["running_pod", "active_config", "runs"]
    r = client.delete("/docker/probeconfig")
    assert r.json()["pod_running"] is True and bid in r.json()["note"], "the pod keeps its config until stopped"
    assert client.delete(f"/bundles/{bid}").json()["detail"]["reasons"] == ["running_pod", "runs"]
    client.app.state.topology_state = None                  # stopped
    r = client.delete(f"/bundles/{bid}", params={"force": "true"})
    assert r.status_code == 200 and r.json()["config_cleared"] is False

    # an uploaded config clears the same way
    assert client.post("/docker/probeconfig", json={"probe_config": (dataset / "ProbeConfig.yml").read_text()}).status_code == 200
    assert client.delete("/docker/probeconfig").json()["cleared"]["source"] == "uploaded"


def test_a_new_pod_replaces_the_old_one_and_stop_takes_everything_down(monkeypatch, tmp_path):
    """Every pod shares one compose project name. Start and Stop both remove
    orphans, so a second config started over a first one replaces it and Stop
    leaves nothing of either running (22 Oct 2026: a 'before' pod ran
    for twelve hours behind 'after', invisible to the Probe)."""
    from seren_probe.runtime import docker_env
    calls = []
    monkeypatch.setattr(docker_env, "_run_docker_streamed", lambda *a, **k: calls.append(("up", a)) or [])
    monkeypatch.setattr(docker_env, "_run_docker", lambda *a, **k: calls.append(("down", a)))
    docker_env.compose_up(tmp_path, "seren-probe-target", build=False)
    docker_env.compose_down(tmp_path, "seren-probe-target")
    up, down = calls[0][1], calls[1][1]
    assert "up" in up and "--remove-orphans" in up
    assert "down" in down and "--remove-orphans" in down and "-v" not in down, "the model cache volume stays"
