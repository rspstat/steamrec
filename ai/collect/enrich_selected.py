"""discover_sections.py가 뽑은 섹션 후보(sections IS NOT NULL)에 한해
SteamSpy appdetails로 genre/tags/price/owners를 보강한다.

전체 카탈로그가 아니라 이미 섹션 기준으로 추려진 4천 개 안팎만 대상이라
1req/sec로도 완료까지 1~2시간 수준이면 충분하다. genre가 이미 채워진 행은
대상에서 자동으로 빠지므로 중단 후 재실행해도 이어서 처리된다 (resumable).
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import requests

from db import get_connection, upsert_steamspy_game

STEAMSPY_API = "https://steamspy.com/api.php"
REQUEST_DELAY_SEC = 1

sys.stdout.reconfigure(encoding="utf-8")


def pending_appids(conn) -> list[int]:
    rows = conn.execute(
        """
        SELECT appid FROM games
        WHERE sections IS NOT NULL AND (genre IS NULL OR genre = '')
        ORDER BY appid
        """
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
    print(f"섹션 후보 중 보강 대상 {total_pending}개 중 이번 실행에서 {len(appids)}개 처리")

    for i, appid in enumerate(appids, start=1):
        details = fetch_game_details(appid)
        now = datetime.now(timezone.utc).isoformat()
        upsert_steamspy_game(conn, details, now)
        conn.commit()
        if i % 50 == 0 or i == len(appids):
            print(f"[{i}/{len(appids)}] appid={appid} genre={details.get('genre')!r}")
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(f"이번 실행 완료: {len(appids)}개 처리, 남은 대상 {total_pending - len(appids)}개")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(limit=args.limit)
