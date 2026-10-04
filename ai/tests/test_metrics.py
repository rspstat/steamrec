import numpy as np
import pytest

from evaluation.metrics import (
    coverage,
    hit_rate_at_k,
    intra_list_diversity,
    jaccard,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def test_metrics_known_values():
    rec, rel = [1, 2, 3, 4], {2, 4}
    assert precision_at_k(rec, rel, 2) == 0.5
    assert recall_at_k(rec, rel, 4) == 1.0
    assert ndcg_at_k([2, 4, 1, 3], rel, 4) == 1.0  # 완벽한 순서


def test_ndcg_penalizes_late_hits():
    rel = {2, 4}
    # 정답이 2위, 4위에 있을 때: DCG = 1/log2(3) + 1/log2(5), 이상적 DCG = 1 + 1/log2(3)
    expected = (1 / np.log2(3) + 1 / np.log2(5)) / (1 + 1 / np.log2(3))
    assert ndcg_at_k([1, 2, 3, 4], rel, 4) == pytest.approx(expected)
    assert ndcg_at_k([1, 2, 3, 4], rel, 4) < ndcg_at_k([2, 4, 1, 3], rel, 4)


def test_ndcg_ideal_is_capped_by_k():
    # 정답이 5개여도 k=2면 이상적 DCG는 상위 2자리까지만 → 상위 2개가 모두 정답이면 1.0
    assert ndcg_at_k([1, 2, 9, 9], {1, 2, 3, 4, 5}, 2) == 1.0


def test_hit_rate():
    assert hit_rate_at_k([1, 2, 3], {3}, 3) == 1.0
    assert hit_rate_at_k([1, 2, 3], {3}, 2) == 0.0


def test_recall_empty_relevant_is_zero():
    assert recall_at_k([1, 2], set(), 2) == 0.0


def test_empty_relevant_gives_zero_everywhere():
    assert precision_at_k([1, 2], set(), 2) == 0.0
    assert ndcg_at_k([1, 2], set(), 2) == 0.0
    assert hit_rate_at_k([1, 2], set(), 2) == 0.0


def test_k_larger_than_list_counts_missing_slots_as_misses():
    # 추천이 2개뿐인데 k=10: 분모는 k 그대로 → 2개 다 맞혀도 precision은 0.2
    assert precision_at_k([1, 2], {1, 2}, 10) == pytest.approx(0.2)
    assert recall_at_k([1, 2], {1, 2}, 10) == 1.0
    assert ndcg_at_k([1, 2], {1, 2}, 10) == 1.0


def test_empty_recommendation_list():
    assert precision_at_k([], {1}, 5) == 0.0
    assert recall_at_k([], {1}, 5) == 0.0
    assert ndcg_at_k([], {1}, 5) == 0.0


@pytest.mark.parametrize("fn", [precision_at_k, recall_at_k, hit_rate_at_k, ndcg_at_k])
@pytest.mark.parametrize("k", [0, -1])
def test_non_positive_k_rejected(fn, k):
    with pytest.raises(ValueError):
        fn([1, 2], {1}, k)


def test_jaccard():
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard({"a"}, {"a"}) == 1.0
    assert jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_both_empty_is_zero():
    assert jaccard(set(), set()) == 0.0
    assert jaccard(set(), {"a"}) == 0.0


def test_coverage():
    lists = [[1, 2, 3], [2, 3, 4], [1, 4, 5]]
    assert coverage(lists, 10) == 0.5  # 서로 다른 아이템 5개 / 후보 10개


def test_coverage_rejects_empty_candidates():
    with pytest.raises(ValueError):
        coverage([[1]], 0)


def test_intra_list_diversity():
    sim = np.array(
        [
            [1.0, 0.5, 0.0],
            [0.5, 1.0, 0.5],
            [0.0, 0.5, 1.0],
        ]
    )
    # 쌍 (0,1)=0.5, (0,2)=0.0, (1,2)=0.5 → 평균 유사도 1/3 → 다양성 2/3
    assert intra_list_diversity(sim, [0, 1, 2]) == pytest.approx(2 / 3)
    assert intra_list_diversity(sim, [0, 2]) == 1.0


def test_intra_list_diversity_short_list_is_zero():
    sim = np.eye(3)
    assert intra_list_diversity(sim, [1]) == 0.0
    assert intra_list_diversity(sim, []) == 0.0
