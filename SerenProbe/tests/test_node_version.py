"""A node's own `Version:` pin (29 Oct 2026, probe-node-version): one run
grades a store at two versions side by side, on the same seed and the same
questions. The ask, 19 Oct 2026: 'a way to test a back and forth from one
version to another of memory/loci/corpus'.

- a per-node Version wins over the kind-wide `Versions:` pin; a bare number
  is an exact pin (==), an operator is kept as written
- two nodes at two versions are two images: the version is in the image
  signature, so they share no cached pip layer
- the pin is in the structure signature (a changed pin is a new pod) and in
  what the run ledger records (pinned_versions), per node
- a Version that is not a version is an error that names the node
"""
import pytest

from seren_probe.core.topology import TopologyError, compile_topology, pinned_versions, structure_signature
from seren_probe.core.topology_emit import emit_compose


def _pc() -> dict:
    return {"ProbeConfig": {
        "StartingPort": 7520,
        "Versions": {"loci": "5.1.4"},
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "loci-514", "Port": 7521},
            {"Name": "loci-520", "Port": 7522, "Version": "5.2.0"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "mem", "Port": 7525, "Version": 5.4}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "scc-a", "Port": 7527, "Stores": [{"Store": "loci-514"}, {"Store": "mem"}]},
            {"Name": "scc-b", "Port": 7528, "Version": ">=1.4", "Stores": [{"Store": "loci-520"}]}]},
    }}


def test_a_node_version_wins_over_the_kind_pin_and_is_its_own_image():
    t = compile_topology(_pc())
    assert t.warnings == []
    by = {n.name: n for n in t.loci + t.memory}
    assert by["loci-514"].version == "" and by["loci-520"].version == "==5.2.0"
    assert by["mem"].version == "==5.4", "a bare number (YAML reads 5.4 as a float) is an exact pin"
    assert {c.name: c.version for c in t.corpus} == {"scc-a": "", "scc-b": ">=1.4"}

    svc = emit_compose(t).compose["services"]
    assert svc["loci-514"]["image"] != svc["loci-520"]["image"], "two versions, two images"
    assert svc["loci-514"]["build"]["args"]["VERSION"] == "==5.1.4", "the kind pin, where the node has none"
    assert svc["loci-520"]["build"]["args"]["VERSION"] == "==5.2.0"
    assert svc["mem"]["build"]["args"]["VERSION"] == "==5.4"
    assert svc["scc-b"]["build"]["args"]["VERSION"] == ">=1.4"
    assert svc["scc-a"]["image"] != svc["scc-b"]["image"]


def test_the_pin_is_in_the_signature_and_in_the_record():
    a = compile_topology(_pc())
    pc = _pc()
    pc["ProbeConfig"]["Loci"]["LociConfigs"][1]["Version"] = "5.2.1"
    b = compile_topology(pc)
    assert structure_signature(a) != structure_signature(b), "a changed pin needs a new pod"
    assert pinned_versions(a) == {"loci": "==5.1.4",
                                  "nodes": {"loci-520": "==5.2.0", "mem": "==5.4", "scc-b": ">=1.4"}}

    plain = compile_topology({"ProbeConfig": {
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7521}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7525}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7527, "Stores": [{"Store": "l"}, {"Store": "m"}]}]}}})
    assert pinned_versions(plain) == {}, "nothing pinned, nothing recorded"


def test_a_version_that_is_not_a_version_is_an_error_that_names_the_node():
    pc = _pc()
    pc["ProbeConfig"]["Memory"]["MemoryConfigs"][0]["Version"] = ["5.4"]
    with pytest.raises(TopologyError) as e:
        compile_topology(pc)
    assert "node 'mem' Version" in str(e.value)
