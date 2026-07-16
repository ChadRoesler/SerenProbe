"""Tests for the seed/question resolver (seren_probe.resolve)."""
from pathlib import Path
from seren_probe.core.topology import compile_topology
from seren_probe.core.resolve import resolve_plan
from seren_probe.core.seed_dataset import LociItem, MemoryItem, Question

EXAMPLES = Path(__file__).parent / "fixtures"


def _fake_loader(mapping):
    # (ref, kind, warnings) -- the loaders take a warnings OUT-PARAM now. Without it a
    # clean load DISCARDED its warnings (the unknown-key "IGNORED, not applied" one
    # included), so the parser's loudest safety net only fired when something else was
    # already broken. Fakes have to mirror the real signature or they test a contract
    # nothing implements.
    def _load(ref, kind, warnings=None):
        return list(mapping.get(ref, []))
    return _load


def _fake_qs(*questions):
    """load_qs fake: (ref, warnings) -> the given questions."""
    def _load(ref, warnings=None):
        return list(questions)
    return _load


def _compile(**top_level):
    pc = {"StartingPort": 7420,
          "Loci": {"LociCount": 2, "LociConfigs": [
              {"Name": "la", "Port": 7421}, {"Name": "lb", "Port": 7422}]},
          "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
          "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
              {"Name": "c", "Port": 7427, "Stores": [{"Store": "la"}, {"Store": "m"}]}]}}
    pc.update(top_level)
    return compile_topology({"ProbeConfig": pc})


def test_default_seeds_by_kind():
    t = _compile(DefaultLociSeed="L.yaml", DefaultMemorySeed="M.yaml", DefaultQuestions="Q.yaml")
    li = _fake_loader({"L.yaml": [LociItem("*", "k", "v")], "M.yaml": [MemoryItem("short", "x")]})
    plan = resolve_plan(t, load_items=li, load_qs=_fake_qs(Question("loci", "q", expect_key=["*/k"])))
    assert plan.seed_by_store["la"][0].key == "k"
    assert plan.seed_by_store["lb"][0].key == "k"       # both loci draw the default
    assert plan.seed_by_store["m"][0].tier == "short"
    assert len(plan.questions) == 1
    assert plan.warnings == []


def test_per_node_seed_override_beats_default():
    t = compile_topology({"ProbeConfig": {"StartingPort": 7420, "DefaultLociSeed": "L.yaml",
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "la", "Port": 7421, "Seed": "special.yaml"}, {"Name": "lb", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425, "Seed": "m.yaml"}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "la"}, {"Store": "m"}]}]}}})
    li = _fake_loader({"L.yaml": [LociItem("*", "default", "v")],
                       "special.yaml": [LociItem("*", "special", "v")],
                       "m.yaml": [MemoryItem("short", "x")]})
    plan = resolve_plan(t, load_items=li, load_qs=_fake_qs(Question("loci", "q", expect_content=["x"])))
    assert plan.seed_by_store["la"][0].key == "special"   # override wins
    assert plan.seed_by_store["lb"][0].key == "default"   # falls back to default


def test_negative_gets_only_decoy_never_default():
    t = compile_topology({"ProbeConfig": {"StartingPort": 7420,
        "DefaultLociSeed": "L.yaml", "DefaultMemorySeed": "M.yaml", "DefaultQuestions": "Q.yaml",
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "real", "Port": 7421},
            {"Name": "decoy", "Port": 7422, "NegativeTest": True, "Seed": "decoy.yaml"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "real"}, {"Store": "m"}]}]}}})
    li = _fake_loader({"L.yaml": [LociItem("*", "real", "v")], "M.yaml": [MemoryItem("short", "x")],
                       "decoy.yaml": [LociItem("*", "decoy", "v")]})
    plan = resolve_plan(t, load_items=li, load_qs=_fake_qs(Question("loci", "q", expect_key=["*/real"])))
    assert plan.seed_by_store["real"][0].key == "real"    # default corpus
    assert [i.key for i in plan.seed_by_store["decoy"]] == ["decoy"]   # ONLY the decoy, never L.yaml


def test_negative_without_decoy_seeds_empty_quietly():
    t = compile_topology({"ProbeConfig": {"StartingPort": 7420,
        "DefaultLociSeed": "L.yaml", "DefaultMemorySeed": "M.yaml", "DefaultQuestions": "Q.yaml",
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "real", "Port": 7421},
            {"Name": "decoy", "Port": 7422, "NegativeTest": True}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "real"}, {"Store": "m"}]}]}}})
    li = _fake_loader({"L.yaml": [LociItem("*", "real", "v")], "M.yaml": [MemoryItem("short", "x")]})
    plan = resolve_plan(t, load_items=li, load_qs=_fake_qs(Question("loci", "q", expect_content=["x"])))
    assert plan.seed_by_store["decoy"] == []
    assert not any("decoy" in w for w in plan.warnings)   # resolver stays quiet (compiler already warned)


def test_nonneg_store_without_seed_source_warns():
    t = _compile(DefaultQuestions="Q.yaml")   # no DefaultLociSeed
    li = _fake_loader({})
    plan = resolve_plan(t, load_items=li, load_qs=_fake_qs(Question("loci", "q", expect_content=["x"])))
    assert plan.seed_by_store["la"] == [] and plan.seed_by_store["lb"] == []
    assert any("loci store 'la' has no seed source" in w for w in plan.warnings)
    assert any("DefaultLociSeed" in w for w in plan.warnings)


def test_missing_questions_warns():
    t = _compile(DefaultLociSeed="L.yaml", DefaultMemorySeed="M.yaml")   # no DefaultQuestions
    li = _fake_loader({"L.yaml": [LociItem("*", "k", "v")], "M.yaml": [MemoryItem("short", "x")]})
    plan = resolve_plan(t, load_items=li, load_qs=_fake_qs())
    assert plan.questions == []
    assert any("no DefaultQuestions" in w for w in plan.warnings)


def test_resolve_real_example_files_end_to_end():
    """Real loaders + the shipped flat example files -> a concrete plan."""
    t = compile_topology({"ProbeConfig": {"StartingPort": 7420,
        "DefaultLociSeed": str(EXAMPLES / "meridian.loci.yaml"),
        "DefaultMemorySeed": str(EXAMPLES / "meridian.memory.yaml"),
        "DefaultQuestions": str(EXAMPLES / "meridian.questions.yaml"),
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "lv", "Port": 7421, "Flags": ["vector"]}, {"Name": "lnv", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "mem", "Port": 7425}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "scc-v", "Port": 7424, "Stores": [{"Store": "lv"}, {"Store": "mem"}]},
            {"Name": "scc-nv", "Port": 7423, "Stores": [{"Store": "lnv"}, {"Store": "mem"}]}]}}})
    plan = resolve_plan(t)   # real load_seed_items / load_questions
    assert len(plan.seed_by_store["lv"]) == 15 and len(plan.seed_by_store["lnv"]) == 15
    assert len(plan.seed_by_store["mem"]) == 9
    assert len(plan.questions) == 10
    assert plan.warnings == []
