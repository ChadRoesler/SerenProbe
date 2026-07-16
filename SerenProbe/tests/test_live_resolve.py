"""Resolve-side behavior for LiveStoreUrl nodes: they skip the synthetic seed,
don't warn 'empty', and still count as seed-intent so seeding (+ live import) runs."""
from seren_probe.core.topology import compile_topology
from seren_probe.core.resolve import resolve_plan, resolve_eval_inputs


def _cfg(loci_extra=None, defaults=None):
    l = {"Name": "l", "Port": 7421}
    l.update(loci_extra or {})
    pc = {"ProbeConfig": {"StartingPort": 7420, "DefaultQuestions": "datasets/q.yaml",
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7427, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        "Loci": {"LociCount": 1, "LociConfigs": [l]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7425}]}}}
    if defaults:
        pc["ProbeConfig"].update(defaults)
    return pc


def _plan(t):
    # The loaders take a warnings out-param now -- fakes mirror the real signature.
    return resolve_plan(t,
                        load_items=lambda ref, kind, w=None: [f"ITEM:{ref}:{kind}"],
                        load_qs=lambda ref, w=None: [])


def test_live_node_skips_synthetic_default():
    t = compile_topology(_cfg(loci_extra={"LiveStoreUrl": "http://h:7422"},
                              defaults={"DefaultLociSeed": "d/loci.yaml",
                                        "DefaultMemorySeed": "d/mem.yaml"}))
    plan = _plan(t)
    assert plan.seed_by_store["l"] == []                                   # live node: no synthetic seed
    assert plan.seed_by_store["m"] == ["ITEM:d/mem.yaml:memory"]           # non-live node still seeded
    assert not any("store 'l'" in w for w in plan.warnings)                # no false empty warning


def test_live_only_topology_is_seed_intent():
    t = compile_topology(_cfg(loci_extra={"LiveStoreUrl": "http://h:7422"}))
    ei = resolve_eval_inputs(
        t, {}, resolve=lambda topo: resolve_plan(topo, load_items=lambda r, k, w=None: [],
                                                 load_qs=lambda r, w=None: []),
        load_qs=lambda r, w=None: [])
    assert ei.seed is True and ei.seed_by_store is not None


def test_no_seeds_no_live_is_not_seed_intent():
    t = compile_topology(_cfg())
    ei = resolve_eval_inputs(
        t, {}, resolve=lambda topo: resolve_plan(topo, load_items=lambda r, k, w=None: [],
                                                 load_qs=lambda r, w=None: []),
        load_qs=lambda r, w=None: [])
    assert ei.seed is False
