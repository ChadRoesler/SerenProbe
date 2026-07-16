"""Tests for the ProbeConfig data-plumbing schema: shared Questions, per-kind
default seeds, per-store Seed, and the NegativeTest (decoy) marker + its lints."""
import pytest
from seren_probe.core.topology import compile_topology, TopologyError


def _wrap(pc): return {"ProbeConfig": pc}


def test_seed_questions_negative_fields_parse():
    t = compile_topology(_wrap({
        "StartingPort": 7420,
        "DefaultQuestions": "q.yaml",
        "DefaultLociSeed": "loci.yaml",
        "DefaultMemorySeed": "mem.yaml",
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "loci-v", "Port": 7421, "Flags": ["vector"], "Seed": "special.yaml"},
            {"Name": "loci-decoy", "Port": 7422, "NegativeTest": True, "Seed": "decoy.yaml"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "mem", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "scc", "Port": 7427, "Stores": [{"Store": "loci-v"}, {"Store": "mem"}]}]},
    }))
    assert t.questions_ref == "q.yaml"
    assert t.default_loci_seed == "loci.yaml"
    assert t.default_memory_seed == "mem.yaml"
    by = {n.name: n for n in t.loci}
    assert by["loci-v"].seed_ref == "special.yaml" and by["loci-v"].negative_test is False
    assert by["loci-decoy"].seed_ref == "decoy.yaml" and by["loci-decoy"].negative_test is True
    assert t.warnings == []          # decoy has a seed and stands alone -> clean


def test_negative_without_seed_warns():
    t = compile_topology(_wrap({
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "decoy", "Port": 7421, "NegativeTest": True}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Port": 7427, "Stores": [{"Store": "m"}]}]},
    }))
    assert any("has no decoy Seed" in w for w in t.warnings)


def test_negative_with_decoy_standalone_is_clean():
    t = compile_topology(_wrap({
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "decoy", "Port": 7421, "NegativeTest": True, "Seed": "d.yaml"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Port": 7427, "Stores": [{"Store": "m"}]}]},
    }))
    assert t.warnings == []
    assert t.loci[0].negative_test and t.loci[0].seed_ref == "d.yaml"


def test_negative_fanned_by_scc_warns():
    t = compile_topology(_wrap({
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "decoy", "Port": 7421, "NegativeTest": True, "Seed": "d.yaml"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "decoy"}, {"Store": "m"}]}]},
    }))
    assert any("is fanned by corpus" in w for w in t.warnings)
    assert not any("has no decoy Seed" in w for w in t.warnings)   # it HAS a seed


def test_negative_excluded_from_catchall():
    t = compile_topology(_wrap({
        "StartingPort": 7420,
        "Loci": {"LociCount": 3, "LociConfigs": [
            {"Name": "real", "Port": 7421},
            {"Name": "extra", "Port": 7423},
            {"Name": "decoy", "Port": 7422, "NegativeTest": True, "Seed": "d.yaml"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "real"}, {"Store": "m"}]}]},
    }))
    catch = [c for c in t.corpus if c.is_catchall]
    assert len(catch) == 1
    swept = {s.name for s in catch[0].stores}
    assert "decoy" not in swept and "extra" in swept


def test_corpus_seed_field_warns_stray():
    t = compile_topology(_wrap({
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Seed": "nope.yaml", "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
    }))
    assert any("does nothing on a" in w for w in t.warnings)


def test_corpus_store_seed_without_store_errors():
    with pytest.raises(TopologyError) as ei:
        compile_topology(_wrap({
            "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
            "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
            "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
                {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Seed": "d.yaml"}]}]},
        }))
    assert any("has a 'Seed' but no 'Store'" in e for e in ei.value.errors)


def test_seed_type_and_negative_type_error():
    with pytest.raises(TopologyError) as ei:
        compile_topology(_wrap({
            "Loci": {"LociCount": 1, "LociConfigs": [
                {"Name": "l", "Port": 7421, "Seed": 123, "NegativeTest": "yes"}]},
            "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
            "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
                {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        }))
    errs = ei.value.errors
    assert any("Seed must be a string" in e for e in errs)
    assert any("NegativeTest must be true/false" in e for e in errs)


def test_default_questions_type_error():
    """A NON-ref type still errors. Note what is NOT here any more: a list of STRINGS.

    This test used to feed `["not", "a", "string"]` and expect a type error. It is now a
    perfectly legal value -- a list of refs, merged in order, which is what lets a
    character store draw `[chars/grishnak.yaml, world/lore.yaml]` instead of pre-composing
    the world lore into six files and watching it drift. So the type check narrowed: a list
    of strings is CONTENT, and whether those strings resolve is the loader's job, not the
    compiler's (a ref may be a path OR inline YAML -- the compiler cannot tell them apart
    and must not pretend to).
    """
    with pytest.raises(TopologyError) as ei:
        compile_topology(_wrap({
            "DefaultQuestions": {"not": "a ref"},
            "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
            "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
            "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
                {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        }))
    assert any("DefaultQuestions must be a string" in e for e in ei.value.errors)


def test_default_questions_accepts_a_list_of_refs():
    t = compile_topology(_wrap({
        "DefaultQuestions": ["a.yaml", "b.yaml"],
        "DefaultLociSeed": ["chars/grishnak.yaml", "world/lore.yaml"],
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
    }))
    assert t.questions_ref == ["a.yaml", "b.yaml"]
    assert t.default_loci_seed == ["chars/grishnak.yaml", "world/lore.yaml"]


def test_legacy_top_level_questions_is_refused_by_name():
    """`Questions` -> `DefaultQuestions`. REFUSED, not silently accepted.

    Quietly honouring both keys has no honest interpretation: whichever one you pick, the
    author believed the other. That is how you grade a set you did not think you were
    grading, and it fails looking like a bad store.
    """
    with pytest.raises(TopologyError) as ei:
        compile_topology(_wrap({
            "Questions": "q.yaml",
            "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
            "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
            "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
                {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        }))
    assert any("`Questions` is now `DefaultQuestions`" in e for e in ei.value.errors)


def test_per_node_questions_parse():
    t = compile_topology(_wrap({
        "DefaultQuestions": "world.yaml",
        "Loci": {"LociCount": 1, "LociConfigs": [
            {"Name": "l", "Port": 7421, "Questions": "loci_qs.yaml"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [
            {"Name": "m", "Port": 7425, "Questions": ["grishnak.yaml", "party.yaml"]}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Questions": "cross_store.yaml",
             "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
    }))
    assert t.loci[0].questions_ref == "loci_qs.yaml"
    assert t.memory[0].questions_ref == ["grishnak.yaml", "party.yaml"]
    # Questions on a CORPUS are the cross-store ones -- the questions no single fanned
    # store can answer alone. They are the whole point, so they must NOT warn as a stray.
    assert t.corpus[0].questions_ref == "cross_store.yaml"
    assert not any("does nothing on a" in w for w in t.warnings)


def test_backward_compat_no_new_fields():
    """A config with none of the new fields compiles exactly as before."""
    t = compile_topology(_wrap({
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
    }))
    assert t.questions_ref is None and t.default_loci_seed is None and t.default_memory_seed is None
    assert t.loci[0].seed_ref is None and t.loci[0].negative_test is False
    assert t.loci[0].questions_ref is None and t.corpus[0].questions_ref is None
    assert t.warnings == []
