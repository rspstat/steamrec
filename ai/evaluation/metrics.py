"""추천 평가 지표 (순수 함수만 — DB/모델/파일 의존 없음).

`recommended`는 점수 내림차순으로 정렬된 아이템 리스트, `relevant`는 정답 집합.
리스트가 k보다 짧으면 모자란 자리는 "맞히지 못한 것"으로 센다 (분모는 항상 k).
"""

from collections.abc import Collection, Iterable, Sequence

import numpy as np


def _check_k(k: int) -> None:
    if k <= 0:
        raise ValueError(f"k는 1 이상이어야 함: {k}")


def precision_at_k(recommended: Sequence, relevant: Collection, k: int) -> float:
    _check_k(k)
    return sum(x in relevant for x in recommended[:k]) / k


def recall_at_k(recommended: Sequence, relevant: Collection, k: int) -> float:
    _check_k(k)
    if not relevant:
        return 0.0
    return sum(x in relevant for x in recommended[:k]) / len(relevant)


def hit_rate_at_k(recommended: Sequence, relevant: Collection, k: int) -> float:
    """Top-K 안에 정답이 하나라도 있으면 1.0, 아니면 0.0."""
    _check_k(k)
    return float(any(x in relevant for x in recommended[:k]))


def ndcg_at_k(recommended: Sequence, relevant: Collection, k: int) -> float:
    """relevance를 이진(정답=1, 아니면 0)으로 쓰는 NDCG@K."""
    _check_k(k)
    dcg = sum(1 / np.log2(i + 2) for i, x in enumerate(recommended[:k]) if x in relevant)
    ideal = sum(1 / np.log2(i + 2) for i in range(min(len(relevant), k)))
    return float(dcg / ideal) if ideal else 0.0


def jaccard(a: Collection, b: Collection) -> float:
    """두 집합의 자카드 유사도. 둘 다 비어 있으면 0.0 (비교할 정보가 없는 것을 "일치"로 세지 않음)."""
    a, b = set(a), set(b)
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def coverage(recommendation_lists: Iterable[Sequence], n_candidates: int) -> float:
    """추천 리스트 전체에 한 번이라도 등장한 서로 다른 아이템 수 / 후보 아이템 수."""
    if n_candidates <= 0:
        raise ValueError(f"n_candidates는 1 이상이어야 함: {n_candidates}")
    seen: set = set()
    for items in recommendation_lists:
        seen.update(items)
    return len(seen) / n_candidates


def intra_list_diversity(sim: np.ndarray, indices: Sequence[int]) -> float:
    """리스트 안 모든 아이템 쌍의 평균 (1 - 유사도). 아이템이 2개 미만이면 0.0.

    `sim`은 전체 아이템 간 유사도 행렬, `indices`는 그 행렬에서 리스트 아이템의 인덱스.
    """
    if len(indices) < 2:
        return 0.0
    sub = np.asarray(sim)[np.ix_(indices, indices)]
    upper = sub[np.triu_indices(len(indices), k=1)]
    return float(np.mean(1.0 - upper))
