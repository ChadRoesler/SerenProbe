"""Known-answer tests for seren_probe.metrics - the honest-scoring core.

These pin the retrieval + docket math to hand-computed values so a drift in any
metric fails loudly instead of silently lying in every eval. Pure functions, no
stack, no transport.

A couple of tests are CHARACTERIZATION tests: they pin behavior that's arguably
surprising (mrr scans the full list, not top-k; precision divides by k even for
short result lists) so a future change to those has to be deliberate. Both are
flagged inline.
"""
import math
import pytest

from seren_probe.core.metrics import (
    compute_metrics, compute_metrics_batch, _ndcg,
    normalize_text, grade_against_content, EvalMetrics,
)


# ── hit_rate ────────────────────────────────────────────────────────────
def test_hit_rate_present_and_absent():
    assert compute_metrics([("a", 1.0)], {"a"}, k=10)["hit_rate"] == 1.0
    assert compute_metrics([("a", 1.0)], {"z"}, k=10)["hit_rate"] == 0.0


def test_hit_rate_only_counts_top_k():
    # relevant sits at rank 12; with k=10 it's outside the window -> miss
    retrieved = [(f"d{i}", 1.0) for i in range(11)] + [("R", 1.0)]
    assert compute_metrics(retrieved, {"R"}, k=10)["hit_rate"] == 0.0


# ── mrr ─────────────────────────────────────────────────────────────────
def test_mrr_first_relevant_rank_three():
    retrieved = [("a", 0.9), ("b", 0.8), ("R", 0.7)]
    assert compute_metrics(retrieved, {"R"}, k=10)["mrr"] == pytest.approx(1 / 3)


def test_mrr_rank_one_is_one():
    assert compute_metrics([("R", 1.0), ("b", 0.5)], {"R"}, k=10)["mrr"] == 1.0


def test_mrr_is_capped_at_k():
    # mrr@k: a relevant doc OUTSIDE the top-k window is a miss. Rank 12 at k=10
    # -> 0.0 (was 1/12 before the @k fix that aligned mrr with the other metrics).
    retrieved = [(f"d{i}", 1.0) for i in range(11)] + [("R", 1.0)]
    assert compute_metrics(retrieved, {"R"}, k=10)["mrr"] == 0.0


def test_mrr_at_k_boundary_still_counts():
    # a relevant doc AT rank k (the last in-window slot) still counts: 1/10.
    retrieved = [(f"d{i}", 1.0) for i in range(9)] + [("R", 1.0)]
    assert compute_metrics(retrieved, {"R"}, k=10)["mrr"] == pytest.approx(1 / 10)


# ── precision / recall ──────────────────────────────────────────────────
def test_precision_divides_by_k_characterization():
    # CHARACTERIZATION: precision@k uses k as the denominator, so a single
    # all-relevant hit at k=10 is 0.1, not 1.0. Standard for precision@k, but
    # worth pinning so nobody "fixes" it to len(retrieved) by accident.
    res = compute_metrics([("a", 1.0)], {"a"}, k=10)
    assert res["precision"] == pytest.approx(0.1)
    assert res["recall"] == 1.0


def test_precision_and_recall_partial():
    retrieved = [("a", 1.0), ("b", 0.9), ("c", 0.8), ("d", 0.7)]
    res = compute_metrics(retrieved, {"a", "b", "z"}, k=4)
    assert res["precision"] == pytest.approx(2 / 4)   # 2 of top-4 relevant
    assert res["recall"] == pytest.approx(2 / 3)      # 2 of 3 relevant found


def test_recall_empty_relevant_is_zero():
    assert compute_metrics([("a", 1.0)], set(), k=10)["recall"] == 0.0


# ── ndcg ────────────────────────────────────────────────────────────────
def test_ndcg_perfect_when_relevant_on_top():
    top = ["a", "b"]
    assert _ndcg(top, {"a", "b"}, 10) == pytest.approx(1.0)


def test_ndcg_single_relevant_at_rank_two():
    # dcg = 1/log2(3); ideal (1 relevant on top) = 1/log2(2) = 1.0
    val = _ndcg(["x", "R"], {"R"}, 10)
    assert val == pytest.approx(1.0 / math.log2(3))


def test_ndcg_empty_relevant_is_zero():
    # was 1.0 ("no relevant docs -> any ranking is ideal"), which is defensible in
    # isolation and a lie in an eval harness: empty `relevant` means ground truth
    # failed to resolve, and that scored PERFECT while hit_rate scored 0.
    assert _ndcg(["a", "b"], set(), 10) == 0.0


# ── iou ─────────────────────────────────────────────────────────────────
def test_iou_jaccard():
    # top {a,b,c} vs relevant {a}: 1 / 3
    assert compute_metrics([("a", 1), ("b", 1), ("c", 1)], {"a"}, k=10)["iou"] == pytest.approx(1 / 3)


# ── prec_omega ──────────────────────────────────────────────────────────
def test_prec_omega_rank_one_k_three_is_half():
    # weights log2(2)+log2(1.5)+log2(4/3) = 1 + 0.585 + 0.415 = 2.0 exactly;
    # a single rank-1 hit contributes weight 1.0 -> 1.0 / 2.0 = 0.5
    res = compute_metrics([("R", 1), ("b", 1), ("c", 1)], {"R"}, k=3)
    assert res["prec_omega"] == pytest.approx(0.5)


def test_prec_omega_rewards_higher_ranks():
    top_rank1 = compute_metrics([("R", 1), ("b", 1), ("c", 1)], {"R"}, k=3)["prec_omega"]
    top_rank3 = compute_metrics([("b", 1), ("c", 1), ("R", 1)], {"R"}, k=3)["prec_omega"]
    assert top_rank1 > top_rank3


# ── batch aggregate ─────────────────────────────────────────────────────
def test_batch_aggregate_means():
    batch = [([("a", 1)], {"a"}), ([("z", 1)], {"a"})]   # one hit, one miss
    agg = compute_metrics_batch(batch, k=10).aggregate()
    assert agg["hit_rate"] == pytest.approx(0.5)
    assert agg["count"] == 2
    # no docket values were set -> docket keys stay absent
    assert "docket_coverage" not in agg


def test_empty_aggregate_is_empty_dict():
    assert EvalMetrics().aggregate() == {}


# ── normalize_text ──────────────────────────────────────────────────────
def test_normalize_unifies_separators():
    assert normalize_text("rate_limit") == normalize_text("rate-limit") == normalize_text("rate limit") == "rate limit"


def test_normalize_none_and_punctuation():
    assert normalize_text(None) == ""
    assert normalize_text("Rate-Limit!!") == "rate limit"


# ── grade_against_content ───────────────────────────────────────────────
def test_grade_content_coverage_and_density():
    hits = [{"id": "a", "content": "the rate limit is 5", "score": 0.9},
            {"id": "b", "content": "unrelated text", "score": 0.5}]
    retrieved, relevant, cov, den = grade_against_content(hits, ["rate_limit"], k=10)
    assert relevant == {"a"}
    assert cov == pytest.approx(1.0)          # 1 of 1 expected item found
    assert den == pytest.approx(0.5)          # 1 of 2 hits carries it


def test_grade_counts_each_expected_once():
    # both hits contain 'foo'; coverage counts the expected item once (break),
    # so only the FIRST matching hit lands in relevant.
    hits = [{"id": "a", "content": "foo bar"}, {"id": "b", "content": "foo baz"}]
    _, relevant, cov, den = grade_against_content(hits, ["foo"], k=10)
    assert relevant == {"a"}
    assert cov == pytest.approx(1.0)
    assert den == pytest.approx(1.0)          # density still counts BOTH hits


def test_grade_falls_back_to_expected_ids():
    hits = [{"id": "x", "content": "nothing matches here", "score": 0.5}]
    _, relevant, cov, den = grade_against_content(hits, ["absent phrase"], expected_ids=["x"], k=10)
    assert relevant == {"x"}
    assert cov == pytest.approx(1.0)
    assert den == pytest.approx(1.0)


def test_grade_empty_expected_is_zero_coverage():
    _, _, cov, _ = grade_against_content([{"id": "a", "content": "x"}], [], k=10)
    assert cov == 0.0
