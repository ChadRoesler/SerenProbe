"""
A paraphrased question is not a dataset defect (punch list probe-lint-paraphrase).

The discriminability check counts the query's words in the seed, so a question
that shares no words with its answer reads as 'names a category' and 'scores
exactly like a dead store'. For a paraphrase that is the point: a vector lane
has to find it. Tagged `paraphrase`, or marked `lexical: false`, the count is
not taken and the lint notes why; untagged, the check stands.
"""
from __future__ import annotations

from seren_probe.core.linters.plan import lint_questions
from seren_probe.core.seed_dataset import load_questions

# Twelve memory entries that all share the query's words, so a lexical count
# calls any one of them ambiguous at k=10.
MEM = [{"tier": "short", "ref": f"evt-{i}", "content": f"the reactor hummed at station {i} on night {i}"}
       for i in range(12)]
LOCI = [{"project": "p", "key": "k", "value": "v", "why": "w"}]


def _q(**extra):
    return {"asks": "memory", "query": "reactor hummed station night", "expect_ref": ["evt-3"], **extra}


def test_an_untagged_question_that_cannot_be_singled_out_is_still_an_error():
    rep = lint_questions([_q()], LOCI, MEM)
    assert not rep.ok and "AMBIGUOUS" in rep.errors[0]


def test_a_paraphrase_tag_skips_the_lexical_count_and_says_so():
    rep = lint_questions([_q(tags=["paraphrase", "detail"])], LOCI, MEM)
    assert rep.ok and not rep.ambiguous, rep.render()
    assert any("paraphrase" in n and "not checked" in n for n in rep.notes)


def test_lexical_false_does_the_same():
    rep = lint_questions([_q(lexical=False)], LOCI, MEM)
    assert rep.ok and not rep.ambiguous


def test_lexical_is_a_known_question_key_and_rides_on_the_question():
    qs = load_questions([_q(lexical=False), _q(tags=["paraphrase"])])
    assert [q.lexical for q in qs] == [False, True]
    assert qs[1].tags == ["paraphrase"]


def test_the_plan_path_keeps_the_tag_and_the_flag_on_the_way_to_the_check():
    """The adapter that turns a Question into the dict the checks read once
    dropped `hops`, then `quiet_in`; not `tags` and `lexical` too."""
    from seren_probe.core.linters.adapters import _question_to_dict
    qs = load_questions([_q(tags=["paraphrase"]), _q(lexical=False)])
    rep = lint_questions([_question_to_dict(q) for q in qs], LOCI, MEM)
    assert rep.ok and not rep.ambiguous and len(rep.notes) == 2, rep.render()
