"""장르/태그가 없는 '활성' 게임을 SteamSpy appdetails로 보강한다.

SteamSpy 'all' 벌크 응답에는 genre/tags가 없어서 (Job 1 참고) 개별
appdetails 호출로만 채울 수 있는데, 82,523개 전체를 1req/sec로 돌리면
약 23시간이 걸린다. 그중 owners_estimate가 SteamSpy 최하위 구간
('0 .. 20,000', 거의 안 팔린/발견되지 않은 게임)인 56,472개는 제외하고,
실제 플레이어가 있는 26,051개(약 7.2시간)만 우선 보강 대상으로 삼는다.

genre가 이미 채워진 행은 대상에서 자동으로 빠지므로, 중간에 중단했다가
다시 실행해도 이어서 처리된다 (resumable) — --limit으로 한 번에 처리할
개수를 제한할 수 있다.
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import requests

from db import get_connection, upsert_steamspy_game

STEAMSPY_API = "https://steamspy.com/api.php"
REQUEST_DELAY_SEC = 1
LOWEST_OWNERS_BUCKET = "0 .. 20,000"

sys.stdout.reconfigure(encoding="utf-8")


def pending_appids(conn) -> list[int]:
    rows = conn.execute(
        """
        SELECT appid FROM games
        WHERE (genre IS NULL OR genre = '')
          AND owners_estimate IS NOT NULL
          AND owners_estimate != ?
        ORDER BY appid
        """,
        (LOWEST_OWNERS_BUCKET,),
    ).fetchall()
    return [row[0] for row in rows]


def fetch_game_details(appid: int) -> dict:
    resp = requests.get(
        STEAMSPY_API, params={"request": "appdetails", "appid": appid}, timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def main(limit: int | None) -> None:
    conn = get_connection()
    appids = pending_appids(conn)
    total_pending = len(appids)
    if limit:
        appids = appids[:limit]
    print(f"남은 보강 대상 {total_pending}개 중 이번 실행에서 {len(appids)}개 처리")

    for i, appid in enumerate(appids, start=1):
        details = fetch_game_details(appid)
        now = datetime.now(timezone.utc).isoformat()
        upsert_steamspy_game(conn, details, now)
        conn.commit()
        if i % 20 == 0 or i == len(appids):
            print(f"[{i}/{len(appids)}] appid={appid} genre={details.get('genre')!r}")
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(f"이번 실행 완료: {len(appids)}개 처리, 남은 대상 {total_pending - len(appids)}개")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(limit=args.limit)
