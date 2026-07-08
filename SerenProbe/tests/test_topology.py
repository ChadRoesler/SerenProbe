"""Tests for the ProbeConfig topology compiler (seren_probe.topology)."""
from pathlib import Path
import pytest
import yaml

from seren_probe.topology import compile_topology, load_probe_config, TopologyError

PROBECONFIG = Path(__file__).parent / "ProbeConfig.yml"


def _wrap(pc: dict) -> dict:
    return {"ProbeConfig": pc}


# ── GOOD: compiles clean, zero warnings ─────────────────────────────────
def test_full_explicit_no_warnings():
    t = compile_topology(_wrap({
        "Loci":   {"LociCount": 2, "LociConfigs": [
            {"Name": "loci-a", "Port": 7421, "Flags": ["vector"]},
            {"Name": "loci-b", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "mem-a", "Port": 7425, "Flags": ["mcp"]}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "corp-x", "Port": 7427, "Stores": [
                {"Store": "loci-a"}, {"Store": "loci-b"}, {"Store": "mem-a", "Weight": 0.5}]}]},
    }))
    assert t.warnings == []
    assert len(t.loci) == 2 and len(t.memory) == 1 and len(t.corpus) == 1


def test_autogen_and_catchall():
    t = compile_topology(_wrap({
        "StartingPort": 7420,
        "Loci":   {"LociCount": 5, "LociConfigs": [
            {"Name": "loci-a", "Port": 7421, "Flags": ["vector"]},
            {"Name": "loci-b"}, {"Name": "loci-c", "Port": 7423},
            {"Name": "loci-d", "Flags": ["mcp"]}]},
        "Memory": {"MemoryCount": 3, "MemoryConfigs": [
            {"Name": "mem-a", "Port": 7425, "Flags": ["mcp"]}, {"Name": "mem-b"}]},
        "Corpus": {"CorpusCount": 3, "CorpusConfigs": [
            {"Name": "corp-x", "Port": 7427, "Stores": [
                {"Store": "loci-a"}, {"Store": "mem-a", "Weight": 0.5}]},
            {"Name": "corp-y", "Stores": [{"Store": "loci-c"}]}]},
    }))
    assert t.warnings == []
    # one auto-gen loci, one auto-gen memory, one catch-all corpus
    assert sum(n.generated for n in t.loci) == 1
    assert sum(n.generated for n in t.memory) == 1
    catch = [c for c in t.corpus if c.is_catchall]
    assert len(catch) == 1
    # catch-all swept the 5 unreferenced nodes (loci-b, loci-d, auto-loci, mem-b, auto-mem)
    assert len(catch[0].stores) == 5


# ── GOOD-with-warnings ──────────────────────────────────────────────────
def test_invalid_flag_warns_and_drops():
    t = compile_topology(_wrap({
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [
            {"Name": "m", "Port": 7425, "Flags": ["vector", "mcp"]}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
    }))
    assert any("'vector' isn't valid for Memory" in w for w in t.warnings)
    assert t.memory[0].flags == ["mcp"]      # vector dropped, mcp kept


def test_landmine_identical_store_sets_warns():
    t = compile_topology(_wrap({
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "L", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "M", "Port": 7425}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "c1", "Port": 7440, "Stores": [{"Store": "L"}, {"Store": "M"}]},
            {"Name": "c2", "Port": 7441, "Stores": [{"Store": "L"}, {"Store": "M"}]}]},
    }))
    assert any("IDENTICAL store set" in w for w in t.warnings)


def test_orphan_store_no_catchall_warns():
    t = compile_topology(_wrap({
        "Loci":   {"LociCount": 2, "LociConfigs": [
            {"Name": "L1", "Port": 7421}, {"Name": "L2", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "M", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "L1"}, {"Store": "M"}]}]},
    }))
    assert any("'L2' isn't referenced" in w for w in t.warnings)


# ── BAD: each raises TopologyError naming the right rule ─────────────────
BAD_CASES = {
    "bounds_low": ({
        "StartingPort": 7420,
        "Loci":   {"LociCount": 6, "LociConfigs": [{"Name": f"l{i}"} for i in range(4)]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m"}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Stores": []}]},
    }, "expected 5 or 6"),
    "dup_port": ({
        "Loci":   {"LociCount": 2, "LociConfigs": [{"Name": "a", "Port": 7421}, {"Name": "b", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Port": 7427, "Stores": [{"Store": "a"}]}]},
    }, "claimed by"),
    "port_below_floor": ({
        "StartingPort": 7430,
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "a", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m"}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Stores": [{"Store": "a"}]}]},
    }, "below StartingPort"),
    "missing_starting_port": ({
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "a"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Port": 7427, "Stores": [{"Store": "a"}]}]},
    }, "StartingPort is required"),
    "bad_store_ref": ({
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "a", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Port": 7427, "Stores": [{"Store": "nope"}]}]},
    }, "isn't a declared"),
    "corpus_refs_corpus": ({
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "a", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "c1", "Port": 7427, "Stores": [{"Store": "a"}]},
            {"Name": "c2", "Port": 7428, "Stores": [{"Store": "c1"}]}]},
    }, "that's a Corpus"),
    "dup_name": ({
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "dup", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "dup", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Port": 7427, "Stores": [{"Store": "dup"}]}]},
    }, "globally unique"),
    "name_as_key_oldstyle": ({
        "Loci":   {"LociCount": 1, "LociConfigs": [{"loci-x": {"Port": 7421}}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Stores": []}]},
    }, "no 'Name'"),
    "count_zero": ({
        "Loci":   {"LociCount": 0, "LociConfigs": []},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Port": 7427, "Stores": []}]},
    }, ">= 1"),
}


@pytest.mark.parametrize("name", list(BAD_CASES))
def test_bad_raises(name):
    pc, needle = BAD_CASES[name]
    with pytest.raises(TopologyError) as ei:
        compile_topology(_wrap(pc))
    assert any(needle in e for e in ei.value.errors), f"{name}: {needle!r} not in {ei.value.errors}"


# ── determinism + type-grouped ports ────────────────────────────────────
def _autogen_cfg():
    return _wrap({
        "StartingPort": 7420,
        "Loci":   {"LociCount": 5, "LociConfigs": [
            {"Name": "loci-a", "Port": 7421, "Flags": ["vector"]}, {"Name": "loci-b"},
            {"Name": "loci-c", "Port": 7423}, {"Name": "loci-d", "Flags": ["mcp"]}]},
        "Memory": {"MemoryCount": 3, "MemoryConfigs": [
            {"Name": "mem-a", "Port": 7425, "Flags": ["mcp"]}, {"Name": "mem-b"}]},
        "Corpus": {"CorpusCount": 3, "CorpusConfigs": [
            {"Name": "corp-x", "Port": 7427, "Stores": [{"Store": "loci-a"}, {"Store": "mem-a"}]},
            {"Name": "corp-y", "Stores": [{"Store": "loci-c"}]}]},
    })


def test_ports_deterministic():
    def portmap(t):
        return {n.name: n.port for n in t.loci + t.memory} | {c.name: c.port for c in t.corpus}
    assert portmap(compile_topology(_autogen_cfg())) == portmap(compile_topology(_autogen_cfg()))


def test_ports_type_grouped():
    t = compile_topology(_autogen_cfg())
    loci_auto = sorted(n.port for n in t.loci if n.port >= 7428)
    mem_auto  = sorted(n.port for n in t.memory if n.port >= 7428)
    cor_auto  = sorted(c.port for c in t.corpus if c.port >= 7428)
    assert loci_auto and mem_auto and cor_auto
    assert max(loci_auto) < min(mem_auto) < max(mem_auto) < min(cor_auto)


# ── load_probe_config: path AND string ──────────────────────────────────
def test_load_from_string():
    t = load_probe_config("""
ProbeConfig:
  Loci:   {LociCount: 1, LociConfigs: [{Name: l, Port: 7421}]}
  Memory: {MemoryCount: 1, MemoryConfigs: [{Name: m, Port: 7425}]}
  Corpus: {CorpusCount: 1, CorpusConfigs: [{Name: c, Port: 7427, Stores: [{Store: l}, {Store: m}]}]}
""")
    assert len(t.loci) == 1 and len(t.corpus) == 1


def test_load_real_probeconfig_yml_end_to_end():
    """The shipped ProbeConfig.yml must compile clean and match its declared shape."""
    t = load_probe_config(PROBECONFIG)
    assert t.warnings == [], t.warnings
    assert len(t.loci) == 5 and len(t.memory) == 7 and len(t.corpus) == 3
    catch = [c for c in t.corpus if c.is_catchall]
    assert len(catch) == 1 and len(catch[0].stores) == 7   # 12 nodes - 5 referenced
    # the vector Loci carry the flag; the auto-gen one does not
    assert any("vector" in n.flags for n in t.loci)
    assert any(n.generated and not n.flags for n in t.loci)
