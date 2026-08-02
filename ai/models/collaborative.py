"""SVD 기반 협업 필터링(CF) — 유저 플레이타임으로 게임 추천 점수를 학습한다.

backend/data/app.db의 owned_games(Steam 로그인 시 GetOwnedGames로 수집된
유저 라이브러리)를 입력으로 쓴다. ai는 backend의 운영 DB를 읽기 전용으로만
참조하고(ai/backend 분리 원칙, backend가 games.db를 읽기 전용으로 참조하는
것과 대칭), 학습 결과는 ai/data/games.db의 cf_recommendations 테이블에
저장한다 — backend는 이 테이블만 읽으면 되므로 scikit-surprise 같은 무거운
학습 의존성이 backend까지 퍼지지 않는다.

지금은 로그인 유저가 1명뿐이라 실제로 쓸만한 개인화 추천은 아직 안 나온다
(교차 유저 신호가 없어 SVD가 사실상 전역 평균에 수렴함). 유저가 늘어날
때마다 이 스크립트를 재실행하면 자동으로 좋아지는 구조만 먼저 만들어둔다.

플레이타임(분)을 그대로 "평점"으로 쓰면 몇만 분씩 플레이한 게임에 압도되니
log1p로 눌러서 완만하게 만든다.
"""

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from surprise import SVD, Dataset, Reader

BACKEND_DB_PATH = Path(__file__).resolve().parents[2] / "backend" / "data" / "app.db"
GAMES_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "games.db"
TOP_N = 20
MIN_USERS_FOR_TRAINING = 1

sys.stdout.reconfigure(encoding="utf-8")


def load_interactions() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{BACKEND_DB_PATH}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT steamid, appid, playtime_forever FROM owned_games", conn
    )
    conn.close()
    df["rating"] = np.log1p(df["playtime_forever"].clip(lower=0))
    return df


def load_candidate_appids() -> list[int]:
    conn = sqlite3.connect(GAMES_DB_PATH)
    rows = conn.execute(
        "SELECT appid FROM games WHERE sections IS NOT NULL AND is_active = 1"
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def save_recommendations(recs: dict[str, list[tuple[int, float]]]) -> None:
    conn = sqlite3.connect(GAMES_DB_PATH)
    conn.execute("DROP TABLE IF EXISTS cf_recommendations")
    conn.execute(
        """
        CREATE TABLE cf_recommendations (
            steamid TEXT,
            appid INTEGER,
            score REAL,
            rank INTEGER,
            PRIMARY KEY (steamid, rank)
        )
        """
    )
    for steamid, items in recs.items():
        conn.executemany(
            "INSERT INTO cf_recommendations (steamid, appid, score, rank) VALUES (?, ?, ?, ?)",
            [(steamid, appid, score, rank) for rank, (appid, score) in enumerate(items, start=1)],
        )
    conn.commit()
    conn.close()


def main() -> None:
    interactions = load_interactions()
    n_users = interactions["steamid"].nunique()
    print(f"유저 {n_users}명, 상호작용 {len(interactions)}건으로 학습")
    if n_users < MIN_USERS_FOR_TRAINING:
        print("학습할 유저가 없어 종료")
        return

    reader = Reader(rating_scale=(interactions["rating"].min(), interactions["rating"].max()))
    data = Dataset.load_from_df(interactions[["steamid", "appid", "rating"]], reader)
    trainset = data.build_full_trainset()

    model = SVD()
    model.fit(trainset)

    candidate_appids = load_candidate_appids()
    owned_by_user = interactions.groupby("steamid")["appid"].apply(set).to_dict()

    recs: dict[str, list[tuple[int, float]]] = {}
    for steamid, owned in owned_by_user.items():
        unowned = [a for a in candidate_appids if a not in owned]
        predictions = [(a, model.predict(steamid, a).est) for a in unowned]
        predictions.sort(key=lambda pair: pair[1], reverse=True)
        recs[steamid] = predictions[:TOP_N]

    save_recommendations(recs)
    print(f"cf_recommendations 저장 완료: 유저 {len(recs)}명 x 상위 {TOP_N}개")


if __name__ == "__main__":
    main()
