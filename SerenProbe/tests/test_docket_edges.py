"""
What the Callosum's marked edges buy a briefing, and what they cost.

19 Oct 2026 (punch list: probe-docket-eval). A Callosum packet is the ranked hits,
then MARKED edges after it: a core's newest satellites, the core it superseded, topic
neighbours. The topology eval learned that morning that an edge is never a rank; the
live knob sweep (regrade_live) went on ranking every edge as a hit, so a sweep and
an eval of the same config disagreed. Both graders now read core/edges.py.

Pinned:
- surroundings(): coverage of the ranked packet alone, then with each edge family
  folded in; density with each family; rows and text per family
- the sweep's grade_corpus and the eval's _grade agree on the same packet
- a corpus column carries `surroundings`; the docket comparison carries and prints it
- the edge knobs compile (bool knobs take bools), map onto the Callosum's fields,
  sweep live only, and need the SCC to advertise them
"""
from __future__ import annotations

import pytest

from seren_probe.core.docket import docket_comparison, format_docket_comparison
from seren_probe.core.edges import mean_surroundings, surroundings
from seren_probe.core.seed_dataset import Question, SeedResult
from seren_probe.core.topology import compile_topology, TopologyError
from seren_probe.runtime.live_eval import _grade, run_topology_evaluation
from seren_probe.runtime.regrade_live import (
    PACKET_COUPLED_KNOBS, configure_payload, grade_corpus, is_packet_coupled,
)

CORE = {"id": "core", "score": 0.9, "content": "House Vayne moved its seat to Blackmere",
        "metadata": {"matched_via": {"id": "s0", "content": "the comet over the Ashfen"}}}
OTHER = {"id": "other", "score": 0.5, "content": "the harvest failed in the south"}
SAT = {"id": "s1", "score": 0.0, "content": "the fire burned the great hall",
       "metadata": {"source": "satellite-edge", "edge_of": "core"}}
OLD = {"id": "old", "score": 0.0, "content": "House Vayne holds its seat at Grimsgate",
       "metadata": {"source": "supersedes-edge", "edge_of": "core"}}
TOPIC = {"id": "t1", "score": 0.0, "content": "a feast at Blackmere", "metadata": {"source": "topic-edge"}}
HITS = [CORE, OTHER, SAT, OLD, TOPIC]
EXPECT = ["Blackmere", "great hall", "Ashfen", "never said anywhere"]


def test_surroundings_splits_coverage_by_edge_family():
    s = surroundings(HITS, EXPECT, k=10)
    # Blackmere (core) and Ashfen (the satellite the core came through) are in the packet
    assert s["coverage_packet"] == pytest.approx(2 / 4)
    assert s["coverage_with"]["satellite-edge"] == pytest.approx(3 / 4)     # + great hall
    assert s["coverage_with"]["supersedes-edge"] == pytest.approx(2 / 4)    # adds nothing new
    assert s["coverage_with"]["topic-edge"] == pytest.approx(2 / 4)         # Blackmere again
    # density: relevant / rows over the top-k packet plus that family
    assert s["density_with"]["satellite-edge"] == pytest.approx((1 + 1) / (2 + 1))
    assert s["density_with"]["supersedes-edge"] == pytest.approx((1 + 0) / (2 + 1))
    assert s["rows"] == {"packet": 2, "satellite-edge": 1, "supersedes-edge": 1, "topic-edge": 1}
    assert s["chars"]["satellite-edge"] == len(SAT["content"]) + 1    # content + empty via, joined


def test_sizes_without_content_and_the_column_mean():
    s = surroundings(HITS, [], k=10)
    assert "coverage_packet" not in s and s["rows"]["packet"] == 2
    m = mean_surroundings([surroundings(HITS, EXPECT, 10), surroundings([CORE], ["Blackmere"], 10)])
    assert m["questions"] == 2 and m["graded"] == 2
    assert m["coverage_packet"] == pytest.approx((0.5 + 1.0) / 2)
    assert m["rows"]["satellite-edge"] == pytest.approx(0.5)


def test_the_sweep_and_the_eval_grade_one_packet_the_same():
    """grade_corpus ranked every edge until 19 Oct; now it reads the packet as _grade does."""
    q = Question(asks="corpus", query="q", expect_content=EXPECT)
    sweep = grade_corpus(HITS, EXPECT, k=10)
    evald = _grade(HITS, q, "corpus", lambda pk: "", lambda r: "", 10)
    assert sweep[0] == evald[0] == [("core", 0.9), ("other", 0.5)]      # edges never rank
    assert sweep[1] == evald[1] == {"core"}                              # the edge credits its core
    assert sweep[2] == pytest.approx(evald[2]) and sweep[3] == pytest.approx(evald[3])
    assert sweep[3] == pytest.approx(1 / 2)                              # density over the packet


def test_a_corpus_column_carries_its_surroundings_and_the_comparison_shows_them():
    t = compile_topology({"ProbeConfig": {"StartingPort": 7580,
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "la", "Port": 7581, "Flags": ["vector"]}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7582}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7583, "Stores": [{"Store": "la"}, {"Store": "m"}]}]}}})
    url = {n.name: f"http://127.0.0.1:{n.port}" for n in t.loci + t.memory}
    url["c"] = "http://127.0.0.1:7583"
    q = Question(asks="corpus", query="where is the seat", expect_content=EXPECT)

    def post(u, path, body):
        return {"hits": HITS if u == url["c"] else []} if path == "/search" else {}
    sr = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={}, key_index={})
    out = run_topology_evaluation(t, url, [q], seed_result=sr, seed=False, post=post, delete=None,
                                  get_params=lambda *a: {}, max_parallel_stores=1, max_parallel_questions=1)
    s = out["stores"]["c"]["surroundings"]
    assert s["coverage_packet"] == pytest.approx(0.5)
    assert s["coverage_with"]["satellite-edge"] == pytest.approx(0.75)
    assert "surroundings" not in out["stores"]["la"]                 # only a corpus column has edges
    cmp = docket_comparison(out, t)
    assert cmp["columns"][0]["surroundings"] == s
    text = format_docket_comparison(cmp)
    assert "+satellite 0.7500" in text and "packet 0.5000" in text


def _cfg(regrades):
    return {"ProbeConfig": {"StartingPort": 7420,
        "Corpus": {"CorpusRegrades": regrades, "CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "l", "Port": 7421}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]}}}


def test_the_edge_knobs_compile_map_and_sweep_live():
    t = compile_topology(_cfg([{"Name": "sats", "satellite_edges_enabled": [True, False],
                                "satellite_budget": [0, 3, 8]},
                               {"Name": "topic", "edges_enabled": False, "edge_budget": 2}]))
    assert t.warnings == []
    sats, topic = t.corpus_regrades
    assert sats.overrides == {"satellite_edges_enabled": [True, False], "satellite_budget": [0, 3, 8]}
    assert is_packet_coupled(sats) and is_packet_coupled(topic)
    assert {"edges_enabled", "edge_budget", "satellite_edges_enabled", "satellite_budget"} <= PACKET_COUPLED_KNOBS
    body = configure_payload({"satellite_edges_enabled": False, "satellite_budget": 0, "edge_budget": 2}, {})
    assert body == {"satellite_edges_enabled": False, "satellite_budget": 0, "edge_budget": 2}


def test_a_bool_knob_takes_only_a_bool_and_a_number_knob_still_refuses_one():
    with pytest.raises(TopologyError, match="satellite_edges_enabled"):
        compile_topology(_cfg([{"Name": "x", "satellite_edges_enabled": [1]}]))
    with pytest.raises(TopologyError, match="satellite_budget"):
        compile_topology(_cfg([{"Name": "x", "satellite_budget": [True]}]))


def test_a_corpus_can_boot_with_the_satellite_edges_off():
    cfg = _cfg([])
    cfg["ProbeConfig"]["Corpus"]["CorpusConfigs"][0]["Config"] = {"satellite_edges_enabled": False}
    t = compile_topology(cfg)
    assert t.corpus[0].config == {"satellite_edges_enabled": False}
    from seren_probe.core.topology_emit import _corpus_yaml
    fed = _corpus_yaml(t.corpus[0], {"l": 7421, "m": 7425})["federation"]
    assert fed["satellite_edges_enabled"] is False


def test_the_capture_replay_hangs_no_edges_on_its_packet():
    """The replay turned topic edges off and left satellite edges ON (the Callosum's
    default); _fused_to_dicts then dropped the marker, so a captured core's satellite
    came back as an unmarked score-0.0 rank and its text counted as coverage."""
    pytest.importorskip("seren_corpus_callosum")
    import asyncio
    from types import SimpleNamespace
    import dataclasses
    from seren_corpus_callosum.config import FederationConfig, StoreConfig
    from seren_probe.runtime.regrade import build_fed_config, edges_off, regrade_one
    if "satellite_edges_enabled" not in {f.name for f in dataclasses.fields(FederationConfig)}:
        assert edges_off() == {"edges_enabled": False}       # an old Callosum is not handed a field it lacks
        pytest.skip("the installed seren-corpus-callosum predates satellite edges")
    url = "http://127.0.0.1:7425"
    hit = {"id": "core", "content": "House Vayne moved its seat to Blackmere", "score": 0.9,
           "raw_distance": 0.2, "tier": "long", "metadata": {"kind": "core"},
           "surroundings": {"satellites": 1, "recent": [{"id": "s1", "content": "the fire burned the great hall",
                                                         "created_at": 1.0}]}}
    cfg = build_fed_config([StoreConfig(name="m", type="seren_memory", url=url)], rrf_k=60, loci_weight=1.0,
                           loci_floor=0.0, authority=0.0, min_per_store=0, fusion_mode="rrf",
                           n_results=10, fetch_multiplier=1)
    assert cfg.satellite_edges_enabled is False and cfg.edges_enabled is False
    q = SimpleNamespace(query="seat", expected_content=["Blackmere", "great hall"], expected_ids=[])
    agg, misses = asyncio.run(regrade_one({"seat": {url: {"hits": [hit]}}}, cfg, [q], eval_k=10))
    assert misses == 0
    assert agg["docket_coverage"] == pytest.approx(0.5)          # the core, not its satellite
    assert agg["docket_coverage_packet"] == agg["docket_coverage"]
