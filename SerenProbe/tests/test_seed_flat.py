"""Tests for the FLAT seed loader (seren_probe.seed_dataset.load_seed_items)."""
from pathlib import Path
import pytest
from seren_probe.core.seed_dataset import load_seed_items, LociItem, MemoryItem, SeedError

EXAMPLES = Path(__file__).parent / "fixtures"


def test_flat_loci_list():
    items = load_seed_items([{"project": "p", "key": "k", "value": "v", "why": "w"},
                             {"key": "k2", "value": "v2"}], "loci")
    assert len(items) == 2 and all(isinstance(i, LociItem) for i in items)
    assert items[0].project == "p" and items[1].project == "*"   # default project


def test_flat_memory_list_all_tiers():
    items = load_seed_items([
        {"ref": "a", "content": "short one"},
        {"ref": "b", "intent": "do later", "tier": "near"},
        {"ref": "c", "content": "durable", "tier": "long"},
    ], "memory")
    assert [i.tier for i in items] == ["short", "near", "long"]
    assert all(isinstance(i, MemoryItem) for i in items)


def test_flat_items_wrapper_tolerated():
    items = load_seed_items({"items": [{"key": "k", "value": "v"}]}, "loci")
    assert len(items) == 1


def test_flat_kind_named_key_tolerated():
    items = load_seed_items({"memory": [{"content": "x"}]}, "memory")
    assert len(items) == 1 and items[0].tier == "short"


def test_flat_bad_kind_raises():
    with pytest.raises(SeedError) as ei:
        load_seed_items([], "corpus")
    assert any("must be 'loci' or 'memory'" in e for e in ei.value.errors)


def test_flat_not_a_list_raises():
    with pytest.raises(SeedError) as ei:
        load_seed_items({"nope": 1}, "loci")
    assert any("must be a list" in e for e in ei.value.errors)


def test_flat_loci_missing_key_value_raises():
    with pytest.raises(SeedError) as ei:
        load_seed_items([{"value": "no key"}], "loci")
    assert any("needs 'key' and 'value'" in e for e in ei.value.errors)
    assert any("loci seed[0]" in e for e in ei.value.errors)   # clean flat-file label


def test_flat_memory_bad_tier_raises():
    with pytest.raises(SeedError) as ei:
        load_seed_items([{"content": "x", "tier": "eternal"}], "memory")
    assert any("tier 'eternal' invalid" in e for e in ei.value.errors)


def test_flat_near_content_warns_not_raises():
    # a warning isn't fatal; near-with-content still loads (content treated as intent)
    items = load_seed_items([{"content": "should be intent", "tier": "near"}], "memory")
    assert len(items) == 1 and items[0].text == "should be intent"


def test_flat_near_missing_intent_raises():
    with pytest.raises(SeedError) as ei:
        load_seed_items([{"ref": "x", "tier": "near"}], "memory")
    assert any("needs 'intent'" in e for e in ei.value.errors)


def test_flat_tier_on_loci_item_warns_not_fatal():
    # a 'tier' on a loci item is meaningless -> warned, but the item still loads
    items = load_seed_items([{"key": "k", "value": "v", "tier": "short"}], "loci")
    assert len(items) == 1 and items[0].key == "k"


# ── validate the shipped flat example files ──────────────────────────────
def test_example_meridian_loci_valid():
    items = load_seed_items(str(EXAMPLES / "meridian.loci.yaml"), "loci")
    assert len(items) == 15 and all(i.project == "meridian" for i in items)


def test_example_meridian_memory_valid():
    items = load_seed_items(str(EXAMPLES / "meridian.memory.yaml"), "memory")
    assert len(items) == 9
    tiers = {i.tier for i in items}
    assert tiers == {"short", "near", "long"}
    assert sum(1 for i in items if i.tier == "near") == 2
