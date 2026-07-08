"""Tests for the seed-dataset spine (seren_probe.seed_dataset)."""
import pytest

from seren_probe.topology import compile_topology
from seren_probe.seed_dataset import (
    load_seed_dataset, load_questions, seed_stores, SeedError,
)


def _topo():
    return compile_topology({"ProbeConfig": {
        "StartingPort": 7420,
        "Loci":   {"LociCount": 2, "LociConfigs": [
            {"Name": "loci-a", "Port": 7421, "Flags": ["vector"]}, {"Name": "loci-b", "Port": 7422}]},
        "Memory": {"MemoryCount": 2, "MemoryConfigs": [
            {"Name": "mem-a", "Port": 7425}, {"Name": "mem-b", "Port": 7426}]},
        "Corpus": {"CorpusCount": 2, "CorpusConfigs": [
            {"Name": "corp", "Port": 7427, "Stores": [{"Store": "loci-a"}, {"Store": "mem-a"}]}]},
    }})


def _good_ds():
    return {
        "pools": {
            "facts": [
                {"project": "p", "key": "k1", "value": "v1", "why": "because"},
                {"key": "k2", "value": "v2"},
            ],
            "episodes": [
                {"ref": "ep1", "content": "short episode", "topic": "t"},
                {"ref": "nt1", "intent": "do a thing", "topic": "t", "tier": "near"},
                {"ref": "lt1", "content": "long fact", "topic": "arch", "tier": "long"},
            ],
        },
        "default": {"loci": "facts", "memory": "episodes"},
        "overrides": {},
    }


# ── GOOD ────────────────────────────────────────────────────────────────
def test_good_dataset_compiles_clean():
    ds = load_seed_dataset(_good_ds(), _topo())
    assert ds.warnings == []
    assert ds.pool_for("loci-a", "seren_loci") == "facts"
    assert ds.pool_for("mem-b", "seren_memory") == "episodes"


def test_override_to_real_store():
    d = _good_ds()
    d["pools"]["special"] = [{"key": "sk", "value": "sv"}]
    d["overrides"] = {"loci-b": "special"}
    ds = load_seed_dataset(d, _topo())
    assert ds.pool_for("loci-b", "seren_loci") == "special"
    assert ds.pool_for("loci-a", "seren_loci") == "facts"


def test_warns_unused_pool_and_misplaced_tier():
    d = _good_ds()
    d["pools"]["orphan"] = [{"key": "x", "value": "y"}]          # nobody draws it
    d["pools"]["facts"].append({"key": "k3", "value": "v3", "tier": "short"})  # tier on a loci item
    ds = load_seed_dataset(d, _topo())
    assert any("orphan" in w and "no store draws" in w for w in ds.warnings)
    assert any("meaningless for a loci item" in w for w in ds.warnings)


def test_warns_content_on_near_item():
    d = _good_ds()
    d["pools"]["episodes"].append({"ref": "nt2", "content": "should be intent", "tier": "near"})
    ds = load_seed_dataset(d, _topo())
    assert any("should use 'intent'" in w for w in ds.warnings)


# ── BAD ─────────────────────────────────────────────────────────────────
def test_missing_default_loci_raises():
    d = _good_ds(); d["default"].pop("loci")
    with pytest.raises(SeedError) as ei:
        load_seed_dataset(d, _topo())
    assert any("default.loci" in e for e in ei.value.errors)


def test_default_references_unknown_pool():
    d = _good_ds(); d["default"]["memory"] = "nope"
    with pytest.raises(SeedError) as ei:
        load_seed_dataset(d, _topo())
    assert any("isn't defined in 'pools'" in e for e in ei.value.errors)


def test_override_unknown_store():
    d = _good_ds(); d["overrides"] = {"ghost-store": "facts"}
    with pytest.raises(SeedError) as ei:
        load_seed_dataset(d, _topo())
    assert any("isn't a store in this topology" in e for e in ei.value.errors)


def test_loci_item_missing_key_value():
    d = _good_ds(); d["pools"]["facts"].append({"value": "no key here"})
    with pytest.raises(SeedError) as ei:
        load_seed_dataset(d, _topo())
    assert any("needs 'key' and 'value'" in e for e in ei.value.errors)


def test_memory_bad_tier():
    d = _good_ds(); d["pools"]["episodes"].append({"content": "x", "tier": "eternal"})
    with pytest.raises(SeedError) as ei:
        load_seed_dataset(d, _topo())
    assert any("tier 'eternal' invalid" in e for e in ei.value.errors)


def test_near_missing_intent():
    d = _good_ds(); d["pools"]["episodes"].append({"ref": "bad", "tier": "near"})
    with pytest.raises(SeedError) as ei:
        load_seed_dataset(d, _topo())
    assert any("needs 'intent'" in e for e in ei.value.errors)


# ── questions ───────────────────────────────────────────────────────────
def test_good_questions():
    qs = load_questions({"questions": [
        {"asks": "loci",   "query": "supersede", "expect_key": ["seren-loci/supersede_rule"]},
        {"asks": "memory", "query": "fusion",    "expect_ref": ["ep1"]},
        {"asks": "corpus", "query": "merge",     "expect_content": ["RRF", "rank-only"]},
    ]})
    assert len(qs) == 3 and qs[0].expect_key == ["seren-loci/supersede_rule"]


def test_question_needs_some_expectation():
    with pytest.raises(SeedError) as ei:
        load_questions([{"asks": "loci", "query": "q"}])
    assert any("at least one of expect_key" in e for e in ei.value.errors)


def test_question_bad_asks_and_missing_query():
    with pytest.raises(SeedError) as ei:
        load_questions([{"asks": "bogus", "expect_content": ["x"]}])
    msg = "\n".join(ei.value.errors)
    assert "'asks' must be one of" in msg and "needs a 'query'" in msg


# ── the seeder: proven with injected post/delete (no live stack) ─────────
def test_seed_stores_maps_pools_and_captures_refs():
    topo = _topo()
    ds = load_seed_dataset(_good_ds(), topo)
    url_of = {n.name: f"http://127.0.0.1:{n.port}" for n in topo.loci + topo.memory}

    calls = []
    _mint = {"n": 0}
    def fake_post(url, path, body):
        calls.append(("POST", url, path, body))
        if path in ("/short", "/near"):
            _mint["n"] += 1
            return {"id": f"mint-{_mint['n']}"}
        return {}
    def fake_delete(url, path):
        calls.append(("DELETE", url, path, None))

    res = seed_stores(topo, ds, url_of, post=fake_post, delete=fake_delete)

    # loci: both loci stores got 2 /fact posts from the facts pool
    assert res.loci_counts == {"loci-a": 2, "loci-b": 2}
    fact_posts = [c for c in calls if c[2] == "/fact"]
    assert len(fact_posts) == 4
    assert fact_posts[0][3] == {"project": "p", "key": "k1", "value": "v1", "why": "because"}
    assert fact_posts[1][3]["project"] == "*"   # default project

    # key_index resolves expect_key -> which stores hold it
    assert set(res.key_index["p/k1"]) == {"loci-a", "loci-b"}
    assert set(res.key_index["*/k2"]) == {"loci-a", "loci-b"}

    # memory: each store did short(ep1) + near(nt1) + long(lt1 => short+promote+DELETE)
    assert res.memory_counts == {"mem-a": 3, "mem-b": 3}
    assert any(c[2] == "/near" and c[3]["intent"] == "do a thing" for c in calls)
    assert any(c[0] == "DELETE" and c[2].startswith("/short/") for c in calls)
    assert any(c[2].endswith("/promote") for c in calls)

    # ref -> minted id captured (store-scoped and convenience keys)
    assert "mem-a:ep1" in res.ref_to_id and res.ref_to_id["mem-a:ep1"].startswith("mint-")
    assert "mem-b:lt1" in res.ref_to_id
    assert "ep1" in res.ref_to_id   # convenience bare key


def test_seed_stores_long_tier_without_delete_still_seeds():
    topo = _topo()
    ds = load_seed_dataset(_good_ds(), topo)
    url_of = {n.name: f"http://127.0.0.1:{n.port}" for n in topo.loci + topo.memory}
    calls = []
    def fake_post(url, path, body):
        calls.append((url, path)); return {"id": "x"} if path in ("/short", "/near") else {}
    # delete=None -> long-tier promotes but skips the short-copy cleanup (disclosed degradation)
    res = seed_stores(topo, ds, url_of, post=fake_post, delete=None)
    assert res.memory_counts == {"mem-a": 3, "mem-b": 3}
    assert any(p.endswith("/promote") for _, p in calls)
