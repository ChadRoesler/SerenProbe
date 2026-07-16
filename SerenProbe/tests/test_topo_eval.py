"""Tests for topology-driven evaluation (seren_probe.live_eval.run_topology_evaluation).

Transport is injected (post/delete/get_params) so grading is proven without a
live store stack - same discipline as the seed_stores tests.
"""
from seren_probe.core.topology import compile_topology
from seren_probe.core.seed_dataset import Question, SeedResult
from seren_probe.runtime.live_eval import run_topology_evaluation


def _topo():
    return compile_topology({"ProbeConfig": {
        "StartingPort": 7420,
        "Loci":   {"LociCount": 2, "LociConfigs": [
            {"Name": "L1", "Port": 7421, "Flags": ["vector"]}, {"Name": "L2", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "M", "Port": 7425}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "C", "Port": 7427, "Stores": [{"Store": "L1"}, {"Store": "M"}]}]},
    }})  # loci L1,L2 ; memory M ; corpus C + one catch-all


def _url_of(topo):
    m = {n.name: f"http://127.0.0.1:{n.port}" for n in topo.loci + topo.memory}
    m.update({c.name: f"http://127.0.0.1:{c.port}" for c in topo.corpus})
    return m


def _fakes(topo, corpus_hits=None):
    url = _url_of(topo)
    search = {
        url["L1"]: [{"id": "L1hit", "project": "seren-loci", "key": "supersede_rule",
                     "value": "strict", "why": "one live value", "score": 0.9}],
        url["L2"]: [],
        url["M"]:  [{"id": "Mhit", "content": "fusion is rank-only and embedder-agnostic", "score": 0.8}],
    }
    cport = [c.port for c in topo.corpus if c.name == "C"][0]
    if corpus_hits is not None:
        search[f"http://127.0.0.1:{cport}"] = corpus_hits

    def post(u, path, body):
        if path == "/search":
            return {"hits": search.get(u, [])}
        if path in ("/short", "/near"):
            return {"id": "mint"}
        return {}

    def get_params(u, path, params):
        if u == url["L1"] and params.get("key") == "supersede_rule":
            return {"id": "L1hit"}
        return {}

    def delete(u, path):
        pass

    return url, post, get_params, delete


def test_dynamic_columns_one_per_store():
    topo = _topo()
    url, post, get_params, delete = _fakes(
        topo, corpus_hits=[{"id": "Chit", "content": "RRF rank-only fusion across stores", "score": 0.7}])
    qs = [
        Question(asks="loci",   query="supersede", expect_key=["seren-loci/supersede_rule"]),
        Question(asks="memory", query="fusion",    expect_ref=["ep1"]),
        Question(asks="corpus", query="merge",     expect_content=["RRF", "rank-only"]),
    ]
    sr = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={"M:ep1": "Mhit"}, key_index={})
    rep = run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                                  post=post, delete=delete, get_params=get_params, k=10)
    catch = {c.name for c in topo.corpus if c.is_catchall}
    assert set(rep["stores"]) == {"L1", "L2", "M", "C"} | catch
    assert rep["question_count"] == 3
    assert rep["stores"]["L1"]["question_count"] == 1
    assert rep["stores"]["M"]["question_count"] == 1
    assert rep["stores"]["C"]["question_count"] == 1


def test_expect_key_honest_hit_and_miss():
    topo = _topo()
    url, post, get_params, delete = _fakes(topo)
    qs = [Question(asks="loci", query="supersede", expect_key=["seren-loci/supersede_rule"])]
    sr = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={}, key_index={})
    rep = run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                                  post=post, delete=delete, get_params=get_params, k=10)
    assert rep["stores"]["L1"]["aggregate"]["hit_rate"] == 1.0
    assert rep["stores"]["L1"]["aggregate"]["recall"] == 1.0
    assert rep["stores"]["L2"]["aggregate"]["hit_rate"] == 0.0


def test_expect_ref_resolves_via_seed_result():
    topo = _topo()
    url, post, get_params, delete = _fakes(topo)
    qs = [Question(asks="memory", query="fusion", expect_ref=["ep1"])]
    sr = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={"M:ep1": "Mhit"}, key_index={})
    rep = run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                                  post=post, delete=delete, get_params=get_params, k=10)
    assert rep["stores"]["M"]["aggregate"]["hit_rate"] == 1.0


def test_corpus_gets_docket_metrics_loci_does_not():
    topo = _topo()
    url, post, get_params, delete = _fakes(
        topo, corpus_hits=[{"id": "Chit", "content": "RRF rank-only fusion", "score": 0.7}])
    qs = [
        Question(asks="corpus", query="merge", expect_content=["RRF", "rank-only"]),
        Question(asks="loci",   query="supersede", expect_key=["seren-loci/supersede_rule"]),
    ]
    sr = SeedResult(loci_counts={}, memory_counts={}, ref_to_id={}, key_index={})
    rep = run_topology_evaluation(topo, url, qs, seed_result=sr, seed=False,
                                  post=post, delete=delete, get_params=get_params, k=10)
    agg_c = rep["stores"]["C"]["aggregate"]
    assert "docket_coverage" in agg_c and agg_c["docket_coverage"] > 0.0
    assert "docket_coverage" not in rep["stores"]["L1"]["aggregate"]
