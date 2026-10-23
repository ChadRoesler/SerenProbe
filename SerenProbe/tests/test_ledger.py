"""
The run ledger (21 Oct 2026): every eval and regrade sweep is kept, keyed and
comparable, instead of one results file overwritten per run. Pinned here:

- a run is recorded under the state dir with its fingerprint, seed time,
  question hash, version pins, Probe version, duration and results; the list
  is newest first and filters exactly; load gives the results back; delete
  removes one
- two runs compare per store (every numeric metric both graded) and per tag
  (the questions' classes, from the drill-down rows), movers first, and say
  whether the question sets match
- the eval route records a run and the results carry its id; the ledger
  routes list, fetch, compare and delete
- a drill-down row carries the question's tags, so the per-tag table exists
- a write failure costs the record, never the run

Everything runs against a throwaway state dir in tmp_path.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seren_probe.app import create_app
from seren_probe.config import SerenProbeConfig
from seren_probe.core.topology import compile_topology
from seren_probe.runtime import ledger
from seren_probe.runtime.docker_env import configure_state_dir


@pytest.fixture(autouse=True)
def state_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("SEREN_PROBE_STATE_DIR", str(tmp_path / "state"))
    configure_state_dir("")
    yield tmp_path / "state"
    configure_state_dir("")


def _results(hit_rate: float, tags_hits: dict[str, tuple[int, int]]) -> dict:
    """A results dict shaped like run_topology_evaluation's: one store with a
    headline metric and drill-down rows whose tags come from tags_hits
    {tag: (n, within_k)}."""
    rows = []
    for tag, (n, hits) in tags_hits.items():
        for i in range(n):
            rows.append({"query": f"{tag} {i}", "tags": [tag], "rank": 1 if i < hits else None,
                         "hit": i < hits, "within_k": i < hits, "has_gt": True})
    return {"stores": {"L": {"hit_rate": hit_rate, "recall": hit_rate, "k": 5, "question_count": len(rows),
                             "flags": [], "q_detail": rows}},
            "question_count": len(rows), "k": 5, "date": "2026-10-21"}


def test_a_run_is_recorded_listed_loaded_and_deleted(state_in_tmp):
    a = ledger.record_run("eval", _results(0.5, {"short": (4, 2)}), project_name="p", fingerprint="f1",
                          seeded_at="2026-10-21T00:00:00", question_hash="q1", versions={"memory": "==5.4.0"},
                          started_at=time.time() - 3.5)
    assert a and a["kind"] == "eval" and a["duration_seconds"] >= 3.4 and a["versions"] == {"memory": "==5.4.0"}
    assert a["headline"] == {"L": {"hit_rate": 0.5, "recall": 0.5}}, "numeric metrics only, shape fields skipped"
    assert a["by_tag"] == {"L": {"short": {"n": 4, "within_k": 0.5}}}
    files = list((state_in_tmp / "runs").glob("*.json"))
    assert len(files) == 1 and files[0].name.endswith(f"-{a['id']}.json")
    b = ledger.record_run("regrade", {"stores": {}, "sweep": [1, 2]}, question_hash="q2")
    rows = ledger.list_runs()
    assert [r["id"] for r in rows] == [b["id"], a["id"]], "newest first"
    assert "results" not in rows[0] and "by_tag" not in rows[0], "the list is summaries"
    assert [r["id"] for r in ledger.list_runs(kind="eval")] == [a["id"]]
    assert [r["id"] for r in ledger.list_runs(question_hash="q2")] == [b["id"]]
    assert ledger.list_runs(bundle="nope") == []
    loaded = ledger.load_run(a["id"])
    assert loaded["results"]["stores"]["L"]["hit_rate"] == 0.5 and loaded["probe_version"]
    assert ledger.load_run("../etc") is None and ledger.load_run("zzzz") is None
    assert ledger.delete_run(a["id"]) is True and ledger.delete_run(a["id"]) is False
    assert [r["id"] for r in ledger.list_runs()] == [b["id"]]


def test_two_runs_compare_per_store_and_per_tag_with_movers_first(state_in_tmp):
    a = ledger.record_run("eval", _results(0.50, {"short": (4, 2), "why": (2, 0)}), question_hash="q1")
    b = ledger.record_run("eval", _results(0.75, {"short": (4, 4), "why": (2, 0)}), question_hash="q1")
    c = ledger.compare_runs(a, b)
    assert c["same_questions"] is True
    assert c["stores"]["L"]["hit_rate"] == {"a": 0.5, "b": 0.75, "delta": 0.25}
    assert c["by_tag"]["L"]["short"] == {"n_a": 4, "n_b": 4, "a": 0.5, "b": 1.0, "delta": 0.5}
    assert c["by_tag"]["L"]["why"]["delta"] == 0.0
    assert c["moved"][0]["metric"] in ("hit_rate", "recall") and c["moved"][0]["delta"] == 0.25
    d = ledger.record_run("eval", _results(0.75, {"short": (4, 4)}), question_hash="q9")
    assert ledger.compare_runs(a, d)["same_questions"] is False, "a different question set is a different experiment"


def test_a_bad_kind_is_refused_and_a_failed_write_is_not_fatal(state_in_tmp, monkeypatch):
    with pytest.raises(ValueError):
        ledger.record_run("sweep", {"stores": {}})
    # A folder under an ordinary file can't be made on any OS. ("Z:/nowhere" was
    # relative on Linux, so CI's mkdir made it and the write went through.)
    blocker = state_in_tmp.parent / "a-file"
    blocker.write_text("not a folder")
    monkeypatch.setattr(ledger, "runs_dir", lambda: blocker / "runs")
    assert ledger.record_run("eval", {"stores": {}}) is None


def _topo():
    return compile_topology({"ProbeConfig": {
        "StartingPort": 7520,
        "Loci":   {"LociCount": 1, "LociConfigs": [{"Name": "L", "Port": 7521}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "M", "Port": 7525}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [{"Name": "C", "Port": 7527,
                                                         "Stores": [{"Store": "L"}, {"Store": "M"}]}]},
    }})


def _pretend_topology_is_up(app, monkeypatch):
    topo = _topo()
    app.state.compiled_topology = topo
    app.state.topology_state = {
        "project_name": "probe-test", "seeded": True, "seeded_at": "2026-10-21T00:00:00",
        "url_of": {n.name: f"http://127.0.0.1:{n.port}" for n in topo.loci + topo.memory}
                  | {c.name: f"http://127.0.0.1:{c.port}" for c in topo.corpus},
    }
    from seren_probe.runtime import docker_env, live_eval
    from seren_probe.core import resolve as _resolve
    monkeypatch.setattr(docker_env, "save_topology_state", lambda state: None)
    monkeypatch.setattr(docker_env, "load_topology_state", lambda: {})

    class _Q:
        def __init__(self, q, tags):
            self.query, self.asks, self.expect_content, self.tags = q, "loci", ["x"], tags

    def _inputs(topology, body=None, **kw):
        return _resolve.EvalInputs(questions=[_Q("what colour", ["short"]), _Q("why blue", ["why"])],
                                   seed_by_store=None, warnings=[], seed=False, questions_by_store=None)
    monkeypatch.setattr(_resolve, "resolve_eval_inputs", _inputs)

    def _fake_eval(topology, url_of, questions, **kw):
        return _results(1.0, {"short": (1, 1), "why": (1, 0)})
    monkeypatch.setattr(live_eval, "run_topology_evaluation", _fake_eval)


@pytest.fixture
def client():
    with TestClient(create_app(SerenProbeConfig())) as c:
        yield c


def test_the_eval_route_records_a_run_and_the_ledger_routes_serve_it(client, monkeypatch, state_in_tmp):
    _pretend_topology_is_up(client.app, monkeypatch)
    r = client.post("/eval/run", json={})
    assert r.status_code == 200, r.text
    rid = r.json()["results"].get("run_id")
    assert rid, "the results name the run they became"
    assert client.get("/eval/results").json()["run_id"] == rid
    rows = client.get("/eval/runs").json()
    assert rows["count"] == 1 and rows["runs"][0]["id"] == rid and rows["runs"][0]["kind"] == "eval"
    assert rows["runs"][0]["question_hash"] and rows["runs"][0]["fingerprint"], "fingerprinted like the latest file"
    one = client.get(f"/eval/runs/{rid}").json()
    assert one["headline"]["L"]["hit_rate"] == 1.0 and one["by_tag"]["L"]["why"]["within_k"] == 0.0
    assert "q_detail" not in one["results"]["stores"]["L"], "leaned for the wire"
    r2 = client.post("/eval/run", json={})
    rid2 = r2.json()["results"]["run_id"]
    cmp = client.get("/eval/compare", params={"a": rid, "b": rid2}).json()
    assert cmp["same_questions"] is True and cmp["stores"]["L"]["hit_rate"]["delta"] == 0.0
    assert client.get("/eval/compare", params={"a": rid, "b": "nope"}).status_code == 404
    assert client.delete(f"/eval/runs/{rid}").status_code == 200
    assert client.get(f"/eval/runs/{rid}").status_code == 404
    assert client.get("/eval/runs").json()["count"] == 1
    on_disk = list((state_in_tmp / "runs").glob("*.json"))
    assert len(on_disk) == 1 and json.loads(on_disk[0].read_text(encoding="utf-8"))["id"] == rid2


def test_a_drill_down_row_carries_the_questions_tags():
    from seren_probe.runtime.live_eval import _question_detail

    class _Q:
        query, tags = "what colour", ["short", "paraphrase"]

    out = _question_detail(_Q(), hits=[], retrieved=[], relevant=set(), kind="loci", coverage=0.0, k=5)
    assert out["tags"] == ["short", "paraphrase"] and out["hit"] is False
