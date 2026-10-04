import sqlite3

import numpy as np
import pytest

from models.hybrid import TOP_K, WEIGHTS, combine_similarities, save_similar_games, top_k_indices


@pytest.fixture
def sims():
    rng = np.random.default_rng(42)
    return tuple(rng.random((6, 6)) for _ in range(3))


def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_combine_keeps_shape(sims):
    genre, cv, nlp = sims
    assert combine_similarities(genre, cv, nlp).shape == genre.shape


@pytest.mark.parametrize("signal", ["genre", "cv", "nlp"])
def test_combine_single_signal_weight_returns_that_matrix(sims, signal):
    genre, cv, nlp = sims
    weights = {"genre": 0.0, "cv": 0.0, "nlp": 0.0, signal: 1.0}
    expected = {"genre": genre, "cv": cv, "nlp": nlp}[signal]
    np.testing.assert_array_equal(combine_similarities(genre, cv, nlp, weights), expected)


def test_combine_default_weights_is_weighted_sum(sims):
    genre, cv, nlp = sims
    expected = WEIGHTS["genre"] * genre + WEIGHTS["cv"] * cv + WEIGHTS["nlp"] * nlp
    np.testing.assert_array_equal(combine_similarities(genre, cv, nlp), expected)


def test_top_k_excludes_self_and_sorts_descending():
    row = np.array([0.99, 0.2, 0.9, 0.5, 0.7])  # 자기 자신(0번)이 가장 높은 점수
    assert top_k_indices(row, 0, 3) == [2, 4, 3]


def test_top_k_ties_keep_original_index_order():
    row = np.array([0.0, 0.5, 0.5, 0.5, 0.9])
    assert top_k_indices(row, 0, 4) == [4, 1, 2, 3]


def test_top_k_larger_than_candidates_returns_all_others():
    assert top_k_indices(np.array([1.0, 0.3, 0.6]), 1, 10) == [0, 2]


def _saved_rows(conn):
    return conn.execute(
        "SELECT appid, similar_appid, score, rank FROM similar_games ORDER BY appid, rank"
    ).fetchall()


@pytest.fixture
def saved():
    n = 15  # TOP_K보다 크게 잡아 10개 컷이 일어나도록 함
    rng = np.random.default_rng(0)
    combined = rng.random((n, n))
    appids = [100 + 3 * i for i in range(n)]  # 연속이 아닌 appid → 인덱스/appid 혼동 검출
    conn = sqlite3.connect(":memory:")
    save_similar_games(conn, appids, combined)
    yield conn, appids, combined
    conn.close()


def test_save_excludes_self(saved):
    conn, _, _ = saved
    assert all(appid != similar for appid, similar, _, _ in _saved_rows(conn))


def test_save_ranks_are_1_to_k_for_every_game(saved):
    conn, appids, _ = saved
    for appid in appids:
        ranks = [r for (a, _, _, r) in _saved_rows(conn) if a == appid]
        assert ranks == list(range(1, TOP_K + 1))


def test_save_scores_non_increasing_with_rank(saved):
    conn, appids, _ = saved
    for appid in appids:
        scores = [s for (a, _, s, _) in _saved_rows(conn) if a == appid]
        assert scores == sorted(scores, reverse=True)


def test_save_maps_indices_back_to_appids_and_scores(saved):
    conn, appids, combined = saved
    index_of = {a: i for i, a in enumerate(appids)}
    for appid, similar, score, _ in _saved_rows(conn):
        assert similar in index_of
        assert score == pytest.approx(combined[index_of[appid]][index_of[similar]])


def test_save_is_idempotent():
    conn = sqlite3.connect(":memory:")
    appids = [1, 2, 3]
    combined = np.array([[1.0, 0.2, 0.8], [0.2, 1.0, 0.5], [0.8, 0.5, 1.0]])
    save_similar_games(conn, appids, combined)
    first = _saved_rows(conn)
    save_similar_games(conn, appids, combined)  # 매번 통째로 재계산(DROP 후 재생성)
    assert _saved_rows(conn) == first
    assert len(first) == 6  # 게임 3개 × 자기 제외 2개
    conn.close()
