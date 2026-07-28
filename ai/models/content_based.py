"""장르/태그 기반 콘텐츠 유사도로 '이 게임과 비슷한 게임' 추천을 계산한다.

CF(협업 필터링)는 실제 유저-게임 상호작용 데이터가 쌓여야 가능한데 아직
하나도 없다 (지금까지 모은 건 전부 게임 메타데이터). 그래서 지금 단계의
추천은 이 콘텐츠 기반 유사도가 전부다.

genre + tags를 하나의 텍스트로 합쳐 TF-IDF로 벡터화하고, 코사인 유사도로
게임 간 유사도를 구해 게임마다 상위 K개를 games.db의 `similar_games`
테이블에 저장한다. backend는 이 테이블만 읽으면 되므로 scikit-learn 같은
무거운 의존성이 backend까지 퍼지지 않는다 (ai/backend 분리 원칙).

대상은 큐레이션된 섹션 후보(games.sections IS NOT NULL)로 한정한다 — 아직
장르가 없는 나머지 카탈로그(10만+)는 대상 밖.
"""

import json
import sqlite3
import sys
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "games.db"
TOP_K = 10

sys.stdout.reconfigure(encoding="utf-8")


def load_games(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT appid, name, genre, tags FROM games
        WHERE sections IS NOT NULL AND genre IS NOT NULL AND genre != ''
        """
    ).fetchall()
    games = []
    for appid, name, genre, tags_json in rows:
        tags: list[str] = []
        if tags_json:
            parsed = json.loads(tags_json)
            if isinstance(parsed, dict):
                tags = list(parsed.keys())
        content = genre.replace(",", " ") + " " + " ".join(tags)
        games.append({"appid": appid, "name": name, "content": content})
    return games


def build_similarity(games: list[dict]):
    texts = [g["content"] for g in games]
    vectorizer = TfidfVectorizer()
    matrix = vectorizer.fit_transform(texts)
    return cosine_similarity(matrix)


def save_similar_games(conn: sqlite3.Connection, games: list[dict], sim_matrix) -> None:
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
    n = len(games)
    for i in range(n):
        scores = sim_matrix[i]
        ranked = sorted(
            ((j, scores[j]) for j in range(n) if j != i),
            key=lambda pair: pair[1],
            reverse=True,
        )[:TOP_K]
        conn.executemany(
            "INSERT INTO similar_games (appid, similar_appid, score, rank) VALUES (?, ?, ?, ?)",
            [
                (games[i]["appid"], games[j]["appid"], float(score), rank)
                for rank, (j, score) in enumerate(ranked, start=1)
            ],
        )
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    games = load_games(conn)
    print(f"{len(games)}개 게임으로 유사도 계산")

    sim_matrix = build_similarity(games)
    save_similar_games(conn, games, sim_matrix)

    conn.close()
    print("similar_games 테이블 저장 완료")


if __name__ == "__main__":
    main()
