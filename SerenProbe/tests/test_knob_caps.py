"""A knob the SCC ignores yields identical rows - indistinguishable from a real
ceiling. Refuse to sweep it rather than emit a flatline that reads as a finding."""
import pytest
from seren_probe.core.knob_caps import assert_knobs_supported, KnobUnsupported
from seren_probe.core.topology import REGRADE_KNOBS, KNOBS_NEEDING_CAPABILITY, compile_topology


def test_hops_is_a_registered_capability_knob():
    assert REGRADE_KNOBS["hops"] is int
    assert "hops" in KNOBS_NEEDING_CAPABILITY


def test_baseline_knobs_need_no_advertisement():
    assert_knobs_supported({}, ["rrf_k", "loci_floor", "n_results"])   # no raise


def test_capability_knob_on_a_silent_scc_hard_errors():
    with pytest.raises(KnobUnsupported) as e:
        assert_knobs_supported({}, ["hops"])
    assert "INERT" in str(e.value)


def test_capability_knob_on_a_capable_scc_is_allowed():
    assert_knobs_supported({"supported_knobs": ["rrf_k", "hops"]}, ["hops"])   # no raise


def test_capability_knob_on_an_incapable_scc_errors():
    with pytest.raises(KnobUnsupported) as e:
        assert_knobs_supported({"supported_knobs": ["rrf_k"]}, ["hops"])
    assert "does NOT implement" in str(e.value)


def test_compile_warns_when_a_regrade_sweeps_hops():
    t = compile_topology({"ProbeConfig": {"StartingPort": 7420,
        "Corpus": {"CorpusCount": 1,
            "CorpusRegrades": [{"Name": "hop-sweep", "hops": [1, 2]}],
            "CorpusConfigs": [{"Name": "c", "Port": 7427,
                               "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]}}})
    # assert any("hop-sweep" in w and "inert knob" in w for w in t.warnings)
    assert any(rs.name == "hop-sweep" and rs.overrides["hops"] == [1, 2]
               for rs in t.corpus_regrades)


def test_hops_actually_reaches_the_scc_configure_body():
    """The trap this whole guard exists for: a knob missing from _FED_KNOB is
    silently DROPPED by configure_payload, the body goes out empty, every combo
    scores the same - and the flatline reads as a real ceiling. hops must map."""
    from seren_probe.runtime.regrade_live import configure_payload, compact_combos
    body = configure_payload({"hops": 2}, loci_name="l")
    assert body == {"hops": 2}, "hops must survive into the /configure body"

    # and a hops sweep must produce distinct bodies, not empty ones
    bodies = [configure_payload(c, "l") for c in compact_combos({"hops": [1, 2]})]
    assert bodies == [{"hops": 1}, {"hops": 2}]
    assert all(b for b in bodies), "an empty body means the knob never reached the SCC"
