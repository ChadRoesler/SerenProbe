"""
Config hierarchy: DefaultConfig + per-node/per-corpus Config + per-store overrides,
resolved through ONE ladder that drives both the boot emit and the live sweep.

The bug this locks down: the emitter used to hardcode `floor: 0.0` on every corpus
store and ignore the Config block entirely, so a baked config was a silent no-op at
boot while the sweep (which /configures live) saw the tuned numbers. Boot and sweep
disagreed and nobody said so. These guard that they now agree.
"""
from __future__ import annotations

from seren_probe.core.topology import compile_topology
from seren_probe.core.topology_emit import emit_compose


def _pc(corpus_default=None, corpus_cfg=None, stores=None, mem_default=None, mem_nodes=None):
    return {"ProbeConfig": {
        "StartingPort": 7620, "DefaultQuestions": "q.yaml",
        "Loci": {"LociCount": 2, "LociConfigs": [{"Name": "A-loci"}, {"Name": "B-loci"}]},
        "Memory": {"MemoryCount": len(mem_nodes or [{"Name": "A-mem"}]),
                   **({"DefaultConfig": mem_default} if mem_default else {}),
                   "MemoryConfigs": mem_nodes or [{"Name": "A-mem"}]},
        "Corpus": {"CorpusCount": 1,
                   **({"DefaultConfig": corpus_default} if corpus_default else {}),
                   "CorpusConfigs": [{"Name": "S",
                       **({"Config": corpus_cfg} if corpus_cfg else {}),
                       "Stores": stores or [{"Store": "A-loci"}, {"Store": "B-loci"},
                                            {"Store": "A-mem"}]}]}}}


# ── the corpus per-store floor/weight ladder ─────────────────────────────────

def test_loci_floor_shorthand_floors_every_loci_store():
    topo = compile_topology(_pc(corpus_default={"loci_floor": 0.5}))
    c = topo.corpus[0]
    floors = {s.name: s.floor for s in c.stores}
    assert floors["A-loci"] == 0.5 and floors["B-loci"] == 0.5   # ALL loci, not just first
    assert floors["A-mem"] is None                               # memory untouched


def test_ladder_precedence_per_store_beats_config_beats_default():
    topo = compile_topology(_pc(
        corpus_default={"loci_floor": 0.5},
        corpus_cfg={"loci_floor": 0.6},
        stores=[{"Store": "A-loci", "Floor": 0.7}, {"Store": "B-loci"}, {"Store": "A-mem"}]))
    floors = {s.name: s.floor for s in topo.corpus[0].stores}
    assert floors["A-loci"] == 0.7      # per-store Floor wins
    assert floors["B-loci"] == 0.6      # per-corpus Config shorthand wins over DefaultConfig
    assert floors["A-mem"] is None      # no mem_floor anywhere -> unspecified


def test_mem_floor_is_independent_of_loci_floor():
    topo = compile_topology(_pc(corpus_default={"loci_floor": 0.5, "mem_floor": 0.1}))
    floors = {s.name: s.floor for s in topo.corpus[0].stores}
    assert floors["A-loci"] == 0.5 and floors["A-mem"] == 0.1


# ── the emit boundary: only-what's-set, no hardcoded 0.0 ─────────────────────

def test_emit_omits_unset_floor_and_carries_federation_knobs():
    topo = compile_topology(_pc(corpus_default={"authority_margin": 0.035, "loci_floor": 0.5}))
    fed = emit_compose(topo).corpus_files["S.corpus.yaml"]["federation"]
    assert fed["authority_margin"] == 0.035           # federation knob emitted
    by = {s["name"]: s for s in fed["stores"]}
    assert by["A-loci"]["floor"] == 0.5
    assert "floor" not in by["A-mem"]                 # unspecified -> OMITTED, not 0.0


def test_emit_no_config_means_no_floor_keys_at_all():
    fed = emit_compose(compile_topology(_pc()))\
        .corpus_files["S.corpus.yaml"]["federation"]
    assert all("floor" not in s for s in fed["stores"])   # nothing hardcoded


# ── memory/loci node config: nested deep-merge + mounted yaml ────────────────

def test_memory_node_config_deep_merges_and_mounts():
    topo = compile_topology(_pc(
        mem_default={"consolidator": {"enabled": False}, "lifetimes": {"short_term_seconds": 100}},
        mem_nodes=[{"Name": "A-mem", "Config": {"lifetimes": {"short_term_seconds": 999}}},
                   {"Name": "B-mem"}]))
    em = emit_compose(topo)
    a = em.corpus_files["A-mem.memory.yaml"]
    assert a["consolidator"]["enabled"] is False      # kept from DefaultConfig
    assert a["lifetimes"]["short_term_seconds"] == 999  # overridden per-node (deep-merge)
    b = em.corpus_files["B-mem.memory.yaml"]
    assert b["lifetimes"]["short_term_seconds"] == 100  # DefaultConfig only
    svc = em.compose["services"]["A-mem"]
    assert svc["environment"]["SEREN_MEMORY_CONFIG"] == "/etc/seren/seren-memory.yaml"
    assert any(v.endswith("/etc/seren/seren-memory.yaml:ro") for v in svc["volumes"])


def test_node_without_config_mounts_nothing():
    """A node with no config gets no yaml, no mount, no config env -> installed defaults."""
    em = emit_compose(compile_topology(_pc()))
    assert "A-loci.loci.yaml" not in em.corpus_files
    svc = em.compose["services"]["A-loci"]
    assert "SEREN_LOCI_CONFIG" not in svc["environment"]


# ── boot == sweep: the live sweep resolves stores the same way ───────────────

def test_sweep_applies_store_knob_to_every_store_of_the_kind():
    from seren_probe.runtime.regrade_live import full_config_body
    store_kinds = {"A-loci": "seren_loci", "B-loci": "seren_loci", "A-mem": "seren_memory"}
    baseline = {"k": 60, "n_results": 30,
                "stores": [{"name": n, "weight": 1.0} for n in store_kinds]}
    body = full_config_body({"loci_floor": 0.7}, store_kinds, baseline)
    floors = {s["name"]: s.get("floor") for s in body["stores"]}
    assert floors["A-loci"] == 0.7 and floors["B-loci"] == 0.7   # every loci store
    assert floors["A-mem"] is None                               # memory untouched
