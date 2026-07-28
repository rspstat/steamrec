"""name이 NULL인 섹션 후보를 Store appdetails로 복구한다 (일회성 리페어).

원인: upsert_steamspy_game이 SteamSpy 응답에 name이 없는 경우 기존 이름을
NULL로 덮어쓰는 버그가 있었다 (db.py에서 COALESCE로 이미 수정됨). 이
스크립트는 그 버그로 이미 망가진 기존 274개 행을 복구하는 용도.

Store에서도 조회가 안 되면(완전히 삭제된 앱) is_active=0으로 표시해 목록
노출에서 제외한다.
"""

import sys
import time
from datetime import datetime, timezone

import requests

from db import get_connection

STORE_API = "https://store.steampowered.com/api/appdetails"
REQUEST_DELAY_SEC = 1.5

sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    conn = get_connection()
    rows = conn.execute(
        "SELECT appid FROM games WHERE sections IS NOT NULL AND name IS NULL"
    ).fetchall()
    appids = [row[0] for row in rows]
    print(f"이름 복구 대상 {len(appids)}개")

    fixed = 0
    for i, appid in enumerate(appids, start=1):
        resp = requests.get(STORE_API, params={"appids": appid, "cc": "us"}, timeout=15)
        resp.raise_for_status()
        entry = resp.json().get(str(appid))
        now = datetime.now(timezone.utc).isoformat()

        if entry and entry.get("success") and entry["data"].get("name"):
            conn.execute(
                "UPDATE games SET name = ?, last_updated_at = ? WHERE appid = ?",
                (entry["data"]["name"], now, appid),
            )
            fixed += 1
        else:
            conn.execute(
                "UPDATE games SET is_active = 0, last_updated_at = ? WHERE appid = ?",
                (now, appid),
            )
        conn.commit()

        if i % 50 == 0 or i == len(appids):
            print(f"[{i}/{len(appids)}] 복구됨 {fixed}개")
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(f"완료: {fixed}/{len(appids)}개 이름 복구, 나머지는 is_active=0 처리")


if __name__ == "__main__":
    main()
