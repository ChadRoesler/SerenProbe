"""
Satellites, supersessions, and long refs that point at rows that exist.

19 Oct 2026, two findings while mapping Probe for the comparison work:

1. THE DEAD ID. The seeder wrote a long item as /short -> /promote -> DELETE and
   recorded the /short id as the ref. Promote mints a NEW long id, so the first eval
   after every seed graded long-tier refs against nothing; only runs 2+ (rehydrated
   by text) came out right.
2. SATELLITES. Memory's long-term is a core and its surroundings. A seed could not
   say satellite or supersede (the fields were dropped), and the grader read only a
   hit's id and content, never matched_via or the Callosum's marked edges.

Pinned here, against the REAL SerenMemory app where the contract matters:
- long refs bind the id the review returned; satellites land on their core;
  a supersede demotes the core it names, in the same run or a later one
- rehydrate binds satellite and superseded refs by text
- a satellite answer is credited to the core that surfaced it (matched_via)
- an edge is never a rank; the answer it carries credits the core it hangs off
- expect_empty counts the packet; quiet_in sees matched_via text and edges
- stale_ref: an old answer ranked as an answer vs offered as a marked edge
- seed items: satellites/supersedes only on a long item, unknown keys warn
"""
from __future__ import annotations

import hashlib

import pytest

from seren_probe.core.seed_dataset import (
    MemoryItem, Question, SatelliteItem, SeedError, load_questions, load_seed_items,
    rehydrate_ref_map, seed_from_plan,
)
from seren_probe.core.topology import compile_topology
from seren_probe.runtime.live_eval import _grade, run_topology_evaluation


def _topo():
    return compile_topology({"ProbeConfig": {"StartingPort": 7540,
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "la", "Port": 7541}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7542}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7543, "Stores": [{"Store": "la"}, {"Store": "m"}]}]}}})


def _urls(t):
    out = {n.name: f"http://127.0.0.1:{n.port}" for n in t.loci + t.memory}
    out.update({c.name: f"http://127.0.0.1:{c.port}" for c in t.corpus})
    return out


# ── the real Memory, in-process ────────────────────────────────────────────────

class _Words:
    """Deterministic offline embedder: shared words -> nearby vectors."""
    _DIM = 64

    def __init__(self) -> None:
        pass

    @classmethod
    def name(cls) -> str:
        return "bow-probe-test"

    def get_config(self) -> dict:
        return {"dim": self._DIM}

    @classmethod
    def build_from_config(cls, config: dict) -> "_Words":
        return cls()

    def __call__(self, input):
        out = []
        for text in input:
            vec = [0.0] * self._DIM
            for tok in text.lower().split():
                vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self._DIM] += 1.0
            mag = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / mag for v in vec])
        return out


@pytest.fixture
def memory(tmp_path):
    pytest.importorskip("seren_memory")
    from fastapi.testclient import TestClient
    from seren_memory.app import create_app
    from seren_memory.config import ConsolidatorConfig, MemoryConfig, StorageConfig
    cfg = MemoryConfig(storage=StorageConfig(persist_dir=str(tmp_path / "memory")),
                       consolidator=ConsolidatorConfig(enabled=False, pruned_safety_days=1))
    app = create_app(cfg, embedding_function=_Words(), _allow_store_reset=True)
    with TestClient(app) as tc:
        yield tc
    try:
        app.state.store.close()
    except Exception:  # noqa: BLE001
        pass


def _bridge(memory, t):
    """post() for the harness: the memory store is the real app, the rest answer empty."""
    mem_url = _urls(t)["m"]

    def post(url, path, body):
        if url != mem_url:
            return {"hits": []}
        r = memory.post(path, json=body)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
        return r.json()
    return post


WORLD = [
    MemoryItem("long", "At the Battle of Frostford House Ostry broke the Iron Pact line",
               topic="battle", ref="frostford",
               satellites=[SatelliteItem("Lord Corwin Vayne was ransomed for nine hundred Highmere marks",
                                         ref="ransom")]),
    MemoryItem("short", "the comet was seen over the Ashfen", topic="omen", ref="comet"),
    MemoryItem("long", "House Vayne holds its seat at Grimsgate", topic="seat", ref="seat-1"),
    MemoryItem("long", "House Vayne moved its seat to Blackmere after the fire", topic="seat",
               ref="seat-2", supersedes="seat-1",
               satellites=[SatelliteItem("The fire at Grimsgate burned the great hall to the ground",
                                         ref="fire")]),
]


def _seeded(memory):
    t = _topo()
    post = _bridge(memory, t)
    res = seed_from_plan(t, {"la": [], "m": list(WORLD)}, _urls(t), post=post)
    return t, post, res


def test_long_refs_bind_rows_that_exist_and_satellites_land_on_their_core(memory):
    _, _, res = _seeded(memory)
    store = memory.app.state.store
    ids = {r: res.ref_to_id[f"m:{r}"] for r in ("frostford", "ransom", "comet", "seat-1", "seat-2", "fire")}
    rows = {r: store.get_by_id(i) for r, i in ids.items()}
    assert all(rows.values()), "every ref binds a row Memory holds"
    assert rows["frostford"]["metadata"]["kind"] == "core"
    assert rows["ransom"]["metadata"]["kind"] == "satellite"
    assert rows["ransom"]["metadata"]["core_id"] == ids["frostford"]
    assert rows["comet"]["tier"] == "short"
    assert rows["seat-1"]["metadata"]["superseded_by"] == ids["seat-2"]
    assert rows["fire"]["metadata"]["core_id"] == ids["seat-2"]
    assert res.memory_counts["m"] == 4


def test_rehydrate_binds_satellites_and_superseded_cores(memory):
    t, post, res = _seeded(memory)
    rebuilt, unresolved = rehydrate_ref_map(t, {"m": list(WORLD)}, _urls(t), post)
    assert unresolved == []
    for ref in ("frostford", "ransom", "seat-1", "seat-2", "fire"):
        assert rebuilt[f"m:{ref}"] == res.ref_to_id[f"m:{ref}"], ref


def _memory_snapshot(memory, questions):
    t, post, res = _seeded(memory)
    out = run_topology_evaluation(t, _urls(t), questions, seed_result=res, seed=False,
                                  post=post, delete=None, get_params=lambda *a: {},
                                  max_parallel_stores=1, max_parallel_questions=1)
    return out["stores"]["m"]


def test_the_first_eval_after_a_seed_finds_long_answers(memory):
    snap = _memory_snapshot(memory, [
        Question(asks="memory", query="Battle of Frostford Iron Pact line", expect_ref=["frostford"])])
    assert snap["per_query"]["hit_rates"] == [1.0]


def test_a_satellite_answer_is_credited_to_the_core_that_surfaced_it(memory):
    # expect_ref alone: an expect_content would match the core through the satellite's
    # text and credit it anyway, hiding whether the REF is credited.
    snap = _memory_snapshot(memory, [
        Question(asks="memory", query="ransomed for nine hundred Highmere marks", expect_ref=["ransom"])])
    assert snap["per_query"]["hit_rates"] == [1.0] and snap["per_query"]["recalls"] == [1.0]
    row = snap["q_detail"][0]["hits"][0]
    assert row["expected"] and "nine hundred" in row["via"]


def test_a_superseded_core_does_not_come_back_from_memory(memory):
    snap = _memory_snapshot(memory, [
        Question(asks="memory", query="where is the seat of House Vayne", expect_ref=["seat-2"],
                 stale_ref=["seat-1"])])
    assert snap["stale_count"] == 1 and snap["stale_in_packet"] == 0 and snap["stale_rate"] == 0.0


# ── grading, without a store ───────────────────────────────────────────────────

def _edge(id, source, edge_of, content):
    return {"id": id, "content": content, "score": 0.0,
            "metadata": {"source": source, "edge_of": edge_of}}


def test_an_edge_is_never_a_rank_and_credits_the_core_it_hangs_off():
    hits = [{"id": "c1", "content": "the battle", "score": 0.9},
            {"id": "o1", "content": "goats", "score": 0.5},
            _edge("s1", "satellite-edge", "c1", "ransomed for nine hundred marks"),
            _edge("t1", "topic-edge", None, "nine hundred again")]
    q = Question(asks="corpus", query="ransom", expect_ref=["s"], expect_content=["nine hundred marks"])
    retrieved, relevant, coverage, density = _grade(hits, q, "corpus", lambda k: "",
                                                    {"s": "s1"}.get, 10)
    assert [r[0] for r in retrieved] == ["c1", "o1"]
    assert relevant == {"c1"} and coverage == 1.0 and density == 0.0


def test_matched_via_credits_the_core_and_its_text_counts():
    hits = [{"id": "c1", "content": "the battle", "score": 0.9,
             "metadata": {"matched_via": {"id": "s1", "content": "ransomed for nine hundred marks"}}}]
    q = Question(asks="corpus", query="ransom", expect_ref=["s"], expect_content=["nine hundred marks"])
    _, relevant, coverage, density = _grade(hits, q, "corpus", lambda k: "", {"s": "s1"}.get, 10)
    assert relevant == {"c1"} and coverage == 1.0 and density == 1.0


def _eval_with(memory_hits, corpus_hits, questions):
    t = _topo()
    url = _urls(t)

    def post(u, path, body):
        if path == "/search":
            return {"hits": {url["m"]: memory_hits, url["c"]: corpus_hits}.get(u, [])}
        return {}
    return run_topology_evaluation(t, url, questions, seed=False, post=post, delete=None,
                                   get_params=lambda *a: {}, max_parallel_stores=1,
                                   max_parallel_questions=1)["stores"]


def test_expect_empty_counts_the_packet_not_its_edges():
    stores = _eval_with([], [_edge("t1", "topic-edge", None, "something near")],
                        [Question(asks="corpus", query="the phantom duke", expect_empty=True)])
    assert stores["c"]["empty_pass_rate"] == 1.0


def test_quiet_in_sees_a_leak_in_matched_via_and_in_an_edge():
    via = [{"id": "c1", "content": "goats", "score": 0.4,
            "matched_via": {"id": "s1", "content": "the tavern brawl at the Gilded Eel"}}]
    edge = [{"id": "c2", "content": "goats", "score": 0.4},
            _edge("s2", "satellite-edge", "c2", "the tavern brawl at the Gilded Eel")]
    q = [Question(asks="memory", query="tavern brawl", expect_content=["tavern brawl"], quiet_in=["m", "c"])]
    stores = _eval_with(via, edge, q)
    assert stores["m"]["quiet_rate"] == 0.0, "the satellite it came through is in front of the reader"
    assert stores["c"]["quiet_rate"] == 0.0, "so is every edge on the packet"


def test_stale_ref_tells_an_old_answer_ranked_from_one_offered_as_history():
    current = {"id": "new", "content": "Blackmere", "score": 0.9}
    old = {"id": "old", "content": "Grimsgate", "score": 0.8}
    q = Question(asks="corpus", query="seat", expect_content=["Blackmere"], stale_ref=["seat-1"])
    t = _topo()
    url = _urls(t)
    for corpus_hits, in_packet, as_edge in (([current, old], 1, 0),
                                            ([current, {**old, "score": 0.0,
                                                        "metadata": {"source": "supersedes-edge",
                                                                     "edge_of": "new"}}], 0, 1)):
        def post(u, path, body, hits=corpus_hits):
            return {"hits": hits if u == url["c"] else []} if path == "/search" else {}
        from seren_probe.core.seed_dataset import SeedResult
        sr = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={"seat-1": "old"}, key_index={})
        snap = run_topology_evaluation(t, url, [q], seed_result=sr, seed=False, post=post, delete=None,
                                       get_params=lambda *a: {}, max_parallel_stores=1,
                                       max_parallel_questions=1)["stores"]["c"]
        assert (snap["stale_in_packet"], snap["stale_as_edge"]) == (in_packet, as_edge)


# ── the seeder's shape, against a recorder ──────────────────────────────────────

def _recorder():
    calls, n, drafts = [], {"i": 0}, {}

    def post(url, path, body):
        calls.append((path, body))
        n["i"] += 1
        if path == "/drafts":
            drafts[f"d{n['i']}"] = body["operations"]
            return {"id": f"d{n['i']}"}
        if path.endswith("/review"):
            ops = drafts[path.split("/")[2]]
            return {"results": [{"op": d["op"], ("satellite_id" if ops[d["op"]]["kind"] == "attach"
                                                 else "long_term_id"): f"x{n['i']}-{d['op']}"}
                                for d in body["decisions"]]}
        return {"id": f"s{n['i']}"}
    return calls, post


def test_a_supersede_of_a_ref_that_never_lands_is_loud():
    t = _topo()
    _, post = _recorder()
    with pytest.raises(ValueError, match="nowhere"):
        seed_from_plan(t, {"la": [], "m": [MemoryItem("long", "x", ref="a", supersedes="nowhere")]},
                       _urls(t), post=post)


def test_a_long_run_is_cut_into_drafts_and_every_ref_still_binds():
    t = _topo()
    calls, post = _recorder()
    items = [MemoryItem("long", f"fact {i}", ref=f"r{i}") for i in range(100)]
    res = seed_from_plan(t, {"la": [], "m": items}, _urls(t), post=post)
    drafts = [b for p, b in calls if p == "/drafts"]
    assert len(drafts) == 3 and all(len(d["operations"]) <= 48 for d in drafts)
    assert all(res.ref_to_id[f"m:r{i}"] for i in range(100))


def test_seed_items_carry_satellites_only_on_a_long_core():
    warnings: list[str] = []
    items = load_seed_items([{"tier": "long", "content": "core", "ref": "c", "evidence": 3,
                              "satellites": [{"content": "ep", "ref": "e"}], "supersedes": "old"}],
                            "memory", warnings)
    assert items[0].satellites == [SatelliteItem("ep", None, "e")] and items[0].supersedes == "old"
    assert any("evidence" in w for w in warnings), "an unknown key is said, not dropped"
    with pytest.raises(SeedError, match="long-tier core"):
        load_seed_items([{"tier": "short", "content": "x", "satellites": [{"content": "y"}]}], "memory")


def test_the_linter_sees_a_satellite_ref_and_checks_stale_refs():
    from seren_probe.core.linters.adapters import _item_dicts, _question_to_dict
    from seren_probe.core.linters.plan import lint_questions
    docs = [d for it in WORLD for d in _item_dicts(it)]
    rep = lint_questions([_question_to_dict(Question(asks="memory", query="ransomed Highmere marks",
                                                     expect_ref=["ransom"], stale_ref=["gone"]))],
                         [], docs)
    assert not any("'ransom'" in e for e in rep.errors), "a satellite's ref is in the seed"
    assert any("stale_ref 'gone'" in e for e in rep.errors)


def test_a_question_carries_its_class_and_its_answer():
    """tags and answer are for the answer-level comparison; retrieval reads neither,
    and before 19 Oct a generated pack's every question warned 'unrecognized key'."""
    warnings: list[str] = []
    q = load_questions([{"asks": "memory", "query": "q", "expect_ref": ["r"], "tags": ["update", "supersession"],
                         "answer": "High King"}], warnings)[0]
    assert q.tags == ["update", "supersession"] and q.answer == "High King" and not warnings
    with pytest.raises(SeedError, match="tags"):
        load_questions([{"asks": "memory", "query": "q", "expect_ref": ["r"], "tags": "update"}])


def test_stale_ref_needs_the_answer_that_is_right_now():
    assert load_questions([{"asks": "memory", "query": "q", "expect_ref": ["new"],
                            "stale_ref": ["old"]}])[0].stale_ref == ["old"]
    with pytest.raises(SeedError, match="stale_ref"):
        load_questions([{"asks": "memory", "query": "q", "expect_empty": True, "stale_ref": ["old"]}])
