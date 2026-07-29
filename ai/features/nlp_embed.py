"""리뷰 텍스트에서 Sentence-BERT로 게임별 임베딩을 뽑는다.

ai/collect/fetch_reviews.py가 모아둔 games.db의 reviews 테이블(게임당 최대
20개, Steam이 도움순으로 골라준 긍정/부정 혼합 리뷰)을 입력으로 쓴다.

리뷰마다 따로 임베딩을 구한 뒤 게임 단위로 평균(mean-pooling)해서 하나의
벡터로 합친다 — 리뷰 하나하나의 뉘앙스보다는 "이 게임에 대해 유저들이
전반적으로 뭐라고 하는지"를 하나의 게임 표현으로 뭉치는 것이 목적이다.

다국어 리뷰(영어/한국어/러시아어 등)가 섞여 있어서 언어별 모델 대신 다국어
Sentence-BERT(paraphrase-multilingual-MiniLM-L12-v2)를 쓴다.

결과는 ai/artifacts/nlp_embeddings.npz에 저장 (appids + embeddings 행렬) —
backend가 무거운 sentence-transformers 의존성 없이 이 배열만 가져다 쓰도록
하기 위함 (ai/backend 분리 원칙). 장르/태그 기반 유사도(content_based.py)와
결합하는 건 다음 단계(하이브리드)에서 한다.
"""

import sqlite3
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "games.db"
ARTIFACT_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "nlp_embeddings.npz"
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

sys.stdout.reconfigure(encoding="utf-8")


def load_reviews(conn: sqlite3.Connection) -> tuple[list[int], list[str]]:
    rows = conn.execute(
        "SELECT appid, review FROM reviews WHERE review IS NOT NULL AND review != ''"
    ).fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    review_appids, texts = load_reviews(conn)
    conn.close()
    print(f"리뷰 {len(texts)}건 ({len(set(review_appids))}개 게임)")

    model = SentenceTransformer(MODEL_NAME)
    vectors = model.encode(texts, show_progress_bar=True, batch_size=64)

    sums: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}
    for appid, vec in zip(review_appids, vectors):
        sums[appid] = sums.get(appid, np.zeros_like(vec)) + vec
        counts[appid] = counts.get(appid, 0) + 1

    appids = list(sums.keys())
    embeddings = np.array([sums[a] / counts[a] for a in appids], dtype=np.float32)

    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez(ARTIFACT_PATH, appids=np.array(appids), embeddings=embeddings)
    print(f"저장 완료: {ARTIFACT_PATH} ({len(appids)}개 게임, {embeddings.shape[1]}차원)")


if __name__ == "__main__":
    main()
