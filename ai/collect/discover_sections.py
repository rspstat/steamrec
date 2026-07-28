"""웹사이트 섹션(신규 인기 급상승 / 인기 인디 / 최신 출시 반응 좋은 / 멀티플레이)에
맞춰 appid 전수 조사 대신 Steam 스토어 자체 검색 API로 후보를 추려서 games
테이블에 채운다.

`store.steampowered.com/search/results/`는 스토어 웹사이트 자체가 쓰는
검색 엔드포인트로, 한 번의 페이지네이션 호출(count=100)만으로 appid/이름/
출시일/리뷰 비율·수까지 같이 받아온다 (appid별 개별 appdetails 호출이 필요
없음). `category1=998`(게임)로 필터가 걸려 있어 DLC/사운드트랙/툴은 애초에
결과에 안 나온다.

섹션 정의:
  - trending    : filter=popularnew (Steam 홈페이지 '신규 인기 급상승'과 동일)
  - new_release : sort_by=Released_DESC, 리뷰 수 최소 기준으로 '반응 좋은'만
  - indie       : tags=492(Indie), sort_by=Reviews_DESC
  - multiplayer : tags=3859(Multiplayer), sort_by=Reviews_DESC

전체 후보를 합쳐 TOTAL_TARGET(기본 5,000)에서 자르고, 하나의 게임이 여러
섹션에 걸리면 games.sections에 콤마로 합쳐 기록한다 (예: "indie,multiplayer").
장르/태그/오너수 등 나머지 메타데이터는 이 스크립트가 채우지 않는다 —
enrich_appdetails.py가 sections가 채워진 행만 대상으로 이어서 보강한다.
"""

import re
import sys
import time
from datetime import datetime, timezone

import requests

from db import get_connection, upsert_section_candidate

SEARCH_API = "https://store.steampowered.com/search/results/"
PAGE_SIZE = 100
REQUEST_DELAY_SEC = 1.5
TOTAL_TARGET = 5000

SECTIONS = [
    # (섹션 키, 검색 파라미터, 섹션당 목표 개수, 리뷰 수 최소 기준, 리뷰 긍정률 최소 기준)
    ("trending", {"filter": "popularnew"}, 1200, 0, 0),
    ("new_release", {"sort_by": "Released_DESC"}, 1500, 5, 70),
    ("indie", {"tags": 492, "sort_by": "Reviews_DESC"}, 1500, 10, 0),
    ("multiplayer", {"tags": 3859, "sort_by": "Reviews_DESC"}, 1500, 10, 0),
]

ROW_SPLIT_RE = re.compile(r'(?=<a href="https://store\.steampowered\.com/app/)')
APPID_RE = re.compile(r'data-ds-appid="(\d+)"')
NAME_RE = re.compile(r'<span class="title">([^<]*)</span>')
RELEASED_RE = re.compile(
    r'search_released responsive_secondrow">\s*([^<]*?)\s*</div>', re.S
)
REVIEW_RE = re.compile(r"(\d+)% of the ([\d,]+) user reviews")

sys.stdout.reconfigure(encoding="utf-8")


def parse_results_html(html: str) -> list[dict]:
    rows = ROW_SPLIT_RE.split(html)
    parsed = []
    for row in rows:
        appid_match = APPID_RE.search(row)
        if not appid_match:
            continue
        name_match = NAME_RE.search(row)
        released_match = RELEASED_RE.search(row)
        review_match = REVIEW_RE.search(row)
        parsed.append(
            {
                "appid": int(appid_match.group(1)),
                "name": name_match.group(1).strip() if name_match else "",
                "release_date": released_match.group(1).strip()
                if released_match
                else None,
                "review_pct": int(review_match.group(1)) if review_match else None,
                "review_count": int(review_match.group(2).replace(",", ""))
                if review_match
                else None,
            }
        )
    return parsed


def fetch_section(
    section_key: str, params: dict, target: int, min_reviews: int, min_review_pct: int = 0
) -> list[dict]:
    collected: list[dict] = []
    start = 0
    while len(collected) < target:
        query = {
            "query": "",
            "start": start,
            "count": PAGE_SIZE,
            "category1": 998,
            "infinite": 1,
            **params,
        }
        resp = requests.get(SEARCH_API, params=query, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = parse_results_html(data.get("results_html", ""))
        if not items:
            break

        for item in items:
            if min_reviews and (item["review_count"] or 0) < min_reviews:
                continue
            if min_review_pct and (item["review_pct"] or 0) < min_review_pct:
                continue
            collected.append(item)

        start += PAGE_SIZE
        if start >= data.get("total_count", 0):
            break
        time.sleep(REQUEST_DELAY_SEC)

    collected = collected[:target]
    print(f"  [{section_key}] {len(collected)}개 수집 (목표 {target})")
    return collected


def main() -> None:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()

    seen_appids: set[int] = set()
    total_inserted = 0
    for section_key, params, target, min_reviews, min_review_pct in SECTIONS:
        if total_inserted >= TOTAL_TARGET:
            break
        print(f"섹션 수집 중: {section_key}")
        items = fetch_section(section_key, params, target, min_reviews, min_review_pct)
        for item in items:
            if total_inserted >= TOTAL_TARGET and item["appid"] not in seen_appids:
                continue
            upsert_section_candidate(
                conn,
                item["appid"],
                item["name"],
                item["release_date"],
                item["review_pct"],
                item["review_count"],
                section_key,
                now,
            )
            if item["appid"] not in seen_appids:
                seen_appids.add(item["appid"])
                total_inserted += 1
        conn.commit()
        time.sleep(REQUEST_DELAY_SEC)

    conn.close()
    print(f"섹션 후보 수집 완료: 고유 {total_inserted}개 (목표 {TOTAL_TARGET})")


if __name__ == "__main__":
    main()
