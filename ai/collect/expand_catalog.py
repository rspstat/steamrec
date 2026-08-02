"""4,463개 큐레이션 이후, 리뷰 수 기준 상위 게임을 추가로 카탈로그에 편입한다.

전체 174,131개 미분류 백로그를 다 처리하는 대신(3~4일 소요), 이미 Job 1
(SteamSpy 백필)에서 리뷰 데이터가 있는 80,244개 중 (positive+negative)
상위 TARGET_COUNT개만 골라 확장한다 — 리뷰 수가 실제 관심도의 대리 지표.

각 후보에 대해:
  1) Store appdetails(cc=us)로 type 확인 → game이 아니면 is_active=0 처리
  2) genre가 없으면(실측: 상위 1만개 중 99%가 없음) SteamSpy appdetails로 보강
  3) sections='catalog'로 표시해 기존 파이프라인(fetch_reviews.py,
     cv_embed.py, hybrid.py)이 `sections IS NOT NULL` 필터로 그대로
     집어먹게 함 — 새 스크립트 없이 재사용.

resumable: sections가 이미 채워졌거나 type이 채워진 행은 대상에서 빠진다.
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import requests

from db import get_connection, upsert_steamspy_game

STORE_API = "https://store.steampowered.com/api/appdetails"
STEAMSPY_API = "https://steamspy.com/api.php"
REQUEST_DELAY_SEC = 1.5
TARGET_COUNT = 10000

sys.stdout.reconfigure(encoding="utf-8")


def already_processed_count(conn) -> int:
    """이 스크립트가 이미 처리한 행 수 (재시작 시 목표치 TARGET_COUNT를 넘지
    않도록 남은 개수를 다시 계산하는 데 씀). type이 채워졌고 positive/negative가
    있는 행은 이 스크립트만 건드리는 조합이라 안전하게 구분된다 (classify_new_games.py는
    owners_estimate가 NULL인 행만 다뤄서 겹치지 않음)."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM games
        WHERE type IS NOT NULL
          AND (positive IS NOT NULL OR negative IS NOT NULL)
        """
    ).fetchone()
    return row[0]


def select_candidates(conn) -> list[int]:
    remaining = max(TARGET_COUNT - already_processed_count(conn), 0)
    rows = conn.execute(
        """
        SELECT appid FROM games
        WHERE sections IS NULL AND type IS NULL
          AND (positive IS NOT NULL OR negative IS NOT NULL)
        ORDER BY (COALESCE(positive, 0) + COALESCE(negative, 0)) DESC
        LIMIT ?
        """,
        (remaining,),
    ).fetchall()
    return [row[0] for row in rows]


def fetch_store_details(appid: int, max_retries: int = 3) -> dict | None:
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(STORE_API, params={"appids": appid, "cc": "us"}, timeout=15)
            resp.raise_for_status()
            entry = resp.json().get(str(appid))
            if not entry or not entry.get("success"):
                return None
            return entry["data"]
        except (requests.RequestException, ValueError) as exc:
            print(f"  appid={appid} store 시도 {attempt}/{max_retries} 실패: {exc}")
            if attempt == max_retries:
                return None
            time.sleep(REQUEST_DELAY_SEC * 3)
    return None


def fetch_steamspy_details(appid: int, max_retries: int = 3) -> dict | None:
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                STEAMSPY_API, params={"request": "appdetails", "appid": appid}, timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"  appid={appid} steamspy 시도 {attempt}/{max_retries} 실패: {exc}")
            if attempt == max_retries:
                return None
            time.sleep(REQUEST_DELAY_SEC * 3)
    return None


def main(limit: int | None) -> None:
    conn = get_connection()
    candidates = select_candidates(conn)
    if limit:
        candidates = candidates[:limit]
    print(f"확장 후보 {len(candidates)}개 처리 시작")

    type_counts: dict[str, int] = {}
    for i, appid in enumerate(candidates, start=1):
        now = datetime.now(timezone.utc).isoformat()
        details = fetch_store_details(appid)
        app_type = details.get("type", "unknown") if details else "unknown"
        type_counts[app_type] = type_counts.get(app_type, 0) + 1

        if app_type == "game":
            row = conn.execute(
                "SELECT genre FROM games WHERE appid = ?", (appid,)
            ).fetchone()
            if not row or not row[0]:
                time.sleep(REQUEST_DELAY_SEC)
                spy_details = fetch_steamspy_details(appid)
                if spy_details:
                    upsert_steamspy_game(conn, spy_details, now)

            release_date = (details.get("release_date") or {}).get("date")
            conn.execute(
                """
                UPDATE games
                SET sections = 'catalog', type = 'game',
                    release_date = COALESCE(release_date, ?),
                    is_active = 1, last_updated_at = ?
                WHERE appid = ?
                """,
                (release_date, now, appid),
            )
        else:
            conn.execute(
                "UPDATE games SET type = ?, is_active = 0, last_updated_at = ? WHERE appid = ?",
                (app_type, now, appid),
            )
        conn.commit()

        if i % 100 == 0 or i == len(candidates):
            print(f"[{i}/{len(candidates)}] {type_counts}")
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(f"완료: {type_counts}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(limit=args.limit)
