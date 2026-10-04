"""유사 게임 추천(`similar_games`)의 신호별 비교 평가 (ablation).

genre-only / cv-only / nlp-only / hybrid 네 설정이 "비슷한 게임"을 얼마나 잘 찾는지를
같은 정답으로 비교한다. 서비스의 추천은 게임 간 유사도(item-item)라 정답 라벨이 따로
없으므로, 정답은 Steam 유저 태그로 만든 대리 지표다:

    두 게임의 태그 집합 자카드 유사도 >= --jaccard-threshold  ⇒  "관련 있음"

순환 평가 방지: 운영 hybrid.py는 genre와 tags를 합쳐 모델 입력으로 쓰는데, 그대로
tags를 정답으로 쓰면 장르/태그 신호가 trivially 이긴다. 그래서 평가용 genre-only와
hybrid는 모델 입력에서 tags를 빼고 genre 필드만 쓴다 (정답은 tags만으로 계산).
단, 장르와 태그는 완전히 독립이 아니다 — 이 한계는 results.md에 명시된다.
따라서 여기서 재는 hybrid는 "운영 hybrid에서 장르/태그 신호를 장르-only로 바꾼 것"이다.

실행 (ai/ 에서):
    python -m evaluation.eval_similar --k 10 --jaccard-threshold 0.3 --seed 42

필요한 입력: ai/data/games.db, ai/artifacts/{nlp,cv}_embeddings.npz
(README "로컬 실행"의 데이터 파이프라인으로 생성). 없으면 안내 메시지와 함께 종료한다.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):  # `python evaluation/eval_similar.py`로 직접 실행하는 경우
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from evaluation.metrics import coverage, intra_list_diversity, ndcg_at_k, precision_at_k
from models.hybrid import (
    WEIGHTS,
    combine_similarities,
    load_embedding_artifact,
    load_genre_texts,
)

AI_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = AI_DIR / "data" / "games.db"
DEFAULT_ARTIFACTS_DIR = AI_DIR / "artifacts"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent

SETTINGS = ("genre-only", "cv-only", "nlp-only", "hybrid")
REQUIRED_COLUMNS = {"appid", "genre", "tags", "sections", "review_count"}


class EvaluationError(Exception):
    """입력 데이터 부족/불일치 등 사용자가 조치해야 하는 오류 (스택트레이스 없이 안내만 출력)."""


# ---------------------------------------------------------------------------
# 입력 로딩
# ---------------------------------------------------------------------------


def _display_path(path: Path) -> str:
    """결과 문서에 남기는 경로. ai/ 아래면 상대경로로 적어 로컬 사용자 경로가 커밋되지 않게 한다."""
    try:
        return path.resolve().relative_to(AI_DIR).as_posix()
    except ValueError:
        return str(path)


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)


def check_inputs(db_path: Path, artifacts_dir: Path) -> None:
    if not db_path.exists():
        raise EvaluationError(
            f"games.db가 없습니다: {db_path}\n"
            "  데이터 파이프라인(README '로컬 실행' 2번)으로 먼저 생성하거나, "
            "--db로 경로를 지정하세요."
        )
    conn = _open_readonly(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    except sqlite3.DatabaseError as exc:
        raise EvaluationError(f"games.db를 SQLite로 열 수 없습니다: {db_path} ({exc})") from exc
    finally:
        conn.close()
    if not columns:
        raise EvaluationError(f"games.db에 games 테이블이 없습니다: {db_path}")
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise EvaluationError(f"games 테이블에 필요한 컬럼이 없습니다: {sorted(missing)}")

    generators = {"cv": "features/cv_embed.py", "nlp": "features/nlp_embed.py"}
    for name, script in generators.items():
        path = artifacts_dir / f"{name}_embeddings.npz"
        if not path.exists():
            raise EvaluationError(
                f"{name} 임베딩 파일이 없습니다: {path}\n"
                f"  ai/{script}로 생성하거나, --artifacts-dir로 경로를 지정하세요."
            )


def load_embeddings(name: str, artifacts_dir: Path) -> dict[int, np.ndarray]:
    try:
        return load_embedding_artifact(name, artifacts_dir)
    except (KeyError, ValueError, OSError) as exc:
        raise EvaluationError(
            f"{name}_embeddings.npz를 읽을 수 없습니다 (appids/embeddings 배열 필요): {exc}"
        ) from exc


def load_tag_sets(conn: sqlite3.Connection) -> dict[int, frozenset[str]]:
    """appid → 유저 태그 이름 집합. SteamSpy가 빈 리스트(`[]`)나 NULL을 주는 게임은 빈 집합."""
    result: dict[int, frozenset[str]] = {}
    for appid, tags_json in conn.execute("SELECT appid, tags FROM games"):
        tags: frozenset[str] = frozenset()
        if tags_json:
            try:
                parsed = json.loads(tags_json)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                tags = frozenset(parsed.keys())
        result[appid] = tags
    return result


def load_review_counts(conn: sqlite3.Connection) -> dict[int, int | None]:
    return {appid: count for appid, count in conn.execute("SELECT appid, review_count FROM games")}


def select_universe(
    genre_texts: dict[int, str],
    nlp_vecs: dict[int, np.ndarray],
    cv_vecs: dict[int, np.ndarray],
    tag_sets: dict[int, frozenset[str]],
    seed: int,
) -> tuple[list[int], dict[str, int]]:
    """평가 대상 게임(쿼리이자 후보). 세 신호가 모두 있고 태그가 비어 있지 않은 게임.

    태그가 없으면 정답을 만들 수 없어 쿼리로도 후보로도 쓰지 않는다. 순서는 seed로
    섞는다 — 동점일 때 인덱스 순서로 승부가 갈리는데, 이 순서가 appid 정렬이면 오래된
    게임이 체계적으로 유리해지기 때문 (장르-only는 동점이 매우 흔하다).
    """
    with_all_signals = set(genre_texts) & set(nlp_vecs) & set(cv_vecs)
    evaluable = sorted(a for a in with_all_signals if tag_sets.get(a))
    order = np.random.default_rng(seed).permutation(len(evaluable))
    appids = [evaluable[i] for i in order]
    counts = {
        "n_genre": len(genre_texts),
        "n_nlp": len(nlp_vecs),
        "n_cv": len(cv_vecs),
        "n_with_all_signals": len(with_all_signals),
        "n_excluded_empty_tags": len(with_all_signals) - len(evaluable),
        "n_evaluated_games": len(appids),
    }
    return appids, counts


def tag_matrix(appids: list[int], tag_sets: dict[int, frozenset[str]]) -> np.ndarray:
    vocab = {t: i for i, t in enumerate(sorted(set().union(*(tag_sets[a] for a in appids))))}
    matrix = np.zeros((len(appids), len(vocab)))
    for row, appid in enumerate(appids):
        for tag in tag_sets[appid]:
            matrix[row, vocab[tag]] = 1.0
    return matrix


# ---------------------------------------------------------------------------
# 평가
# ---------------------------------------------------------------------------


def _mean_sem(values: list[float]) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    sem = float(arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 else 0.0
    return float(arr.mean()), sem


def _nanmedian(values: np.ndarray) -> float | None:
    values = values[~np.isnan(values)]
    return float(np.median(values)) if len(values) else None


def evaluate(
    genre_matrix: np.ndarray,
    cv_matrix: np.ndarray,
    nlp_matrix: np.ndarray,
    tags: np.ndarray,
    review_counts: np.ndarray,
    *,
    k: int,
    threshold: float,
    weights: dict[str, float] = WEIGHTS,
    block_size: int = 256,
) -> dict:
    """네 설정을 같은 쿼리·정답으로 평가한다.

    입력 행렬은 모두 같은 행 순서(게임 순서)이고, genre/cv/nlp는 행이 L2 정규화돼 있어
    행렬곱이 곧 코사인 유사도다. n×n 행렬을 통째로 만들면 게임 1만 개에서 수 GB라
    쿼리를 block_size씩 잘라 블록 단위로 계산한다.

    정답이 하나도 없는 쿼리는 NDCG가 정의되지 않고 모든 설정이 0점이라 평가에서 뺀다
    (모든 설정에 동일하게 적용되므로 비교는 공정하다).

    diversity는 설정 자신의 유사도 공간이 아니라 태그 벡터 코사인 공간에서 잰다 —
    설정마다 공간이 다르면 설정 간 비교가 안 되기 때문.
    """
    n = len(genre_matrix)
    if n - 1 < k:
        raise EvaluationError(f"평가 대상 게임이 너무 적습니다: {n}개 (k={k}이면 최소 {k + 1}개 필요)")

    sizes = tags.sum(axis=1)
    tags_unit = tags / np.sqrt(sizes)[:, None]
    per_query = {s: {"precision": [], "ndcg": [], "tag_jaccard": [], "diversity": []} for s in SETTINGS}
    recs: dict[str, list[list[int]]] = {s: [] for s in SETTINGS}
    n_relevant: list[int] = []
    random_jaccard: list[float] = []
    n_without_relevant = 0

    for start in range(0, n, block_size):
        rows = np.arange(start, min(start + block_size, n))
        local = np.arange(len(rows))

        inter = tags[rows] @ tags.T
        union = sizes[rows][:, None] + sizes[None, :] - inter
        jaccard = inter / union
        jaccard[local, rows] = 0.0
        relevant_mask = jaccard >= threshold
        relevant_mask[local, rows] = False
        counts = relevant_mask.sum(axis=1)
        valid = np.flatnonzero(counts > 0)
        n_without_relevant += len(rows) - len(valid)
        if len(valid) == 0:
            continue

        genre_sim = genre_matrix[rows] @ genre_matrix.T
        cv_sim = cv_matrix[rows] @ cv_matrix.T
        nlp_sim = nlp_matrix[rows] @ nlp_matrix.T
        sims = {
            "genre-only": genre_sim,
            "cv-only": cv_sim,
            "nlp-only": nlp_sim,
            "hybrid": combine_similarities(genre_sim, cv_sim, nlp_sim, weights),
        }
        # 자기 자신을 -inf로 막고 점수 내림차순, 동점은 (seed로 섞인) 인덱스 순서 — hybrid.top_k_indices와 같은 규칙
        top = {}
        for name, sim in sims.items():
            scores = sim[valid].copy()
            scores[np.arange(len(valid)), rows[valid]] = -np.inf
            top[name] = np.argsort(-scores, axis=1, kind="stable")[:, :k]

        for vi, li in enumerate(valid):
            relevant = set(np.flatnonzero(relevant_mask[li]).tolist())
            n_relevant.append(len(relevant))
            random_jaccard.append(float(jaccard[li].sum() / (n - 1)))
            for name in SETTINGS:
                rec = top[name][vi].tolist()
                recs[name].append(rec)
                stats = per_query[name]
                stats["precision"].append(precision_at_k(rec, relevant, k))
                stats["ndcg"].append(ndcg_at_k(rec, relevant, k))
                stats["tag_jaccard"].append(float(jaccard[li, rec].mean()))
                tag_cos = tags_unit[rec] @ tags_unit[rec].T
                stats["diversity"].append(intra_list_diversity(tag_cos, list(range(k))))

    n_queries = len(n_relevant)
    if n_queries == 0:
        raise EvaluationError(
            f"정답이 있는 쿼리가 하나도 없습니다 (jaccard 임계값 {threshold}이 너무 높거나 태그가 빈약함)."
        )

    popularity_all = _nanmedian(review_counts)
    settings_result = {}
    for name in SETTINGS:
        stats = per_query[name]
        entry = {}
        for metric in ("precision", "ndcg", "tag_jaccard", "diversity"):
            mean, sem = _mean_sem(stats[metric])
            entry[metric] = mean
            entry[f"{metric}_sem"] = sem
        entry["coverage"] = coverage(recs[name], n)
        recommended_counts = review_counts[np.concatenate([np.asarray(r) for r in recs[name]])]
        entry["median_review_count_recommended"] = _nanmedian(recommended_counts)
        settings_result[name] = entry

    return {
        "settings": settings_result,
        "baseline_random": {
            "precision": float(np.mean([c / (n - 1) for c in n_relevant])),
            "tag_jaccard": float(np.mean(random_jaccard)),
        },
        "median_review_count_all": popularity_all,
        "queries": {
            "n_queries": n_queries,
            "n_without_relevant": n_without_relevant,
            "mean_relevant_per_query": float(np.mean(n_relevant)),
            "median_relevant_per_query": float(np.median(n_relevant)),
        },
    }


def run_evaluation(
    db_path: Path,
    artifacts_dir: Path,
    *,
    k: int,
    threshold: float,
    seed: int,
    block_size: int = 256,
) -> dict:
    if k <= 0:
        raise EvaluationError(f"--k는 1 이상이어야 합니다: {k}")
    if block_size <= 0:
        raise EvaluationError(f"--block-size는 1 이상이어야 합니다: {block_size}")
    if not 0 < threshold <= 1:
        raise EvaluationError(f"--jaccard-threshold는 0 초과 1 이하여야 합니다: {threshold}")
    check_inputs(db_path, artifacts_dir)

    conn = _open_readonly(db_path)
    try:
        genre_texts = load_genre_texts(conn, include_tags=False)  # 순환 평가 방지: 태그 제외
        tag_sets = load_tag_sets(conn)
        review_count_by_appid = load_review_counts(conn)
    finally:
        conn.close()
    nlp_vecs = load_embeddings("nlp", artifacts_dir)
    cv_vecs = load_embeddings("cv", artifacts_dir)

    appids, counts = select_universe(genre_texts, nlp_vecs, cv_vecs, tag_sets, seed)
    if not appids:
        raise EvaluationError(
            "세 신호(장르/NLP/CV)가 모두 있고 태그가 비어 있지 않은 게임이 없습니다. "
            f"(장르 {counts['n_genre']}, NLP {counts['n_nlp']}, CV {counts['n_cv']}개)"
        )

    genre_matrix = TfidfVectorizer().fit_transform([genre_texts[a] for a in appids]).toarray()
    cv_matrix = normalize(np.array([cv_vecs[a] for a in appids]))
    nlp_matrix = normalize(np.array([nlp_vecs[a] for a in appids]))
    review_counts = np.array(
        [np.nan if review_count_by_appid.get(a) is None else review_count_by_appid[a] for a in appids],
        dtype=float,
    )

    results = evaluate(
        genre_matrix,
        cv_matrix,
        nlp_matrix,
        tag_matrix(appids, tag_sets),
        review_counts,
        k=k,
        threshold=threshold,
        block_size=block_size,
    )
    results["params"] = {"k": k, "jaccard_threshold": threshold, "seed": seed, "weights": dict(WEIGHTS)}
    results["data"] = {
        "db": _display_path(db_path),
        "artifacts_dir": _display_path(artifacts_dir),
        **counts,
    }
    results["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return results


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------


def _pm(mean: float, sem: float, digits: int = 3) -> str:
    return f"{mean:.{digits}f} ± {sem:.{digits}f}"


def _fmt_count(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}"


def render_markdown(results: dict) -> str:
    p, d, q = results["params"], results["data"], results["queries"]
    k = p["k"]
    w = p["weights"]
    base = results["baseline_random"]
    header = (
        f"| 설정 | Precision@{k} | NDCG@{k} | 평균 Tag-Jaccard@{k} | Coverage | "
        f"리스트 내 다양성 | 추천 review_count 중앙값 |\n|---|---|---|---|---|---|---|\n"
    )
    rows = []
    for name in SETTINGS:
        r = results["settings"][name]
        rows.append(
            f"| {name} | {_pm(r['precision'], r['precision_sem'])} | {_pm(r['ndcg'], r['ndcg_sem'])} "
            f"| {_pm(r['tag_jaccard'], r['tag_jaccard_sem'])} | {r['coverage']:.3f} "
            f"| {_pm(r['diversity'], r['diversity_sem'])} "
            f"| {_fmt_count(r['median_review_count_recommended'])} |"
        )
    rows.append(
        f"| (참고) 무작위 기대값 | {base['precision']:.3f} | - | {base['tag_jaccard']:.3f} | - | - "
        f"| {_fmt_count(results['median_review_count_all'])} (전체 중앙값) |"
    )

    return f"""# 유사 게임 추천 평가 — 신호별 비교

- 생성: {results["generated_at"]}
- 실행: `python -m evaluation.eval_similar --k {k} --jaccard-threshold {p["jaccard_threshold"]} --seed {p["seed"]}`
- 데이터: `{d["db"]}`, `{d["artifacts_dir"]}`
- 이 문서는 `eval_similar.py`가 자동 생성한다. 아래 수치는 모두 그 실행 결과이며, 손으로 고치지 않는다.

## 평가 대상

| 항목 | 값 |
|---|---|
| 장르 있는 게임 / NLP 임베딩 / CV 임베딩 | {d["n_genre"]:,} / {d["n_nlp"]:,} / {d["n_cv"]:,} |
| 세 신호가 모두 있는 게임 | {d["n_with_all_signals"]:,} |
| 태그가 비어 있어 제외 | {d["n_excluded_empty_tags"]:,} |
| **평가 대상 게임 (쿼리이자 후보)** | **{d["n_evaluated_games"]:,}** |
| 정답이 1개 이상 있는 쿼리 (실제 평가에 사용) | {q["n_queries"]:,} |
| 정답이 하나도 없어 제외한 쿼리 | {q["n_without_relevant"]:,} |
| 쿼리당 정답 수 (평균 / 중앙값) | {q["mean_relevant_per_query"]:.1f} / {q["median_relevant_per_query"]:.1f} |

## 결과 (K={k}, 정답 = 태그 자카드 ≥ {p["jaccard_threshold"]})

{header}{chr(10).join(rows)}

각 값은 쿼리 평균 ± 표준오차(쿼리 간 변동만 반영, seed/임계값 변동은 미포함).
네 설정은 같은 쿼리와 같은 정답으로 평가했다.

hybrid 가중치: 장르 {w["genre"]} / CV {w["cv"]} / NLP {w["nlp"]} (`models/hybrid.py`의 `WEIGHTS`, 이 평가로 조정하지 않음).

## 지표 정의

- **정답**: 두 게임의 Steam 유저 태그 집합의 자카드 유사도가 임계값 이상이면 "관련 있음".
- **Precision@K / NDCG@K**: Top-K 안의 정답 비율 / 순서까지 반영한 이진 NDCG.
- **평균 Tag-Jaccard@K**: Top-K 추천과 쿼리 게임의 태그 자카드 평균 (임계값 없이 연속값으로 본 근접도).
- **Coverage**: 모든 추천 리스트에 한 번이라도 등장한 서로 다른 게임 수 / 후보 게임 수.
- **리스트 내 다양성**: Top-K 안 모든 게임 쌍의 평균 (1 − 태그 벡터 코사인). 설정마다 유사도 공간이 달라 비교가 안 되므로 모든 설정을 같은 태그 공간에서 잰다.
- **추천 review_count 중앙값**: 모든 추천 슬롯에 등장한 게임의 리뷰 수 중앙값. 맨 아래 줄의 전체 중앙값보다 크면 인기작 쪽으로 쏠렸다는 신호.
- **무작위 기대값**: 후보에서 무작위로 K개를 뽑았을 때의 기대 Precision / 평균 Tag-Jaccard (계산식, 추첨 아님).

## 방법

- 쿼리 게임마다 자기 자신을 뺀 후보 전체를 점수 내림차순으로 정렬해 Top-K를 취한다.
- **순환 평가 방지**: 모델 입력에서 `tags`를 제외하고 `genre` 필드만 쓴다 (genre-only, hybrid의 장르 신호). 정답은 `tags`만으로 계산한다.
- 동점은 seed로 섞은 게임 순서로 가른다. 장르 텍스트가 같은 게임이 많아 genre-only는 동점이 흔하고, appid 순서로 가르면 오래된 게임이 체계적으로 유리해지기 때문이다.

## 한계 (결과를 읽을 때 반드시 고려)

1. **정답은 태그 기반 대리 지표다.** 사람이 "비슷하다"고 판단한 라벨이 아니라, 유저 태그가 겹치는 정도일 뿐이다. 태그를 신호로 쓰지 않는 CV/NLP가 구조적으로 불리할 수 있다.
2. **순환 평가 위험이 완전히 사라지지는 않는다.** 모델 입력에서 `tags`는 뺐지만 장르와 태그는 독립이 아니다 (Action, RPG, Indie, Strategy 같은 이름이 양쪽에 겹친다). 그래서 genre-only와 hybrid의 장르 신호가 정답과 일부 겹쳐 유리해졌을 가능성이 있다.
3. **여기서 재는 hybrid는 운영 hybrid와 다르다.** 운영 `hybrid.py`는 장르+태그 TF-IDF를 쓰지만, 태그를 정답으로 쓰는 이 평가에서는 같은 입력을 쓸 수 없어 장르-only 신호로 대체했다. 운영 모델 자체의 수치가 아니다.
4. **정답은 임계값({p["jaccard_threshold"]})에 의존한다.** 임계값을 바꾸면 수치와 순위가 달라질 수 있다 (`--jaccard-threshold`). 정답이 하나도 없는 쿼리는 모든 설정에서 평가 불가라 제외했다.
5. **가중치 튜닝은 하지 않았다.** hybrid 가중치는 기존 직관값 그대로다. 이 정답으로 가중치를 고르면 같은 정답으로 점수를 보고하게 되므로, 튜닝하려면 게임을 검증/테스트로 분할해야 한다.
6. **CF(SVD)는 평가하지 않았다.** 로그인 유저가 1명뿐이라 Recall@K 같은 지표가 의미가 없다. 유저가 충분히 모이면 유저 단위 홀드아웃으로 별도 평가한다.
7. **표본 크기와 분산.** 표준오차는 쿼리 간 변동만 반영한다. 동점 처리에 쓰는 seed가 달라지면 특히 genre-only 수치가 흔들릴 수 있다.
"""


def write_outputs(results: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "results.json"
    md_path = out_dir / "results.md"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(results), encoding="utf-8")
    return json_path, md_path


def print_summary(results: dict) -> None:
    d, q, p = results["data"], results["queries"], results["params"]
    print(
        f"평가 대상 게임 {d['n_evaluated_games']:,}개 "
        f"(세 신호 모두 있는 {d['n_with_all_signals']:,}개 중 태그가 비어 {d['n_excluded_empty_tags']:,}개 제외)"
    )
    print(
        f"정답이 있는 쿼리 {q['n_queries']:,}개 (정답 없는 쿼리 {q['n_without_relevant']:,}개 제외), "
        f"K={p['k']}, jaccard>={p['jaccard_threshold']}, seed={p['seed']}"
    )
    k = p["k"]
    print(f"{'설정':<12} {'P@' + str(k):>8} {'NDCG@' + str(k):>8} {'Jac@' + str(k):>8} {'Cov':>7} {'Div':>7}")
    for name in SETTINGS:
        r = results["settings"][name]
        print(
            f"{name:<12} {r['precision']:>8.3f} {r['ndcg']:>8.3f} {r['tag_jaccard']:>8.3f} "
            f"{r['coverage']:>7.3f} {r['diversity']:>7.3f}"
        )
    base = results["baseline_random"]
    print(f"{'(random)':<12} {base['precision']:>8.3f} {'-':>8} {base['tag_jaccard']:>8.3f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="유사 게임 추천 신호별 비교 평가")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--jaccard-threshold", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--block-size", type=int, default=256, help="한 번에 처리할 쿼리 수 (메모리 조절)")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):  # 오류 안내문(한글)이 Windows 콘솔 인코딩에서 깨지지 않게
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    try:
        results = run_evaluation(
            args.db,
            args.artifacts_dir,
            k=args.k,
            threshold=args.jaccard_threshold,
            seed=args.seed,
            block_size=args.block_size,
        )
    except EvaluationError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    print_summary(results)
    json_path, md_path = write_outputs(results, args.out_dir)
    print(f"저장 완료: {json_path}, {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
