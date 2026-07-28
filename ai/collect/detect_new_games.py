"""Job 2 — 매일 실행: IStoreService/GetAppList로 Steam 전체 앱 목록을 받아
로컬 games 테이블에 없는 신규 appid를 찾아 최소 정보로 등록한다.

Valve 자체 스토어 목록이라 발매 당일 신작도 바로 잡힌다는 게 이 잡의 핵심
가치다. STEAM_API_KEY가 필요하다 (.env 참고, IStoreService는 키 없이는
403).

여기서는 appid/이름만 기록한다 — genre/tags/price 등 상세 메타데이터 보강은
Job 3(미구현)에서 이 신규 목록을 받아 Store appdetails로 처리한다. 목록에는
게임이 아닌 DLC/데모/사운드트랙/툴도 섞여 있으므로 type 필터링도 Job 3의 몫.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from db import existing_appids, get_connection, upsert_new_appid

STORE_SERVICE_API = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
MAX_RESULTS_PER_PAGE = 50000

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def fetch_all_apps(api_key: str) -> list[dict]:
    apps: list[dict] = []
    last_appid = None
    while True:
        params = {"key": api_key, "max_results": MAX_RESULTS_PER_PAGE}
        if last_appid is not None:
            params["last_appid"] = last_appid
        resp = requests.get(STORE_SERVICE_API, params=params, timeout=30)
        resp.raise_for_status()
        result = resp.json()["response"]
        apps.extend(result.get("apps", []))
        print(f"  fetched {len(apps)} apps so far...")
        if not result.get("have_more_results"):
            break
        last_appid = result["last_appid"]
    return apps


def main() -> None:
    api_key = os.environ.get("STEAM_API_KEY")
    if not api_key:
        raise SystemExit("STEAM_API_KEY가 .env에 설정되어 있지 않습니다.")

    conn = get_connection()
    known = existing_appids(conn)

    apps = fetch_all_apps(api_key)
    print(f"Steam 전체 목록: {len(apps)}개 (기존 DB에 {len(known)}개 보유)")

    new_apps = [app for app in apps if app["appid"] not in known]
    now = datetime.now(timezone.utc).isoformat()
    for app in new_apps:
        upsert_new_appid(
            conn, app["appid"], app.get("name", ""), app.get("last_modified"), now
        )
    conn.commit()
    conn.close()

    print(f"신규 게임 {len(new_apps)}개 발견 및 등록 완료")
    for app in new_apps[:10]:
        print(f"  - {app['appid']}: {app.get('name')}")


if __name__ == "__main__":
    main()
