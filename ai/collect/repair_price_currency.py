"""통화 오염된 price를 Store appdetails(cc=us 고정)로 재조회해 복구한다 (일회성 리페어).

원인: enrich_genre_fallback.py가 Store appdetails를 호출할 때 `cc`(지역)를
지정하지 않아서, 서버 IP 지역에 따라 KRW 등 다른 통화로 응답을 받았다.
price 컬럼은 SteamSpy를 포함해 전부 USD-cent 기준이어야 하는데, KRW 값이
그대로 들어가 예: 41,000원짜리 게임이 $410.00로 보이는 오류가 생겼다
(db.py/enrich_genre_fallback.py는 이미 cc=us로 고정하도록 수정됨, 이
스크립트는 그 버그로 이미 망가진 기존 데이터를 복구하는 용도).

정상가는 대체로 $150 미만이라 그 이상인 행만 재조회 대상으로 삼는다 —
일부 정상적으로 비싼 게임이 섞여도 재조회는 실제 USD 가격으로 정확히
덮어쓰므로 안전하다.
"""

import sys
import time
from datetime import datetime, timezone

import requests

from db import get_connection

STORE_API = "https://store.steampowered.com/api/appdetails"
REQUEST_DELAY_SEC = 1.5
PRICE_THRESHOLD_CENTS = 15000  # $150

sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    conn = get_connection()
    rows = conn.execute(
        "SELECT appid FROM games WHERE sections IS NOT NULL AND price > ?",
        (PRICE_THRESHOLD_CENTS,),
    ).fetchall()
    appids = [row[0] for row in rows]
    print(f"가격 복구 대상 {len(appids)}개")

    fixed = 0
    for i, appid in enumerate(appids, start=1):
        resp = requests.get(STORE_API, params={"appids": appid, "cc": "us"}, timeout=15)
        resp.raise_for_status()
        entry = resp.json().get(str(appid))
        now = datetime.now(timezone.utc).isoformat()

        if entry and entry.get("success"):
            data = entry["data"]
            price_overview = data.get("price_overview") or {}
            price = 0 if data.get("is_free") else price_overview.get("final")
            if price is not None:
                conn.execute(
                    "UPDATE games SET price = ?, last_updated_at = ? WHERE appid = ?",
                    (price, now, appid),
                )
                fixed += 1
        conn.commit()

        if i % 50 == 0 or i == len(appids):
            print(f"[{i}/{len(appids)}] 복구됨 {fixed}개")
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(f"완료: {fixed}/{len(appids)}개 가격 복구 (USD cent 기준)")


if __name__ == "__main__":
    main()
