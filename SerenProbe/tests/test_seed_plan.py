"""Tests for the resolved-plan seeder (seed_from_plan) + resolve->seed wiring."""
from seren_probe.core.topology import compile_topology
from seren_probe.core.seed_dataset import seed_from_plan, LociItem, MemoryItem
from seren_probe.core.resolve import resolve_plan


def _topo():
    return compile_topology({"ProbeConfig": {"StartingPort": 7420,
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "la", "Port": 7421}, {"Name": "lb", "Port": 7422}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "la"}, {"Store": "m"}]}]}}})


def _url_of(t):
    return {n.name: f"http://127.0.0.1:{n.port}" for n in t.loci + t.memory}


def _recorder():
    calls = []
    mint = {"n": 0}
    def post(url, path, body):
        calls.append(("POST", url, path, body))
        if path in ("/short", "/near"):
            mint["n"] += 1
            return {"id": f"mint-{mint['n']}"}
        return {}
    def delete(url, path):
        calls.append(("DELETE", url, path, None))
    return calls, post, delete


def test_seed_from_plan_writes_loci_and_memory_with_tiers():
    t = _topo()
    plan = {
        "la": [LociItem("p", "k1", "v1", "w"), LociItem("*", "k2", "v2")],
        "lb": [],
        "m": [MemoryItem("short", "s1", topic="t", ref="r1"),
              MemoryItem("near", "do later", ref="r2"),
              MemoryItem("long", "durable fact", ref="r3")],
    }
    calls, post, delete = _recorder()
    res = seed_from_plan(t, plan, _url_of(t), post=post, delete=delete)
    assert res.loci_counts == {"la": 2, "lb": 0}
    assert res.memory_counts == {"m": 3}
    facts = [c for c in calls if c[2] == "/fact"]
    assert len(facts) == 2 and facts[0][3] == {"project": "p", "key": "k1", "value": "v1", "why": "w"}
    assert facts[1][3]["project"] == "*"
    assert res.key_index["p/k1"] == ["la"]
    # memory: one /near, two /short, one promote, one delete (long cleanup)
    assert any(c[2] == "/near" and c[3]["intent"] == "do later" for c in calls)
    assert sum(1 for c in calls if c[2] == "/short") == 2
    assert any(c[2].endswith("/promote") for c in calls)
    assert any(c[0] == "DELETE" and c[2].startswith("/short/") for c in calls)
    # ref capture: namespaced + bare
    assert res.ref_to_id["m:r1"].startswith("mint-") and "r1" in res.ref_to_id


def test_seed_from_plan_long_without_delete_still_promotes():
    t = _topo()
    plan = {"la": [], "lb": [], "m": [MemoryItem("long", "x", ref="r")]}
    calls, post, _ = _recorder()
    res = seed_from_plan(t, plan, _url_of(t), post=post, delete=None)
    assert res.memory_counts["m"] == 1
    assert any(c[2].endswith("/promote") for c in calls)
    assert not any(c[0] == "DELETE" for c in calls)


def test_resolve_then_seed_from_plan_end_to_end():
    """Resolver output feeds straight into the seeder - the config-driven path."""
    t = compile_topology({"ProbeConfig": {"StartingPort": 7420,
        "DefaultLociSeed": "L.yaml", "DefaultMemorySeed": "M.yaml", "DefaultQuestions": "Q.yaml",
        "Loci": {"LociCount": 2, "LociConfigs": [
            {"Name": "real", "Port": 7421},
            {"Name": "decoy", "Port": 7422, "NegativeTest": True, "Seed": "decoy.yaml"}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "real"}, {"Store": "m"}]}]}}})

    def li(ref, kind, warnings=None):
        return {
            "L.yaml": [LociItem("*", "real1", "v"), LociItem("*", "real2", "v")],
            "M.yaml": [MemoryItem("short", "mem1", ref="e1")],
            "decoy.yaml": [LociItem("*", "decoy1", "v")],
        }.get(ref, [])

    plan = resolve_plan(t, load_items=li, load_qs=lambda r, w=None: [])
    calls, post, delete = _recorder()
    res = seed_from_plan(t, plan.seed_by_store, _url_of(t), post=post, delete=delete)
    assert res.loci_counts == {"real": 2, "decoy": 1}    # decoy got ONLY its 1 decoy fact
    assert res.memory_counts == {"m": 1}
    # decoy fact came from decoy.yaml, not L.yaml
    decoy_facts = [c[3]["key"] for c in calls if c[2] == "/fact" and "7422" in c[1]]
    assert decoy_facts == ["decoy1"]
