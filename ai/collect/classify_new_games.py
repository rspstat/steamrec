"""Job 3 — Job 2가 발견한 신규 appid를 게임/비게임으로 분류하고, 게임이면 보강한다.

대상은 owners_estimate가 NULL인 행(Job 2가 appid/이름만으로 등록한 행)으로
한정한다. Job 1(SteamSpy 벌크)에서 이미 받은 행은 최소한의 메타데이터가 있어
type 분류 우선순위가 낮다.

처리 순서:
  1) Store `appdetails`로 type(game/dlc/demo/music/...)과 release_date 확인
  2) type이 'game'이 아니면 is_active=0으로 표시하고 종료 (SteamSpy 호출 절약)
  3) type이 'game'이면 SteamSpy `appdetails`로 genre/tags/price/owners까지 보강

Store appdetails의 비공식 rate limit은 문서화되어 있지 않아 보수적으로
1req/sec로 처리한다. type이 채워진 행은 대상에서 자동으로 빠지므로,
--limit으로 나눠 실행해도 이어서 처리된다 (resumable).
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import requests

from db import get_connection, upsert_steamspy_game, upsert_store_classification

STORE_API = "https://store.steampowered.com/api/appdetails"
STEAMSPY_API = "https://steamspy.com/api.php"
REQUEST_DELAY_SEC = 1

sys.stdout.reconfigure(encoding="utf-8")


def pending_appids(conn) -> list[int]:
    rows = conn.execute(
        """
        SELECT appid FROM games
        WHERE type IS NULL AND owners_estimate IS NULL
        ORDER BY appid
        """
    ).fetchall()
    return [row[0] for row in rows]


def fetch_store_details(appid: int) -> dict | None:
    resp = requests.get(STORE_API, params={"appids": appid, "cc": "us"}, timeout=15)
    resp.raise_for_status()
    entry = resp.json().get(str(appid))
    if not entry or not entry.get("success"):
        return None
    return entry["data"]


def fetch_steamspy_details(appid: int) -> dict:
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
    print(f"남은 분류 대상 {total_pending}개 중 이번 실행에서 {len(appids)}개 처리")

    type_counts: dict[str, int] = {}
    for i, appid in enumerate(appids, start=1):
        now = datetime.now(timezone.utc).isoformat()
        details = fetch_store_details(appid)

        if details is None:
            upsert_store_classification(conn, appid, "unknown", None, now)
            type_counts["unknown"] = type_counts.get("unknown", 0) + 1
        else:
            app_type = details.get("type", "unknown")
            release_date = details.get("release_date", {}).get("date")
            upsert_store_classification(conn, appid, app_type, release_date, now)
            type_counts[app_type] = type_counts.get(app_type, 0) + 1

            if app_type == "game":
                time.sleep(REQUEST_DELAY_SEC)
                spy_details = fetch_steamspy_details(appid)
                upsert_steamspy_game(conn, spy_details, now)

        conn.commit()
        if i % 10 == 0 or i == len(appids):
            print(f"[{i}/{len(appids)}] 누적 분류: {type_counts}")
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(f"이번 실행 완료: {len(appids)}개 처리, 남은 대상 {total_pending - len(appids)}개")
    print(f"타입 분포: {type_counts}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(limit=args.limit)
