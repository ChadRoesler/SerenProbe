"""
The replay cuts a generous capture down to what each request asked for (24 Nov 2026).

The Callosum's satellites_per_core and satellite_order change what MEMORY sends
(surroundings_n, surroundings_by), so a plain capture could not sweep them: every
variant replayed the three newest satellites the capture happened to hold. A
generous capture holds every satellite of every core, in match order, with dates
and distances, and the replay rebuilds the answer to each ask. Pinned here:

- match order at n is the first n captured rows
- recent order is rebuilt the way Memory builds it: the n newest, the episode the
  core was found through moved to the front, no raw_distance on the rows
- a core found through a short in its orbit carries the short first, and the
  short is not counted among its satellites
- a request that names neither field is read as Memory reads it: 3, recent
- a plain replay (the default) hands the captured surroundings back untouched
- edges_off() still turns the satellites off whatever satellite_share says: the
  replay grader flattens a packet to ranks, and an edge left on comes back as one
  (the trap of 19 Oct and again of 24 Nov 2026)
"""
from __future__ import annotations

import asyncio

import pytest

from seren_probe.runtime.regrade import ReplayTransport, edges_off, surroundings_as_asked


def _sat(i, when, dist):
    return {"id": f"s{i}", "content": f"episode {i}", "created_at": float(when), "raw_distance": dist}


def _core(via=None):
    """Five satellites as a generous capture holds them: match order (the one the
    core was found through, the other matches nearest first, then the newest)."""
    hit = {"id": "c1", "tier": "long", "content": "the core", "raw_distance": 0.2, "metadata": {"kind": "core"},
           "surroundings": {"satellites": 5, "latest_satellite_at": 50.0, "supersedes": None, "superseded_by": None,
                            "recent": [_sat(2, 20, 0.20), _sat(1, 10, 0.31), _sat(4, 40, 0.44),
                                       _sat(5, 50, None), _sat(3, 30, None)]}}
    if via:
        hit["matched_via"] = via
    return hit


VIA_SAT = {"id": "s2", "content": "episode 2", "created_at": 20.0, "raw_distance": 0.20, "tier": "long"}


def _ids(hit):
    return [r["id"] for r in hit["surroundings"]["recent"]]


def test_match_order_is_a_prefix_of_the_capture():
    hit = _core(VIA_SAT)
    assert _ids(surroundings_as_asked(hit, 1, "match")) == ["s2"]
    assert _ids(surroundings_as_asked(hit, 3, "match")) == ["s2", "s1", "s4"]
    assert _ids(surroundings_as_asked(hit, 9, "match")) == ["s2", "s1", "s4", "s5", "s3"]
    assert surroundings_as_asked(hit, 3, "match")["surroundings"]["recent"][1]["raw_distance"] == 0.31


def test_recent_order_is_rebuilt_as_memory_builds_it():
    got = surroundings_as_asked(_core(VIA_SAT), 3, "recent")
    assert _ids(got) == ["s2", "s5", "s4"], "the one it was found through, then the two newest"
    assert all(set(r) == {"id", "content", "created_at"} for r in got["surroundings"]["recent"]), "no raw_distance"
    assert _ids(surroundings_as_asked(_core(), 3, "recent")) == ["s5", "s4", "s3"], "found by its own text: the newest"
    assert _ids(surroundings_as_asked(_core(VIA_SAT), 1, "recent")) == ["s2"]
    # the one it was found through is already among the three newest: it moves to the front, nothing is lost
    newest_via = {"id": "s5", "content": "episode 5", "created_at": 50.0, "raw_distance": 0.5, "tier": "long"}
    hit = _core(newest_via)
    hit["surroundings"]["recent"] = [_sat(5, 50, 0.5), _sat(1, 10, 0.6), _sat(4, 40, None), _sat(3, 30, None),
                                     _sat(2, 20, None)]
    assert _ids(surroundings_as_asked(hit, 3, "recent")) == ["s5", "s4", "s3"]


def test_a_short_in_orbit_rides_first_and_is_not_a_satellite():
    short = {"id": "sh9", "content": "a fresh short-term note", "created_at": 99.0, "raw_distance": 0.1, "tier": "short"}
    hit = _core(short)
    hit["surroundings"]["recent"] = [dict(short, **{}), *hit["surroundings"]["recent"]]
    hit["surroundings"]["recent"][0].pop("tier")
    assert _ids(surroundings_as_asked(hit, 3, "recent")) == ["sh9", "s5", "s4"]
    assert _ids(surroundings_as_asked(hit, 3, "match")) == ["sh9", "s2", "s1"]


def test_none_and_untouched():
    assert _ids(surroundings_as_asked(_core(VIA_SAT), 0, "match")) == []
    bare = {"id": "x", "content": "a short", "metadata": {}}
    assert surroundings_as_asked(bare, 3, "match") is bare
    hit = _core(VIA_SAT)
    before = [dict(r) for r in hit["surroundings"]["recent"]]
    surroundings_as_asked(hit, 2, "recent")
    assert hit["surroundings"]["recent"] == before, "the capture itself is never changed"


def _replay(payload, **kw):
    cap = {"q": {"http://memory.in": {"hits": [_core(VIA_SAT)]}}}
    t = ReplayTransport(cap, **kw)
    return asyncio.run(t.post_json("http://memory.in/search", {"query": "q", "n_results": 10, **payload}))["hits"][0]


def test_the_replay_answers_each_ask():
    assert _ids(_replay({}, surroundings=True)) == ["s2", "s5", "s4"], "said nothing: 3, recent, as Memory reads it"
    assert _ids(_replay({"surroundings_n": 5, "surroundings_by": "match"}, surroundings=True)) == \
        ["s2", "s1", "s4", "s5", "s3"]
    assert _ids(_replay({"surroundings_n": 1}, surroundings=True)) == ["s2"]
    assert _ids(_replay({"surroundings_n": 1, "surroundings_by": "match"})) == ["s2", "s1", "s4", "s5", "s3"], \
        "a plain replay hands back what was captured"


def test_edges_off_turns_satellites_off_whatever_the_share_says():
    pytest.importorskip("seren_corpus_callosum")
    from seren_corpus_callosum.config import FederationConfig, StoreConfig
    from seren_corpus_callosum.federation import Federation
    cap = {"q": {"http://memory.in": {"hits": [_core(VIA_SAT)]}}}
    kw = {"satellite_share": 0.4} if "satellite_share" in FederationConfig.__dataclass_fields__ else {}
    cfg = FederationConfig(stores=[StoreConfig(name="m", type="seren_memory", url="http://memory.in")],
                           **{**kw, **edges_off()})
    for budget in (None, 4000):
        fused = asyncio.run(Federation(cfg, ReplayTransport(cap, surroundings=True)).search("q", budget_tokens=budget))
        assert [f.hit.id for f in fused] == ["c1"], "the ranked row and nothing hung after it"
        assert "recent" not in fused[0].hit.metadata["surroundings"]
