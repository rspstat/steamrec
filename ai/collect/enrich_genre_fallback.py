"""enrich_selected.py 이후에도 genre가 비어 있는 섹션 후보를 Store appdetails로 보강.

SteamSpy는 플레이 데이터가 쌓여야 반영되는 3rd-party라 갓 나온 게임은
genre/tags가 비어 있을 수 있다 (실측: 4,463개 중 1,077개). Store API는
발매 직후에도 자체 `genres` 필드를 바로 제공하므로 이 갭만 메운다.

community tags(SteamSpy 전용 필드)는 이 경로로는 채워지지 않는다 — 나중에
SteamSpy가 따라잡으면 enrich_selected.py를 다시 돌려서 채우면 된다.
genre가 이미 채워진 행은 대상에서 자동으로 빠지므로 재실행해도 이어서
처리된다 (resumable).
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import requests

from db import get_connection, upsert_store_genre_fallback

STORE_API = "https://store.steampowered.com/api/appdetails"
REQUEST_DELAY_SEC = 1.5

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


def fetch_store_details(appid: int) -> dict | None:
    # cc를 안 주면 서버 IP 지역에 따라 통화가 바뀐다 (KRW 등) — price는 다른
    # 소스(SteamSpy)와 전부 USD-cent 기준으로 맞추기 위해 cc=us로 고정한다.
    resp = requests.get(STORE_API, params={"appids": appid, "cc": "us"}, timeout=15)
    resp.raise_for_status()
    entry = resp.json().get(str(appid))
    if not entry or not entry.get("success"):
        return None
    return entry["data"]


def main(limit: int | None) -> None:
    conn = get_connection()
    appids = pending_appids(conn)
    total_pending = len(appids)
    if limit:
        appids = appids[:limit]
    print(f"genre 보강 대상 {total_pending}개 중 이번 실행에서 {len(appids)}개 처리")

    filled = 0
    for i, appid in enumerate(appids, start=1):
        details = fetch_store_details(appid)
        now = datetime.now(timezone.utc).isoformat()
        if details:
            genres = details.get("genres") or []
            genre_str = ", ".join(g["description"] for g in genres)
            price_overview = details.get("price_overview") or {}
            price = 0 if details.get("is_free") else price_overview.get("final")
            release_date = details.get("release_date", {}).get("date")
            if genre_str:
                filled += 1
            upsert_store_genre_fallback(conn, appid, genre_str, price, release_date, now)
            conn.commit()

        if i % 50 == 0 or i == len(appids):
            print(f"[{i}/{len(appids)}] 채워짐 {filled}개")
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(
        f"이번 실행 완료: {len(appids)}개 처리 (genre 채움 {filled}개), "
        f"남은 대상 {total_pending - len(appids)}개"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(limit=args.limit)
