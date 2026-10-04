"""장르/태그 TF-IDF + NLP 리뷰 임베딩 + CV 이미지 임베딩을 가중 결합한 하이브리드 추천.

CF(협업 필터링)는 아직 로그인 유저가 1명뿐이라 이 결합에 포함하지 않는다
(유저가 늘어나면 별도 신호로 추가할 예정). 지금은 콘텐츠 기반 3개 신호만
결합한다.

세 신호의 실측 품질이 다르다:
  - 장르/태그 TF-IDF (content_based.py): 가장 정확함 (예: Terraria→Starbound)
  - CV 헤더 이미지 CLIP (cv_embed.py): 브랜드/비주얼 스타일은 잘 잡지만
    장르 신호로는 약함
  - NLP 리뷰 Sentence-BERT (nlp_embed.py): Steam 리뷰가 밈/유머 톤이 강해
    "무슨 게임인지"보다 "리뷰 쓰는 말투"를 더 많이 잡아내서 단독 신뢰도가
    낮음 (예: Dota 2가 MOBA가 아니라 배틀로얄과 묶임)

그래서 동일 가중치로 섞지 않고 장르/태그를 주 신호로, CV/NLP는 보조로
낮게 가중한다 (WEIGHTS 상수로 조정 가능). 결과는 content_based.py가 만든
`similar_games` 테이블을 하이브리드 점수로 덮어쓴다 — backend의
`/games/{appid}/similar`는 코드 변경 없이 새 결과를 그대로 받는다.
"""

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "games.db"
ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "artifacts"
TOP_K = 10

WEIGHTS = {
    "genre": 0.6,
    "cv": 0.25,
    "nlp": 0.15,
}

sys.stdout.reconfigure(encoding="utf-8")


def load_genre_texts(conn: sqlite3.Connection, include_tags: bool = True) -> dict[int, str]:
    """`include_tags=False`면 장르만 쓴다 — 태그를 정답으로 삼는 평가(evaluation/)에서
    입력과 정답이 겹치는 순환 평가를 피하기 위한 옵션. 운영(main)은 기본값(True)."""
    rows = conn.execute(
        """
        SELECT appid, genre, tags FROM games
        WHERE sections IS NOT NULL AND genre IS NOT NULL AND genre != ''
        """
    ).fetchall()
    result: dict[int, str] = {}
    for appid, genre, tags_json in rows:
        text = genre.replace(",", " ")
        if include_tags:
            tags: list[str] = []
            if tags_json:
                parsed = json.loads(tags_json)
                if isinstance(parsed, dict):
                    tags = list(parsed.keys())
            text += " " + " ".join(tags)
        result[appid] = text
    return result


def load_embedding_artifact(name: str, artifacts_dir: Path | None = None) -> dict[int, np.ndarray]:
    path = (artifacts_dir or ARTIFACTS_DIR) / f"{name}_embeddings.npz"
    data = np.load(path)
    return {int(a): v for a, v in zip(data["appids"], data["embeddings"])}


def combine_similarities(
    genre_sim: np.ndarray,
    cv_sim: np.ndarray,
    nlp_sim: np.ndarray,
    weights: dict[str, float] = WEIGHTS,
) -> np.ndarray:
    return (
        weights["genre"] * genre_sim + weights["cv"] * cv_sim + weights["nlp"] * nlp_sim
    )


def top_k_indices(scores_row: np.ndarray, i: int, k: int = TOP_K) -> list[int]:
    """`scores_row`에서 자기 자신(i)을 뺀 점수 내림차순 상위 k개의 인덱스.

    동점이면 원래 인덱스 순서를 유지한다 (sorted의 안정 정렬).
    """
    ranked = sorted(
        ((j, scores_row[j]) for j in range(len(scores_row)) if j != i),
        key=lambda pair: pair[1],
        reverse=True,
    )[:k]
    return [j for j, _ in ranked]


def save_similar_games(conn: sqlite3.Connection, appids: list[int], combined: np.ndarray) -> None:
    conn.execute("DROP TABLE IF EXISTS similar_games")
    conn.execute(
        """
        CREATE TABLE similar_games (
            appid INTEGER,
            similar_appid INTEGER,
            score REAL,
            rank INTEGER,
            PRIMARY KEY (appid, rank)
        )
        """
    )
    n = len(appids)
    for i in range(n):
        scores = combined[i]
        conn.executemany(
            "INSERT INTO similar_games (appid, similar_appid, score, rank) VALUES (?, ?, ?, ?)",
            [
                (appids[i], appids[j], float(scores[j]), rank)
                for rank, j in enumerate(top_k_indices(scores, i), start=1)
            ],
        )
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    genre_texts = load_genre_texts(conn)
    nlp_vecs = load_embedding_artifact("nlp")
    cv_vecs = load_embedding_artifact("cv")

    appids = sorted(set(genre_texts) & set(nlp_vecs) & set(cv_vecs))
    print(f"3개 신호(장르/태그, NLP, CV) 모두 있는 게임: {len(appids)}개")

    genre_matrix = TfidfVectorizer().fit_transform([genre_texts[a] for a in appids])
    genre_sim = cosine_similarity(genre_matrix)

    nlp_matrix = normalize(np.array([nlp_vecs[a] for a in appids]))
    cv_matrix = normalize(np.array([cv_vecs[a] for a in appids]))
    nlp_sim = cosine_similarity(nlp_matrix)
    cv_sim = cosine_similarity(cv_matrix)

    combined = combine_similarities(genre_sim, cv_sim, nlp_sim)

    save_similar_games(conn, appids, combined)
    conn.close()
    print("similar_games 테이블을 하이브리드 점수로 갱신 완료")


if __name__ == "__main__":
    main()
