"""Tests for resolve_eval_inputs - the config-driven /eval/run decision logic."""
from pathlib import Path
import pytest
from seren_probe.core.topology import compile_topology
from seren_probe.core.resolve import resolve_eval_inputs
from seren_probe.core.seed_dataset import SeedError

EX = Path(__file__).parent / "fixtures"


def _config_driven_topo():
    return compile_topology({"ProbeConfig": {"StartingPort": 7420,
        "DefaultLociSeed": str(EX / "meridian.loci.yaml"),
        "DefaultMemorySeed": str(EX / "meridian.memory.yaml"),
        "DefaultQuestions": str(EX / "meridian.questions.yaml"),
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "lv", "Port": 7421, "Flags": ["vector"]}, {"Name": "lnv", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "mem", "Port": 7425}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "scc-v", "Port": 7424, "Stores": [{"Store": "lv"}, {"Store": "mem"}]},
            {"Name": "scc-nv", "Port": 7423, "Stores": [{"Store": "lnv"}, {"Store": "mem"}]}]}}})


def _preseeded_topo():
    # no seed refs anywhere -> "stores are pre-seeded, eval as-is"
    return compile_topology({"ProbeConfig": {"StartingPort": 7420,
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]}}})


def test_config_driven_no_body():
    ei = resolve_eval_inputs(_config_driven_topo(), {})
    assert len(ei.questions) == 10                       # from config DefaultQuestions
    assert ei.seed
    assert len(ei.seed_by_store["lv"]) == 15 and len(ei.seed_by_store["mem"]) == 9
    assert ei.warnings == []
    # Nothing declared its own Questions, so every store inherits the default filtered by
    # `asks` -- the pre-scoping behaviour, unchanged.
    assert set(ei.questions_by_store) == {"lv", "lnv", "mem", "scc-v", "scc-nv"}
    assert all(q.asks == "loci" for q in ei.questions_by_store["lv"])
    assert all(q.asks == "corpus" for q in ei.questions_by_store["scc-v"])


def test_body_questions_override():
    body = {"questions": [{"asks": "loci", "query": "q", "expect_key": ["meridian/api_port"]}]}
    ei = resolve_eval_inputs(_config_driven_topo(), body)
    assert len(ei.questions) == 1                        # body wins
    assert ei.seed_by_store is not None                 # still config-seeded
    # A body set is GLOBAL: it replaces the config's scoping wholesale. Leaving the
    # per-store map in place would score half the topology on the uploaded questions and
    # half on the config's -- an override that only half-overrides.
    assert ei.questions_by_store is None


def test_preseeded_config_does_not_plan_seed():
    ei = resolve_eval_inputs(_preseeded_topo(), {"questions": [
        {"asks": "loci", "query": "q", "expect_content": ["x"]}]})
    assert ei.seed_by_store is None
    assert ei.seed is False                              # eval as-is
    assert ei.warnings == []                             # no empty-seed noise


def test_preseeded_no_questions_yields_empty():
    ei = resolve_eval_inputs(_preseeded_topo(), {})
    assert ei.questions == [] and ei.seed is False       # route turns this into a 400


def test_malformed_body_questions_raises():
    with pytest.raises(SeedError):
        resolve_eval_inputs(_config_driven_topo(), {"questions": [{"asks": "bogus"}]})
