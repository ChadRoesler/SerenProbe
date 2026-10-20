"""Tests for the compose emitter (seren_probe.topology_emit)."""
from pathlib import Path
import yaml

from seren_probe.core.topology import load_probe_config, compile_topology
from seren_probe.core.topology_emit import emit_compose

PROBECONFIG = Path(__file__).parent.parent / "seren_probe" / "ProbeConfig.yml"


def test_vector_flag_becomes_embedder_env_and_extra():
    t = compile_topology({"ProbeConfig": {
        "Loci":   {"LociCount": 2, "LociConfigs": [
            {"Name": "lv", "Port": 7421, "Flags": ["vector"]}, {"Name": "lp", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "lv"}, {"Store": "m"}]}]},
    }})
    svc = emit_compose(t).compose["services"]
    assert "SEREN_LOCI_EMBEDDING_MODEL" in svc["lv"]["environment"]
    assert "SEREN_LOCI_EMBEDDING_MODEL" not in svc["lp"]["environment"]
    # vector loci builds with [vector]; plain loci builds with no extras
    assert svc["lv"]["build"]["args"]["EXTRAS"] == "[vector]"
    assert svc["lp"]["build"]["args"]["EXTRAS"] == ""
    assert svc["lv"]["build"]["dockerfile"] == "loci.Dockerfile"


def test_mcp_flag_becomes_extra_not_image_name():
    t = compile_topology({"ProbeConfig": {
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421, "Flags": ["mcp"]}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425, "Flags": ["mcp"]}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
    }})
    svc = emit_compose(t).compose["services"]
    assert svc["l"]["build"]["args"]["EXTRAS"] == "[mcp]"
    assert svc["m"]["build"]["args"]["EXTRAS"] == "[mcp]"       # memory: mcp only (vector is core)
    assert svc["m"]["build"]["dockerfile"] == "memory.Dockerfile"
    # no service carries an invalid bracket-in-image-name
    assert all("image" not in s or "[" not in s["image"] for s in svc.values())


def test_image_override_by_kind_and_name():
    t = compile_topology({"ProbeConfig": {
        "Loci":   {"LociCount": 2, "LociConfigs": [
            {"Name": "la", "Port": 7421}, {"Name": "lb", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "la"}, {"Store": "m"}]}]},
    }})
    svc = emit_compose(t, image_overrides={"seren_memory": "my-mem:latest", "la": "custom-la:1"}).compose["services"]
    assert svc["m"]["image"] == "my-mem:latest" and "build" not in svc["m"]   # by kind
    assert svc["la"]["image"] == "custom-la:1" and "build" not in svc["la"]   # by name
    assert "build" in svc["lb"]                                                # default still builds


def test_corpus_wiring_is_container_dns_not_loopback():
    e = emit_compose(load_probe_config(PROBECONFIG))
    for fn, doc in e.corpus_files.items():
        for st in doc["federation"]["stores"]:
            assert st["url"].startswith("http://"), st
            assert "127.0.0.1" not in st["url"] and "localhost" not in st["url"], (fn, st)


def test_host_ports_published_for_eval():
    # 7441, not 7421: the shipped example topology deliberately starts at 7440 to
    # keep a test pod's published host ports OFF the range where the operator's REAL
    # Seren stores live (memory 7420, loci 7421/7422, SCC 7423/7424). A probe pod
    # that binds 7421 either collides with a live SerenLoci or, worse, quietly
    # becomes the thing something else connects to.
    e = emit_compose(load_probe_config(PROBECONFIG))
    assert e.compose["services"]["loci-vector-projectX"]["ports"] == ["127.0.0.1:7441:7441"]


def test_every_store_listens_open_in_its_container_and_is_published_on_loopback():
    """Since Meninges 2.3.0 a store listens on 127.0.0.1 unless told otherwise, which
    inside a container nothing outside can reach, and since 2.4.0 an open bind with no
    token refuses to start. The harness had not booted a store since (19 Oct 2026)."""
    e = emit_compose(load_probe_config(PROBECONFIG))
    seen = set()
    for name, svc in e.compose["services"].items():
        env = svc["environment"]
        prefix = next((p for p in ("SEREN_LOCI", "SEREN_MEMORY", "SEREN_SCC") if f"{p}_ALLOW_OPEN_LAN" in env), None)
        assert prefix, (name, env)
        assert env[f"{prefix}_HOST"] == "0.0.0.0" and env[f"{prefix}_ALLOW_OPEN_LAN"] == "1", (name, env)
        assert all(p.startswith("127.0.0.1:") for p in svc["ports"]), (name, svc["ports"])
        seen.add(prefix)
    assert seen == {"SEREN_LOCI", "SEREN_MEMORY", "SEREN_SCC"}, seen    # every kind is in the shipped topology
    for doc in e.corpus_files.values():
        assert doc["server"]["host"] == "0.0.0.0"


def test_the_exposure_check_lets_an_emitted_store_start(monkeypatch):
    """The other side of the contract: Meninges' own verdict on what the emitter sets."""
    import pytest
    exposure = pytest.importorskip("seren_meninges.exposure")
    e = emit_compose(load_probe_config(PROBECONFIG))
    for name, svc in e.compose["services"].items():
        env = svc["environment"]
        prefix = next(p for p in ("SEREN_LOCI", "SEREN_MEMORY", "SEREN_SCC") if f"{p}_ALLOW_OPEN_LAN" in env)
        monkeypatch.setenv(f"{prefix}_ALLOW_OPEN_LAN", env[f"{prefix}_ALLOW_OPEN_LAN"])
        verdict = exposure.check_exposure(env[f"{prefix}_HOST"], 7441, service=name, env_prefix=prefix)
        assert not verdict.refused, (name, verdict)
        monkeypatch.delenv(f"{prefix}_ALLOW_OPEN_LAN")
        assert exposure.check_exposure(env[f"{prefix}_HOST"], 7441, service=name, env_prefix=prefix).refused, \
            "without the switch the same bind is refused, so the switch is what lets it start"


def test_corpus_depends_on_stores_healthy():
    e = emit_compose(load_probe_config(PROBECONFIG))
    corp = e.compose["services"]["corpus-projectX"]
    assert corp["depends_on"]["memory-projectX"]["condition"] == "service_healthy"


def test_everything_round_trips_as_valid_yaml():
    e = emit_compose(load_probe_config(PROBECONFIG))
    assert yaml.safe_load(yaml.safe_dump(e.compose))
    for doc in e.corpus_files.values():
        assert yaml.safe_load(yaml.safe_dump(doc))


def test_build_services_get_lowercase_image_tag():
    # uppercase node names must NOT leak into the image tag (Docker repo names
    # must be lowercase); service/container names keep their casing.
    t = compile_topology({"ProbeConfig": {
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "Loci-ProjectZ", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "Mem-ProjectX", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "Corp-Y", "Port": 7427, "Stores": [{"Store": "Loci-ProjectZ"}, {"Store": "Mem-ProjectX"}]}]},
    }})
    svc = emit_compose(t).compose["services"]
    for name, s in svc.items():
        img = s.get("image", "")
        assert img == img.lower(), f"{name}: image {img!r} not lowercase"
        assert "[" not in img
    # service key + container_name keep original casing (DNS is case-insensitive)
    assert "Loci-ProjectZ" in svc
    assert svc["Loci-ProjectZ"]["container_name"] == "Loci-ProjectZ"
    # image is signature-derived (kind+extras+version), NOT name-derived -- so an uppercase
    # node name can't leak into the tag because the name isn't IN the tag at all. Same
    # signature = same image = ONE build; a name-derived tag is the 113-store BuildKit
    # cascade waiting to happen (see _build_sig). The 'project' tag idea that expected
    # 'loci-projectz' here was abandoned -- name already disambiguates, corpus fuses across.
    assert svc["Loci-ProjectZ"]["image"] == "seren-probe-loci:local"
