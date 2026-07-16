"""Tests for the eval-question parser (seren_probe.seed_dataset.load_questions).

(Pools seeding was removed - seeding is config-driven; flat loading is covered by
test_seed_flat, the seeder by test_seed_plan.)
"""
import pytest
from seren_probe.core.seed_dataset import load_questions, SeedError


def test_good_questions():
    qs = load_questions({"questions": [
        {"asks": "loci",   "query": "supersede", "expect_key": ["seren-loci/supersede_rule"]},
        {"asks": "memory", "query": "fusion",    "expect_ref": ["ep1"]},
        {"asks": "corpus", "query": "merge",     "expect_content": ["RRF", "rank-only"]},
    ]})
    assert len(qs) == 3 and qs[0].expect_key == ["seren-loci/supersede_rule"]


def test_bare_list_tolerated():
    qs = load_questions([{"asks": "loci", "query": "q", "expect_key": ["p/k"]}])
    assert len(qs) == 1


def test_question_needs_some_expectation():
    with pytest.raises(SeedError) as ei:
        load_questions([{"asks": "loci", "query": "q"}])
    assert any("at least one of expect_key" in e for e in ei.value.errors)


def test_question_bad_asks_and_missing_query():
    with pytest.raises(SeedError) as ei:
        load_questions([{"asks": "bogus", "expect_content": ["x"]}])
    msg = "\n".join(ei.value.errors)
    assert "'asks' must be one of" in msg and "needs a 'query'" in msg


def test_loci_expect_ref_warns_but_kept():
    # a loci question with expect_ref is unusual (warned) but not fatal
    qs = load_questions([{"asks": "loci", "query": "q", "expect_ref": ["r"]}])
    assert len(qs) == 1 and qs[0].expect_ref == ["r"]


def test_expect_empty_question_valid():
    qs = load_questions([{"asks": "loci", "query": "what does vaporize() return?", "expect_empty": True}])
    assert len(qs) == 1 and qs[0].expect_empty is True
    assert qs[0].expect_key == [] and qs[0].expect_content == []


def test_expect_empty_with_expectations_raises():
    with pytest.raises(SeedError) as ei:
        load_questions([{"asks": "loci", "query": "q", "expect_empty": True, "expect_key": ["p/k"]}])
    assert any("don't" in e and "combine it" in e for e in ei.value.errors)


def test_no_expectation_message_mentions_expect_empty():
    with pytest.raises(SeedError) as ei:
        load_questions([{"asks": "loci", "query": "q"}])
    assert any("expect_empty: true" in e for e in ei.value.errors)
