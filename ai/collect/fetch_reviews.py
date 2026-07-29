"""NLP 임베딩/감성분석용 리뷰 본문을 큐레이션된 게임(sections IS NOT NULL)에 대해 수집한다.

Steam 공식 리뷰 API(store.steampowered.com/appreviews)를 쓴다. review_type=all
+ filter=all(도움순)로 한 번에 요청하면 Steam이 자체적으로 긍정/부정을 섞어서
상위 리뷰를 준다 (실측: 20개 중 10긍정/10부정으로 균형 잡힘) — 극찬 일색인
인기 게임도 부정 리뷰가 묻히지 않고 섞여 들어온다.

voted_up이 이미 리뷰 단위의 긍/부정 라벨이라 별도 감성분류기 학습 없이도
"세부 불만 포인트"는 voted_up=false 리뷰만 모아서 바로 분석 가능하다.

게임당 1회 요청이라 4,463개 전체를 돌아도 몇 시간이면 끝난다. 이미 리뷰가
있는 게임은 대상에서 자동으로 빠지므로 재실행해도 이어서 처리된다
(resumable).
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import requests

from db import get_connection, insert_reviews

REVIEWS_API = "https://store.steampowered.com/appreviews/{appid}"
REQUEST_DELAY_SEC = 1.5
REVIEWS_PER_GAME = 20

sys.stdout.reconfigure(encoding="utf-8")


def pending_appids(conn) -> list[int]:
    rows = conn.execute(
        """
        SELECT g.appid FROM games g
        WHERE g.sections IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.appid = g.appid)
        ORDER BY g.appid
        """
    ).fetchall()
    return [row[0] for row in rows]


def fetch_reviews_for(appid: int, max_retries: int = 3) -> list[dict]:
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                REVIEWS_API.format(appid=appid),
                params={
                    "json": 1,
                    "filter": "all",
                    "language": "all",
                    "review_type": "all",
                    "purchase_type": "all",
                    "num_per_page": REVIEWS_PER_GAME,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                return []
            return data.get("reviews", [])
        except (requests.RequestException, ValueError) as exc:
            print(f"  appid={appid} 시도 {attempt}/{max_retries} 실패: {exc}")
            if attempt == max_retries:
                return []
            time.sleep(REQUEST_DELAY_SEC * 3)
    return []


def main(limit: int | None) -> None:
    conn = get_connection()
    appids = pending_appids(conn)
    total_pending = len(appids)
    if limit:
        appids = appids[:limit]
    print(f"리뷰 수집 대상 {total_pending}개 중 이번 실행에서 {len(appids)}개 처리")

    total_reviews = 0
    for i, appid in enumerate(appids, start=1):
        reviews = fetch_reviews_for(appid)
        now = datetime.now(timezone.utc).isoformat()
        if reviews:
            insert_reviews(conn, appid, reviews, now)
            total_reviews += len(reviews)
        conn.commit()

        if i % 50 == 0 or i == len(appids):
            print(f"[{i}/{len(appids)}] 누적 리뷰 {total_reviews}건")
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(
        f"이번 실행 완료: {len(appids)}개 게임, 리뷰 {total_reviews}건 수집. "
        f"남은 대상 {total_pending - len(appids)}개"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(limit=args.limit)
