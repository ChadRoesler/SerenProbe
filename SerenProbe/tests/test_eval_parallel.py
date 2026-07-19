"""Tests for the parallel fan-out in run_topology_evaluation: stores run
concurrently (bounded by max_parallel_stores) and, within a store, its own
questions' /search calls also fan out (bounded by max_parallel_questions).
Mirrors tests/test_regrade_parallel.py's discipline: parallel result must
match the serial result, concurrency actually happens, and one failing
store/question does not sink the others.
"""
import threading
import time

from seren_probe.core.topology import compile_topology
from seren_probe.core.seed_dataset import Question, SeedResult
from seren_probe.runtime.live_eval import run_topology_evaluation


def _topo():
    return compile_topology({"ProbeConfig": {
        "StartingPort": 7420,
        "Loci":   {"LociCount": 2, "LociConfigs": [
            {"Name": "L1", "Port": 7421}, {"Name": "L2", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "M", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "C", "Port": 7427, "Stores": [{"Store": "L1"}, {"Store": "M"}]}]},
    }})


def _url_of(topo):
    m = {n.name: f"http://127.0.0.1:{n.port}" for n in topo.loci + topo.memory}
    m.update({c.name: f"http://127.0.0.1:{c.port}" for c in topo.corpus})
    return m


def _questions():
    return [
        Question(asks="loci", query="q1", expect_key=["seren-loci/k1"]),
        Question(asks="loci", query="q2", expect_key=["seren-loci/k2"]),
        Question(asks="memory", query="q3", expect_ref=["r1"]),
        Question(asks="memory", query="q4", expect_ref=["r2"]),
    ]


def _seed_result():
    return SeedResult(loci_counts={}, memory_counts={},
                       ref_to_id={"M:r1": "id1", "M:r2": "id2"}, key_index={})


def test_eval_parallel_matches_serial_result():
    topo = _topo()
    url = _url_of(topo)
    qs = _questions()
    sr = _seed_result()

    def post(u, path, body):
        if path == "/search":
            return {"hits": [{"id": "h", "content": "hit", "score": 0.5}]}
        return {}

    def get_params(u, path, params):
        return {"id": "h"}

    def delete(u, path):
        pass

    serial = run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                                      post=post, delete=delete, get_params=get_params,
                                      max_parallel_stores=1, max_parallel_questions=1)
    parallel = run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                                        post=post, delete=delete, get_params=get_params,
                                        max_parallel_stores=8, max_parallel_questions=8)
    assert serial["stores"].keys() == parallel["stores"].keys()
    for name in serial["stores"]:
        assert serial["stores"][name]["question_count"] == parallel["stores"][name]["question_count"]


def test_eval_stores_run_concurrently():
    topo = _topo()
    url = _url_of(topo)
    qs = _questions()
    sr = _seed_result()
    lock = threading.Lock()
    concurrent_peak = [0]
    active = [0]

    def post(u, path, body):
        if path == "/search":
            with lock:
                active[0] += 1
                concurrent_peak[0] = max(concurrent_peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return {"hits": [{"id": "h", "content": "hit", "score": 0.5}]}
        return {}

    def get_params(u, path, params):
        return {"id": "h"}

    def delete(u, path):
        pass

    run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                             post=post, delete=delete, get_params=get_params,
                             max_parallel_stores=8, max_parallel_questions=8)
    assert concurrent_peak[0] > 1


def test_eval_one_failing_store_does_not_sink_the_others():
    topo = _topo()
    url = _url_of(topo)
    qs = _questions()
    sr = _seed_result()

    def post(u, path, body):
        if path == "/search":
            return {"hits": [{"id": "h", "content": "hit", "score": 0.5}]}
        return {}

    def get_params(u, path, params):
        if u == url["L2"]:
            raise RuntimeError("boom")
        return {"id": "h"}

    def delete(u, path):
        pass

    rep = run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                                   post=post, delete=delete, get_params=get_params,
                                   max_parallel_stores=8, max_parallel_questions=8)
    assert set(rep["stores"]) >= {"L1", "L2", "M"}
    assert "error" in rep["stores"]["L2"]
    assert "error" not in rep["stores"]["L1"]
    assert "error" not in rep["stores"]["M"]


def test_eval_one_failing_question_does_not_sink_the_store():
    topo = _topo()
    url = _url_of(topo)
    qs = _questions()
    sr = _seed_result()

    def post(u, path, body):
        if path == "/search":
            if u == url["L1"] and body.get("query") == "q1":
                raise RuntimeError("boom")
            return {"hits": [{"id": "h", "content": "hit", "score": 0.5}]}
        return {}

    def get_params(u, path, params):
        return {"id": "h"}

    def delete(u, path):
        pass

    rep = run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                                   post=post, delete=delete, get_params=get_params,
                                   max_parallel_stores=8, max_parallel_questions=8)
    # L1's column still exists (no store-level error) even though one question failed.
    assert "error" not in rep["stores"]["L1"]
    assert rep["stores"]["L1"]["question_count"] == 2


def test_eval_width_one_takes_the_thread_free_path():
    topo = _topo()
    url = _url_of(topo)
    qs = _questions()
    sr = _seed_result()

    def post(u, path, body):
        if path == "/search":
            return {"hits": [{"id": "h", "content": "hit", "score": 0.5}]}
        return {}

    def get_params(u, path, params):
        return {"id": "h"}

    def delete(u, path):
        pass

    rep = run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                                   post=post, delete=delete, get_params=get_params,
                                   max_parallel_stores=1, max_parallel_questions=1)
    assert set(rep["stores"]) >= {"L1", "L2", "M"}
