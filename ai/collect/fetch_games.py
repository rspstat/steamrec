"""SteamSpy에서 인기 게임 100개의 메타데이터(장르, 태그, 가격, 리뷰)를 수집해 ai/data/raw/에 저장한다.

공식 Steam Web API(IPlayerService 등)는 API 키와 대상 steamid가 있어야 하는
유저별 라이브러리/플레이타임 데이터에 필요하다. 여기서는 키 없이 쓸 수 있는
SteamSpy 공개 API로 게임 자체의 메타데이터만 먼저 모은다.
"""

import json
import time
from pathlib import Path

import requests

STEAMSPY_API = "https://steamspy.com/api.php"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "raw" / "games_raw.json"
TOP_N = 100
REQUEST_DELAY_SEC = 1  # SteamSpy 요청 제한(1req/sec) 준수


def fetch_top_games(limit: int = TOP_N) -> list[dict]:
    resp = requests.get(STEAMSPY_API, params={"request": "top100in2weeks"}, timeout=10)
    resp.raise_for_status()
    return list(resp.json().values())[:limit]


def fetch_game_details(appid: int) -> dict:
    resp = requests.get(
        STEAMSPY_API, params={"request": "appdetails", "appid": appid}, timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    games = fetch_top_games()
    enriched = []
    for i, game in enumerate(games, start=1):
        appid = game["appid"]
        print(f"[{i}/{len(games)}] appid={appid} ({game.get('name')})")
        enriched.append(fetch_game_details(appid))
        time.sleep(REQUEST_DELAY_SEC)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"saved {len(enriched)} games to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
