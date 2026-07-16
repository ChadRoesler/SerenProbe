"""Tests for seren_probe.docket - the SCC with/without-edges docket comparison."""
from seren_probe.core.topology import compile_topology
from seren_probe.core.docket import docket_comparison, format_docket_comparison


def _corpus_snap(coverage, density, recall):
    """A run_topology_evaluation-shaped corpus column snapshot."""
    return {"kind": "corpus", "aggregate": {
        "docket_coverage": coverage, "docket_density": density,
        "recall": recall, "hit_rate": 1.0, "mrr": 0.8}}


def _loci_snap():
    return {"kind": "seren_loci", "aggregate": {"hit_rate": 1.0, "recall": 0.9}}


# ── the canonical 2-SCC topology (scc-v over loci-v, scc-nv over loci-nv) ──
CFG = {"ProbeConfig": {
    "StartingPort": 7420,
    "Loci": {"LociCount": 2, "LociConfigs": [
        {"Name": "loci-v", "Port": 7421, "Flags": ["vector"]},
        {"Name": "loci-nv", "Port": 7422}]},
    "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "mem", "Port": 7420}]},
    "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
        {"Name": "scc-v", "Port": 7424, "Stores": [{"Store": "loci-v"}, {"Store": "mem"}]},
        {"Name": "scc-nv", "Port": 7423, "Stores": [{"Store": "loci-nv"}, {"Store": "mem"}]}]},
}}


def _report_from_pasted():
    """Chad's actual pasted run: no-vector BEAT vector on docket coverage."""
    return {"k": 10, "question_count": 30, "corpus_size": 600, "stores": {
        "loci-v": _loci_snap(), "loci-nv": _loci_snap(), "mem": _loci_snap(),
        "scc-v":  _corpus_snap(0.5331, 0.6967, 0.9250),
        "scc-nv": _corpus_snap(0.5850, 0.7033, 0.9544),
    }}


def test_topology_flavor_is_authoritative():
    topo = compile_topology(CFG)
    cmp = docket_comparison(_report_from_pasted(), topo)
    assert cmp["flavor_source"] == "topology"
    by = {c["name"]: c for c in cmp["columns"]}
    assert by["scc-v"]["flavor"] == "vector" and by["scc-v"]["label"] == "with edges"
    assert by["scc-nv"]["flavor"] == "lexical" and by["scc-nv"]["label"] == "without edges"
    # lexical sorts first (delta reads with − without)
    assert cmp["columns"][0]["name"] == "scc-nv"


def test_delta_sign_matches_pasted_run():
    topo = compile_topology(CFG)
    cmp = docket_comparison(_report_from_pasted(), topo)
    assert len(cmp["deltas"]) == 1
    d = cmp["deltas"][0]
    assert d["with_edges"] == "scc-v" and d["without_edges"] == "scc-nv"
    # edges (vector) LOST on coverage & density in that run - negative deltas
    assert d["docket_coverage"] == round(0.5331 - 0.5850, 4)
    assert d["docket_coverage"] < 0
    assert d["docket_density"] < 0
    assert d["recall"] == round(0.9250 - 0.9544, 4)


def test_format_block_renders_verdict():
    topo = compile_topology(CFG)
    out = format_docket_comparison(docket_comparison(_report_from_pasted(), topo))
    assert "with edges" in out and "without edges" in out
    assert "edges COST" in out       # coverage went down with edges
    assert "scc-nv" in out and "scc-v" in out


def test_name_heuristic_without_topology():
    """Legacy run_live_evaluation report: scc_vector / scc_no_vector, no topology."""
    report = {"k": 10, "stores": {
        "scc_vector":    _corpus_snap(0.5331, 0.6967, 0.9250),
        "scc_no_vector": _corpus_snap(0.5850, 0.7033, 0.9544),
    }}
    cmp = docket_comparison(report)      # no topology
    assert cmp["flavor_source"] == "name-heuristic"
    by = {c["name"]: c for c in cmp["columns"]}
    # 'scc_no_vector' contains 'vector' but must resolve lexical (negatives first)
    assert by["scc_no_vector"]["flavor"] == "lexical"
    assert by["scc_vector"]["flavor"] == "vector"
    assert len(cmp["deltas"]) == 1


def test_no_corpus_columns_is_a_note_not_a_crash():
    report = {"stores": {"loci-v": _loci_snap(), "mem": _loci_snap()}}
    cmp = docket_comparison(report)
    assert cmp["columns"] == [] and cmp["deltas"] == []
    assert "nothing to compare" in cmp["note"]


def test_single_scc_gives_delta_note():
    report = {"stores": {"scc-v": _corpus_snap(0.5, 0.6, 0.9)}}
    cmp = docket_comparison(report)
    assert len(cmp["columns"]) == 1 and cmp["deltas"] == []
    assert "need one vector" in cmp["note"]
