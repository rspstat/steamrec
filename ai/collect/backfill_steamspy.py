"""Job 1 — SteamSpy 'all' 벌크 페이지를 순회해 games 테이블을 초기 백필한다.

개별 게임을 하나씩 조회하지 않고 페이지(최대 1000개) 단위로 받기 때문에
카탈로그 규모와 무관하게 몇 분 내에 끝난다.

확인된 특이사항: SteamSpy 'all'은 마지막 페이지 근처(테스트 시점 기준 86페이지,
약 86,500개)를 넘어가면 빈 응답 대신 500 에러/"Too many connections" 텍스트를
반환한다. 이는 전체 Steam 카탈로그(15만+)를 다 커버하지 못한다는 뜻이라
SteamSpy 커버리지 밖의 롱테일 게임은 Job 2(IStoreService 전체 목록)에서
appid만 먼저 확보되고, 상세 메타데이터는 이후 보강 단계에서 채워야 한다.

주의: 'all' 벌크 응답에는 price/owners/리뷰 수/ccu만 들어있고 genre/tags는
없다 (개별 appdetails 호출에만 존재). genre/tags/type/release_date 보강은
이후 별도 단계(Job 3, 미구현)에서 appid별로 채운다.
"""

import sys
import time
from datetime import datetime, timezone

import requests

from db import DB_PATH, get_connection, upsert_steamspy_game

sys.stdout.reconfigure(encoding="utf-8")

STEAMSPY_API = "https://steamspy.com/api.php"
REQUEST_DELAY_SEC = 1
MAX_RETRIES_PER_PAGE = 2
RETRY_DELAY_SEC = 5


def fetch_page(page: int) -> dict | None:
    for attempt in range(1, MAX_RETRIES_PER_PAGE + 1):
        resp = requests.get(
            STEAMSPY_API, params={"request": "all", "page": page}, timeout=15
        )
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code == 200 and isinstance(data, dict) and data:
            return data
        print(
            f"  page {page} attempt {attempt}/{MAX_RETRIES_PER_PAGE} failed "
            f"(status={resp.status_code}, body={resp.text[:60]!r})"
        )
        time.sleep(RETRY_DELAY_SEC)
    return None


def main() -> None:
    conn = get_connection()
    total = 0
    page = 0
    while True:
        data = fetch_page(page)
        if data is None:
            print(f"page {page}: no more data (SteamSpy 커버리지 끝) — 백필 종료")
            break

        now = datetime.now(timezone.utc).isoformat()
        for game in data.values():
            upsert_steamspy_game(conn, game, now)
        conn.commit()
        total += len(data)
        print(f"page {page}: +{len(data)} (total {total})")

        if len(data) < 1000:
            print("마지막 페이지(1000개 미만) 확인 — 백필 종료")
            break

        page += 1
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(f"backfill complete: {total} games saved to {DB_PATH}")


if __name__ == "__main__":
    main()
