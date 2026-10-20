"""
A Loci fact's past: history, change reasons, and stale_key.

19 Oct 2026. Loci changes a fact by supersession: set the key again and the old value
is kept as a retired row, out of an ordinary search. Probe could not seed that (two
items with one project/key are a hard error, rightly - the second write would silently
replace the first) and could not grade it (a retired row is the same key under another
id, so expect_key cannot tell the old answer from the new). The Loci work on the punch
list (a change reason kept on the retired row, a second vector for the why) is
measured here before and after, so Probe has to speak it first. The reason shipped in
Loci that evening as `why_changed` (not `because`, the name Probe guessed: next to `why`
it read like a second why); POST /fact echoes the one it kept.

Pinned:
- a loci item carries history: (oldest first) and why_changed:; the seeder writes the
  history in order, then the live value, each with its reason
- a Loci that keeps no reasons (no why_changed echoed) is noticed and said, not
  silently dropped
- stale_key resolves to the key's RETIRED ids via /fact/history and is counted with
  the same stale_* counters as stale_ref
- the linter refuses a stale_key the seed never changed
- against the real SerenLoci app: the history lands with its reasons on the retired
  rows, the live value answers, and a retired value does not come back from an
  ordinary search
"""
from __future__ import annotations

import pytest

from seren_probe.core.seed_dataset import (
    LociItem, LociVersion, Question, SeedError, SeedResult, load_questions, load_seed_items,
    seed_from_plan,
)
from seren_probe.core.topology import compile_topology
from seren_probe.runtime.live_eval import run_topology_evaluation


def _topo():
    return compile_topology({"ProbeConfig": {"StartingPort": 7560,
        "Loci": {"LociCount": 1, "LociConfigs": [{"Name": "la", "Port": 7561}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7562}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7563, "Stores": [{"Store": "la"}, {"Store": "m"}]}]}}})


def _urls(t):
    out = {n.name: f"http://127.0.0.1:{n.port}" for n in t.loci + t.memory}
    out.update({c.name: f"http://127.0.0.1:{c.port}" for c in t.corpus})
    return out


RULER = LociItem("vellmarch", "realm.ruler", "Queen Asha",
                 why="crowned at Highmere in 412",
                 why_changed="King Doran fell at the Siege of Corrow",
                 history=[LociVersion("King Aldric", "the founding line"),
                          LociVersion("King Doran", "Aldric's son", why_changed="Aldric died of the grey cough")])


# ── seed shape ──────────────────────────────────────────────────────────────

def test_a_loci_item_carries_its_history_oldest_first():
    warnings: list[str] = []
    it = load_seed_items([{"project": "p", "key": "k", "value": "three", "why_changed": "two was wrong",
                           "history": [{"value": "one", "why": "first"},
                                       {"value": "two", "why_changed": "one was wrong"}]}],
                         "loci", warnings)[0]
    assert [v.value for v in it.history] == ["one", "two"]
    assert it.history[1].why_changed == "one was wrong" and it.why_changed == "two was wrong"
    assert not warnings


def test_reasons_that_explain_nothing_warn_and_unknown_keys_are_named():
    warnings: list[str] = []
    it = load_seed_items([{"key": "k", "value": "v", "why_changed": "nothing came before"},
                          {"key": "j", "value": "v", "colour": "red", "because": "the old name",
                           "history": [{"value": "u", "why_changed": "nothing before me either"}]}],
                         "loci", warnings)
    assert it[0].why_changed is None and it[1].history[0].why_changed is None
    assert any("'why_changed' with no history" in w for w in warnings)
    assert any("because" in w and "IGNORED" in w for w in warnings)   # the guessed name is not read
    assert any("history[0]" in w for w in warnings)
    assert any("colour" in w for w in warnings)
    with pytest.raises(SeedError, match="history"):
        load_seed_items([{"key": "k", "value": "v", "history": [{"why": "no value"}]}], "loci")


def test_the_seeder_writes_the_past_then_the_present_each_with_its_reason():
    calls = []

    def post(url, path, body):
        calls.append(body)
        return {"ok": True, "why_changed": body.get("why_changed")}
    t = _topo()
    res = seed_from_plan(t, {"la": [RULER], "m": []}, _urls(t), post=post)
    assert [c["value"] for c in calls] == ["King Aldric", "King Doran", "Queen Asha"]
    assert "why_changed" not in calls[0]
    assert calls[1]["why_changed"] == "Aldric died of the grey cough"
    assert calls[2]["why_changed"] == "King Doran fell at the Siege of Corrow"
    assert res.loci_counts["la"] == 1 and res.key_index == {"vellmarch/realm.ruler": ["la"]}
    assert res.notes == []


def test_a_loci_that_keeps_no_reasons_is_said_not_silent():
    t = _topo()
    res = seed_from_plan(t, {"la": [RULER], "m": []}, _urls(t),
                         post=lambda u, p, b: {"ok": True, "fact": {}, "superseded": "x"})
    assert len(res.notes) == 1 and "2 change reason(s)" in res.notes[0]
    # An echo of null is a reason the store had nowhere to keep: dropped all the same.
    res = seed_from_plan(t, {"la": [RULER], "m": []}, _urls(t),
                         post=lambda u, p, b: {"ok": True, "fact": {}, "superseded": None, "why_changed": None})
    assert len(res.notes) == 1 and "2 change reason(s)" in res.notes[0]


def test_progress_counts_every_write_a_history_makes():
    from seren_probe.core.seed_dataset import _seed_item_weight
    assert _seed_item_weight("loci", RULER) == 3
    assert _seed_item_weight("loci", LociItem("p", "k", "v")) == 1


# ── questions and the linter ────────────────────────────────────────────────

def test_stale_key_needs_the_answer_that_is_right_now():
    q = load_questions([{"asks": "loci", "query": "who rules", "expect_key": ["vellmarch/realm.ruler"],
                         "stale_key": ["vellmarch/realm.ruler"]}])[0]
    assert q.stale_key == ["vellmarch/realm.ruler"]
    with pytest.raises(SeedError, match="stale_key"):
        load_questions([{"asks": "loci", "query": "q", "expect_empty": True, "stale_key": ["p/k"]}])
    with pytest.raises(SeedError, match="stale_key"):
        load_questions([{"asks": "loci", "query": "q", "expect_key": ["p/k"], "stale_key": "p/k"}])


def test_the_linter_refuses_a_stale_key_that_never_changed():
    from seren_probe.core.linters.adapters import _item_dicts, _question_to_dict
    from seren_probe.core.linters.plan import lint_questions
    items = [RULER, LociItem("vellmarch", "realm.capital", "Highmere")]
    docs = [d for it in items for d in _item_dicts(it)]

    def lint(stale):
        q = Question(asks="loci", query="who rules vellmarch", expect_key=["vellmarch/realm.ruler"],
                     stale_key=[stale])
        return lint_questions([_question_to_dict(q)], docs, []).errors
    assert not any("stale_key" in e for e in lint("vellmarch/realm.ruler"))
    assert any("no history" in e for e in lint("vellmarch/realm.capital"))
    assert any("NOT in the seed" in e for e in lint("vellmarch/realm.gone"))


# ── grading ────────────────────────────────────────────────────────────────

def _grade_loci(hits, history):
    """One loci column, one question, a canned search and a canned /fact/history."""
    t = _topo()
    url = _urls(t)
    q = Question(asks="loci", query="who rules vellmarch", expect_key=["vellmarch/realm.ruler"],
                 stale_key=["vellmarch/realm.ruler"])

    def post(u, path, body):
        return {"hits": hits if u == url["la"] else []} if path == "/search" else {}

    def get_params(u, path, params):
        if path == "/fact":
            return {"id": "live"}
        if path == "/fact/history":
            return {"history": history}
        return {}
    sr = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={}, key_index={})
    return run_topology_evaluation(t, url, [q], seed_result=sr, seed=False, post=post, delete=None,
                                   get_params=get_params, max_parallel_stores=1,
                                   max_parallel_questions=1)["stores"]["la"]


HISTORY = [{"id": "live", "superseded_at": None},
           {"id": "doran", "superseded_at": 2.0},
           {"id": "aldric", "superseded_at": 1.0}]


def test_a_retired_value_ranked_is_the_old_answer_coming_back():
    snap = _grade_loci([{"id": "live", "score": 0.9, "key": "realm.ruler", "value": "Queen Asha"},
                        {"id": "doran", "score": 0.8, "key": "realm.ruler", "value": "King Doran"}],
                       HISTORY)
    assert snap["stale_count"] == 1 and snap["stale_in_packet"] == 1 and snap["stale_rate"] == 1.0
    assert snap["per_query"]["hit_rates"] == [1.0]


def test_only_retired_rows_count_and_the_live_one_never_does():
    snap = _grade_loci([{"id": "live", "score": 0.9, "key": "realm.ruler", "value": "Queen Asha"}], HISTORY)
    assert snap["stale_count"] == 1 and snap["stale_in_packet"] == 0 and snap["stale_rate"] == 0.0


def test_a_key_with_no_retired_rows_is_not_counted_as_a_stale_test():
    snap = _grade_loci([{"id": "live", "score": 0.9}], [{"id": "live", "superseded_at": None}])
    assert "stale_count" not in snap


def test_a_seed_note_reaches_the_ground_truth_block():
    t = _topo()
    url = _urls(t)
    sr = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={}, key_index={},
                    notes=["la: 2 change reason(s) (`why_changed`) were not kept"])
    q = Question(asks="loci", query="q", expect_key=["p/k"])
    out = run_topology_evaluation(t, url, [q], seed_result=sr, seed=False,
                                  post=lambda u, p, b: {"hits": []}, delete=None,
                                  get_params=lambda *a: {}, max_parallel_stores=1, max_parallel_questions=1)
    assert any("change reason" in n for n in out.get("ground_truth", []))


# ── the real SerenLoci, in-process ──────────────────────────────────────────

@pytest.fixture
def loci(tmp_path):
    pytest.importorskip("seren_loci")
    from fastapi.testclient import TestClient
    from seren_loci.app import create_app
    from seren_loci.config import LociConfig, StorageConfig
    cfg = LociConfig(storage=StorageConfig(db_path=str(tmp_path / "loci.db"), embedding_model=None))
    with TestClient(create_app(cfg)) as tc:
        yield tc


def test_against_the_real_loci_the_past_lands_and_stays_out_of_search(loci):
    t = _topo()
    la = _urls(t)["la"]

    def post(url, path, body):
        if url != la:
            return {"hits": []}
        r = loci.post(path, json=body)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
        return r.json()

    def get_params(url, path, params):
        r = loci.get(path, params=params)
        return r.json() if r.status_code == 200 else {}

    res = seed_from_plan(t, {"la": [RULER], "m": []}, _urls(t), post=post)
    hist = loci.get("/fact/history", params={"project": "vellmarch", "key": "realm.ruler"}).json()["history"]
    assert [h["value"] for h in hist] == ["Queen Asha", "King Doran", "King Aldric"]
    assert hist[0]["superseded_at"] is None and all(h["superseded_at"] for h in hist[1:])
    keeps = "why_changed" in hist[0]
    if keeps:
        # each reason sits on the row it retired
        assert res.notes == []
        assert [h["why_changed"] for h in hist] == [None, "King Doran fell at the Siege of Corrow",
                                                    "Aldric died of the grey cough"]
    else:
        assert "2 change reason(s)" in res.notes[0]       # a Loci from before why_changed

    q = Question(asks="loci", query="King Doran Queen Asha ruler", expect_key=["vellmarch/realm.ruler"],
                 stale_key=["vellmarch/realm.ruler"])
    sr = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={}, key_index={})
    snap = run_topology_evaluation(t, _urls(t), [q], seed_result=sr, seed=False, post=post, delete=None,
                                   get_params=get_params, max_parallel_stores=1,
                                   max_parallel_questions=1)["stores"]["la"]
    assert snap["per_query"]["hit_rates"] == [1.0]
    assert snap["stale_count"] == 1 and snap["stale_in_packet"] == 0

    # The retired values ARE there for whoever asks for history.
    r = loci.post("/search", json={"query": "King Doran", "n_results": 5, "include_superseded": True}).json()
    doran = [h for h in r["hits"] if h.get("value") == "King Doran" and h.get("live") is False]
    assert doran
    if keeps:
        assert doran[0]["why_changed"] == "King Doran fell at the Siege of Corrow"   # rides on the hit
