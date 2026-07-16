"""Tests for CorpusRegrades schema parsing (seren_probe.topology)."""
import pytest
from seren_probe.core.topology import compile_topology, TopologyError


def _cfg(regrades):
    return {"ProbeConfig": {"StartingPort": 7420,
        "Corpus": {"CorpusRegrades": regrades, "CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]}}}


def test_valid_regrades_parse_with_defaults():
    t = compile_topology(_cfg([
        {"Name": "baseline"},
        {"Name": "tight", "loci_floor": [0.3], "authority_margin": [0.1]},
        {"Name": "big", "n_results": [20, 30], "fetch_multiplier": [3]},
    ]))
    assert t.warnings == []
    names = [r.name for r in t.corpus_regrades]
    assert names == ["baseline", "tight", "big"]
    assert t.corpus_regrades[0].overrides == {}                 # baseline = all defaults
    assert t.corpus_regrades[1].overrides["loci_floor"] == [0.3]
    assert t.corpus_regrades[2].overrides["n_results"] == [20, 30]


def test_scalar_knob_normalized_to_list():
    t = compile_topology(_cfg([{"Name": "s", "rrf_k": 60, "loci_weight": 0.5}]))
    assert t.corpus_regrades[0].overrides["rrf_k"] == [60]
    assert t.corpus_regrades[0].overrides["loci_weight"] == [0.5]   # int coerced to float ok too


def test_int_coerced_to_float_for_weight():
    t = compile_topology(_cfg([{"Name": "s", "loci_weight": [1]}]))
    assert t.corpus_regrades[0].overrides["loci_weight"] == [1.0]


def test_unknown_knob_warns_not_fatal():
    t = compile_topology(_cfg([{"Name": "s", "whatever": "value", "rrf_k": [30]}]))
    assert any("unknown knob 'whatever'" in w for w in t.warnings)
    assert t.corpus_regrades[0].overrides == {"rrf_k": [30]}     # good knob still kept


def test_bad_type_errors():
    with pytest.raises(TopologyError) as ei:
        compile_topology(_cfg([{"Name": "s", "rrf_k": ["sixty"]}]))
    assert any("wants int values" in e for e in ei.value.errors)


def test_missing_name_errors():
    with pytest.raises(TopologyError) as ei:
        compile_topology(_cfg([{"rrf_k": [30]}]))
    assert any("needs a unique Name" in e for e in ei.value.errors)


def test_duplicate_set_name_errors():
    with pytest.raises(TopologyError) as ei:
        compile_topology(_cfg([{"Name": "dup"}, {"Name": "dup"}]))
    assert any("used 2x" in e for e in ei.value.errors)


def test_regrades_not_a_list_errors():
    with pytest.raises(TopologyError) as ei:
        compile_topology(_cfg({"Name": "nope"}))
    assert any("must be a list" in e for e in ei.value.errors)


def test_no_regrades_is_empty():
    cfg = {"ProbeConfig": {"StartingPort": 7420,
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]}}}
    t = compile_topology(cfg)
    assert t.corpus_regrades == [] and t.warnings == []
