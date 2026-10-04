import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from sklearn.metrics.pairwise import cosine_similarity

from evaluation import eval_similar
from evaluation.eval_similar import (
    SETTINGS,
    EvaluationError,
    evaluate,
    main,
    run_evaluation,
    select_universe,
)
from evaluation.metrics import coverage, intra_list_diversity, jaccard, ndcg_at_k, precision_at_k
from models.hybrid import combine_similarities, top_k_indices

AI_DIR = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 합성 데이터: 군집이 있는 세계. 태그와 CV는 군집을 반영하고, NLP는 순수 노이즈.
# ---------------------------------------------------------------------------

N_GAMES = 120
N_CLUSTERS = 4


def build_world(root: Path, *, uniform_genre: bool = False, with_tags_column: bool = True) -> tuple[Path, Path]:
    rng = np.random.default_rng(0)
    db_path, artifacts_dir = root / "games.db", root / "artifacts"
    artifacts_dir.mkdir()

    tag_cols = "tags TEXT," if with_tags_column else ""
    conn = sqlite3.connect(db_path)
    conn.execute(
        f"CREATE TABLE games (appid INTEGER PRIMARY KEY, genre TEXT, {tag_cols} sections TEXT, review_count INTEGER)"
    )
    centroids = rng.normal(size=(N_CLUSTERS, 16))
    cv_rows: dict[int, np.ndarray] = {}
    nlp_rows: dict[int, np.ndarray] = {}

    def add(appid, genre, tags_json, sections, cluster=None):
        values = [appid, genre] + ([tags_json] if with_tags_column else []) + [sections, int(rng.integers(5, 5000))]
        conn.execute(f"INSERT INTO games VALUES ({','.join('?' * len(values))})", values)
        center = centroids[cluster] if cluster is not None else 0.0
        cv_rows[appid] = center + (0.3 if cluster is not None else 1.0) * rng.normal(size=16)
        nlp_rows[appid] = rng.normal(size=16)

    for i in range(N_GAMES):
        cluster = i % N_CLUSTERS
        own = rng.choice(6, size=4, replace=False)
        tags = {f"c{cluster}_t{j}": 1 for j in own} | {"Indie": 1}
        genre = "Action" if uniform_genre or cluster < 2 else "Strategy"
        add(1000 + i, genre, json.dumps(tags), "catalog", cluster)
    for a in range(5000, 5005):  # 태그가 빈 리스트 (SteamSpy가 이렇게 줌)
        add(a, "Action", "[]", "catalog")
    add(5010, "Action", None, "catalog")  # 태그 NULL
    add(5020, "Action", json.dumps({"Indie": 1}), None)  # 섹션 없음 → 장르 로더가 제외
    conn.commit()
    conn.close()

    for name, rows in (("cv", cv_rows), ("nlp", nlp_rows)):
        ids = sorted(rows)
        np.savez(
            artifacts_dir / f"{name}_embeddings.npz",
            appids=np.array(ids),
            embeddings=np.array([rows[a] for a in ids], dtype=np.float32),
        )
    return db_path, artifacts_dir


@pytest.fixture
def world(tmp_path):
    return build_world(tmp_path)


def _run(world, **overrides):
    db, art = world
    params = dict(k=10, threshold=0.3, seed=42)
    params.update(overrides)
    return run_evaluation(db, art, **params)


# ---------------------------------------------------------------------------
# evaluate(): 느리지만 단순한 구현과 대조
# ---------------------------------------------------------------------------


def _bruteforce(genre, cv, nlp, tagsets, tags, review, k, threshold):
    n = len(genre)
    sims = {
        "genre-only": genre @ genre.T,
        "cv-only": cv @ cv.T,
        "nlp-only": nlp @ nlp.T,
        "hybrid": combine_similarities(genre @ genre.T, cv @ cv.T, nlp @ nlp.T),
    }
    tag_cos = cosine_similarity(tags)
    acc = {s: {"p": [], "ndcg": [], "jac": [], "div": [], "recs": []} for s in SETTINGS}
    n_relevant = []
    for i in range(n):
        relevant = {j for j in range(n) if j != i and jaccard(tagsets[i], tagsets[j]) >= threshold}
        if not relevant:
            continue
        n_relevant.append(len(relevant))
        for s in SETTINGS:
            rec = top_k_indices(sims[s][i], i, k)  # 운영 hybrid.py의 Top-K 규칙
            acc[s]["p"].append(precision_at_k(rec, relevant, k))
            acc[s]["ndcg"].append(ndcg_at_k(rec, relevant, k))
            acc[s]["jac"].append(np.mean([jaccard(tagsets[i], tagsets[j]) for j in rec]))
            acc[s]["div"].append(intra_list_diversity(tag_cos, rec))
            acc[s]["recs"].append(rec)
    out = {}
    for s in SETTINGS:
        recs = acc[s]["recs"]
        out[s] = {
            "precision": np.mean(acc[s]["p"]),
            "ndcg": np.mean(acc[s]["ndcg"]),
            "tag_jaccard": np.mean(acc[s]["jac"]),
            "diversity": np.mean(acc[s]["div"]),
            "coverage": coverage(recs, n),
            "median_review_count_recommended": np.nanmedian(review[np.concatenate(recs)]),
        }
    return out, n_relevant


@pytest.mark.parametrize("block_size", [1, 7, 1000])
def test_evaluate_matches_bruteforce(block_size):
    rng = np.random.default_rng(1)
    n, n_tags, k, threshold = 30, 8, 5, 0.3
    tagsets = [
        frozenset(rng.choice(n_tags, size=rng.integers(1, 5), replace=False).tolist()) for _ in range(n)
    ]
    tags = np.zeros((n, n_tags))
    for i, ts in enumerate(tagsets):
        tags[i, list(ts)] = 1.0
    # 2의 거듭제곱 분수 값 → 부동소수 오차 없이 블록 크기와 무관하게 점수가 정확히 같고, 동점이 아주 많다
    genre = rng.integers(0, 3, size=(n, 4)) * 0.5
    cv = rng.integers(0, 4, size=(n, 6)) * 0.25
    nlp = rng.integers(0, 5, size=(n, 6)) * 0.25
    review = rng.integers(10, 1000, size=n).astype(float)
    review[3] = np.nan

    got = evaluate(genre, cv, nlp, tags, review, k=k, threshold=threshold, block_size=block_size)
    expected, n_relevant = _bruteforce(genre, cv, nlp, tagsets, tags, review, k, threshold)

    assert got["queries"]["n_queries"] == len(n_relevant)
    assert got["queries"]["n_without_relevant"] == n - len(n_relevant)
    assert got["queries"]["mean_relevant_per_query"] == pytest.approx(np.mean(n_relevant))
    assert got["baseline_random"]["precision"] == pytest.approx(np.mean([c / (n - 1) for c in n_relevant]))
    for s in SETTINGS:
        for metric, value in expected[s].items():
            assert got["settings"][s][metric] == pytest.approx(value), (s, metric)


def test_evaluate_rejects_too_few_games():
    ones = np.ones((3, 2))
    with pytest.raises(EvaluationError, match="너무 적습니다"):
        evaluate(ones, ones, ones, ones, np.ones(3), k=10, threshold=0.3)


def test_evaluate_rejects_when_no_query_has_relevant_items():
    tags = np.eye(12)  # 서로 태그가 하나도 안 겹침 → 자카드 0
    ones = np.ones((12, 2))
    with pytest.raises(EvaluationError, match="정답이 있는 쿼리가 하나도 없"):
        evaluate(ones, ones, ones, tags, np.ones(12), k=5, threshold=0.3)


# ---------------------------------------------------------------------------
# run_evaluation(): 합성 세계에서 종단 검증
# ---------------------------------------------------------------------------


def test_universe_counts_exclude_empty_tags(world):
    data = _run(world)["data"]
    assert data["n_evaluated_games"] == N_GAMES
    assert data["n_excluded_empty_tags"] == 6  # 빈 리스트 5개 + NULL 1개
    assert data["n_with_all_signals"] == N_GAMES + 6  # 섹션 없는 5020은 장르 로더에서 이미 제외
    assert data["n_genre"] == N_GAMES + 6


def test_informative_signal_beats_noise_and_random_baseline(world):
    res = _run(world)
    base = res["baseline_random"]["precision"]
    prec = {s: res["settings"][s]["precision"] for s in SETTINGS}

    assert prec["cv-only"] > 2 * base  # 군집을 반영하는 신호는 무작위보다 한참 낫다
    assert abs(prec["nlp-only"] - base) < 0.08  # 순수 노이즈는 무작위 수준
    assert prec["cv-only"] > prec["nlp-only"] + 0.3
    assert prec["hybrid"] > prec["nlp-only"]
    assert 0 <= res["settings"]["cv-only"]["coverage"] <= 1


def test_tags_do_not_leak_into_genre_signal(tmp_path):
    """순환 평가 회귀 테스트: 모든 게임의 장르가 같고 태그만 군집을 구분한다.

    genre-only 입력에 tags가 섞이면 정답과 같은 정보를 보게 돼 점수가 치솟는다.
    장르만 쓰면 모든 점수가 동점이라 무작위 순서(seed)로 결정돼 기대 수준에 머문다.
    """
    res = _run(build_world(tmp_path, uniform_genre=True))
    base = res["baseline_random"]["precision"]
    assert abs(res["settings"]["genre-only"]["precision"] - base) < 0.08
    assert res["settings"]["cv-only"]["precision"] > 2 * base  # 태그 정보 자체는 데이터에 있다


def test_same_seed_is_reproducible_and_seed_changes_tie_breaking(tmp_path):
    world = build_world(tmp_path, uniform_genre=True)
    a, b, c = _run(world, seed=1), _run(world, seed=1), _run(world, seed=2)
    assert a["settings"] == b["settings"]
    assert a["settings"]["genre-only"]["precision"] != c["settings"]["genre-only"]["precision"]


def test_select_universe_shuffles_by_seed():
    genre = {a: "x" for a in range(50)}
    vecs = {a: np.zeros(2) for a in range(50)}
    tags = {a: frozenset({"t"}) for a in range(50)}
    first, _ = select_universe(genre, vecs, vecs, tags, seed=0)
    again, _ = select_universe(genre, vecs, vecs, tags, seed=0)
    other, _ = select_universe(genre, vecs, vecs, tags, seed=1)
    assert first == again
    assert first != other
    assert sorted(first) == list(range(50))


# ---------------------------------------------------------------------------
# 입력 오류: 명확한 메시지
# ---------------------------------------------------------------------------


def test_missing_db_gives_actionable_error(tmp_path):
    with pytest.raises(EvaluationError, match="games.db가 없습니다") as exc:
        run_evaluation(tmp_path / "nope.db", tmp_path, k=10, threshold=0.3, seed=0)
    assert "nope.db" in str(exc.value)


@pytest.mark.parametrize("missing", ["cv", "nlp"])
def test_missing_npz_names_the_file_and_generator(world, missing):
    db, art = world
    (art / f"{missing}_embeddings.npz").unlink()
    with pytest.raises(EvaluationError) as exc:
        run_evaluation(db, art, k=10, threshold=0.3, seed=0)
    assert f"{missing}_embeddings.npz" in str(exc.value)
    assert f"{missing}_embed.py" in str(exc.value)


def test_npz_without_expected_arrays_is_reported(world):
    db, art = world
    np.savez(art / "cv_embeddings.npz", wrong=np.zeros(3))
    with pytest.raises(EvaluationError, match="cv_embeddings.npz를 읽을 수 없습니다"):
        run_evaluation(db, art, k=10, threshold=0.3, seed=0)


def test_missing_required_column_is_reported(tmp_path):
    db, art = build_world(tmp_path, with_tags_column=False)
    with pytest.raises(EvaluationError, match="tags"):
        run_evaluation(db, art, k=10, threshold=0.3, seed=0)


def test_non_sqlite_file_is_reported(tmp_path):
    bad = tmp_path / "games.db"
    bad.write_text("this is not sqlite")
    with pytest.raises(EvaluationError, match="SQLite"):
        run_evaluation(bad, tmp_path, k=10, threshold=0.3, seed=0)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"k": 0}, "--k"),
        ({"threshold": 0.0}, "--jaccard-threshold"),
        ({"threshold": 1.5}, "--jaccard-threshold"),
        ({"block_size": 0}, "--block-size"),
    ],
)
def test_invalid_parameters(world, overrides, message):
    with pytest.raises(EvaluationError, match=message):
        _run(world, **overrides)


def test_k_larger_than_catalog(world):
    with pytest.raises(EvaluationError, match="너무 적습니다"):
        _run(world, k=N_GAMES)


# ---------------------------------------------------------------------------
# CLI와 출력 파일
# ---------------------------------------------------------------------------


def test_main_writes_json_and_markdown(world, tmp_path, capsys):
    db, art = world
    out = tmp_path / "out"
    code = main(["--db", str(db), "--artifacts-dir", str(art), "--out-dir", str(out), "--k", "5", "--seed", "7"])
    assert code == 0
    assert "평가 대상 게임" in capsys.readouterr().out

    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert results["params"]["k"] == 5 and results["params"]["seed"] == 7
    assert set(results["settings"]) == set(SETTINGS)

    md = (out / "results.md").read_text(encoding="utf-8")
    for name in SETTINGS:
        assert f"| {name} |" in md
    assert "Precision@5" in md
    assert "한계" in md and "순환 평가" in md and "CF" in md
    assert "nan" not in md.lower()


def test_main_missing_db_exits_nonzero_without_traceback(tmp_path, capsys):
    code = main(["--db", str(tmp_path / "nope.db"), "--artifacts-dir", str(tmp_path), "--out-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 1
    assert "오류: games.db가 없습니다" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "results.md").exists()


def test_script_can_run_directly_from_any_cwd(tmp_path):
    """`python evaluation/eval_similar.py`로 직접 실행해도 models/·evaluation/ import가 된다."""
    proc = subprocess.run(
        [sys.executable, str(AI_DIR / "evaluation" / "eval_similar.py"), "--db", str(tmp_path / "nope.db")],
        cwd=tmp_path,
        capture_output=True,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
        encoding="utf-8",
    )
    assert proc.returncode == 1
    assert "games.db가 없습니다" in proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr


def test_result_paths_are_relative_inside_ai_dir_and_untouched_outside(tmp_path):
    from evaluation.eval_similar import _display_path

    assert _display_path(AI_DIR / "data" / "games.db") == "data/games.db"
    assert _display_path(AI_DIR / "artifacts") == "artifacts"
    outside = tmp_path / "games.db"
    assert _display_path(outside) == str(outside)


def test_default_paths_point_into_ai_dir():
    assert eval_similar.DEFAULT_DB_PATH == AI_DIR / "data" / "games.db"
    assert eval_similar.DEFAULT_ARTIFACTS_DIR == AI_DIR / "artifacts"
    assert eval_similar.DEFAULT_OUT_DIR == AI_DIR / "evaluation"
