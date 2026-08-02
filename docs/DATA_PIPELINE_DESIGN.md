# 데이터 파이프라인

`ai/collect/`, `ai/features/`가 어떻게 게임 데이터를 모으고 채우는지 정리한
문서. 실행 방법·스케줄은 [ARCHITECTURE.md](ARCHITECTURE.md)를, 전체 시스템
구조는 그 문서를 먼저 보는 게 낫다 — 여기는 데이터 수집 로직 자체에 집중.

## 왜 이런 모양이 됐는지 (핵심 결정 3가지)

1. **Steam 전체 카탈로그(15만+)를 무작위로 훑지 않는다.** 처음엔 SteamSpy
   벌크 백필(8만 2천개) + 공식 `IStoreService/GetAppList` 신규 탐지(10만개
   추가)로 "전체"를 모으려 했는데, appid 순서로 장르/타입을 채우면 하루~
   3.5일씩 걸렸다. 그래서 대신 **Steam 스토어 검색 API**로 웹사이트가 실제
   보여줄 섹션(신규 인기 급상승/최신 출시/인디/멀티플레이) 기준으로 필요한
   게임만 직접 큐레이션하는 쪽으로 방향을 바꿨다. appid 순회로 얻은
   원본(`games` 테이블의 `sections IS NULL`인 행들)은 지우지 않고 남겨뒀다
   — 나중에 리뷰 수 기준 상위권을 추가로 편입할 때(카탈로그 확장) 재사용함.

2. **Store appdetails 호출 시 `cc=us`를 항상 고정한다.** 지역을 안 주면
   서버 IP 위치 기준으로 통화가 바뀌어서(KRW 등) 가격이 오염된다 — 실제로
   484개 게임 가격이 통화 단위 차이로 100배 이상 부풀어 있던 걸 나중에
   발견해서 복구한 적 있음.

3. **모든 수집 스크립트는 resumable하게 짠다.** appid/리뷰/이미지 단위로
   "이미 있으면 스킵"하도록 만들어서, 중단 후 재실행하거나 매일 스케줄러가
   다시 돌려도 새로 생긴 것만 처리한다.

## 데이터 소스

| 소스 | 인증 | 용도 |
|---|---|---|
| `store.steampowered.com/search/results/` | 불필요 | 섹션별 게임 후보 발견 (appid/이름/출시일/가격/리뷰비율) |
| SteamSpy `appdetails` | 불필요 | genre/tags(커뮤니티 투표 태그)/owners/price 보강. 1req/sec 권장 |
| `store.steampowered.com/api/appdetails` | 불필요 (단, `cc=us` 고정) | type(게임/DLC 구분), release_date, genre 폴백 |
| `store.steampowered.com/appreviews/{appid}` | 불필요 | 리뷰 본문 (NLP용) |
| Steam CDN 헤더 이미지 | 불필요 | CV 임베딩용 이미지. 예측 URL 실패 시 Store appdetails로 폴백 |
| `IStoreService/GetAppList/v1` | STEAM_API_KEY | Steam 공식 전체 appid 목록 (역사적으로 카탈로그 확장 후보 발견에 사용) |
| `IPlayerService/GetOwnedGames` | STEAM_API_KEY + steamid | 로그인 유저의 보유 게임 (CF용, `backend/app/api/auth.py`에서 호출) |

## 현재 파이프라인 (`ai/scheduler.py`가 매일 재실행)

```
discover_sections.py  → enrich_selected.py → enrich_genre_fallback.py
        │                                              │
        └──────────────────┬───────────────────────────┘
                            ▼
              fetch_reviews.py, cv_embed.py (features/)
                            ▼
              nlp_embed.py (features/)
                            ▼
              hybrid.py, collaborative.py (models/)
```

1. **`discover_sections.py`** — 스토어 검색 API로 4개 섹션(`trending`,
   `new_release`, `indie`, `multiplayer`)의 후보를 받아 `games.sections`에
   기록. 이미 알던 게임은 섹션만 병합, 새 게임은 appid/이름/출시일/리뷰
   비율로 새 행 생성. `category1=998`(게임) 필터가 걸려 있어 DLC/사운드
   트랙은 애초에 안 섞인다.
2. **`enrich_selected.py`** — `sections`는 있는데 `genre`가 없는 행에
   SteamSpy `appdetails`로 genre/tags/price/owners 보강.
3. **`enrich_genre_fallback.py`** — SteamSpy가 아직 못 따라잡은 갓 나온
   게임(플레이 데이터가 없어 SteamSpy 응답이 비어있음)은 Store
   appdetails로 대체 보강.
4. **`fetch_reviews.py`** — 게임당 리뷰 최대 20개 (Steam이 "도움순"으로
   골라줌, `filter=all&review_type=all`이라 긍정/부정이 알아서 섞여 들어옴).
5. **`cv_embed.py`** (`ai/features/`) — 헤더 이미지를 CLIP으로 임베딩.
6. **`nlp_embed.py`** (`ai/features/`) — 리뷰 텍스트를 다국어 Sentence-BERT로
   임베딩, 게임 단위로 평균.
7. **`hybrid.py`**, **`collaborative.py`** (`ai/models/`) — 콘텐츠
   유사도/CF 재계산. 자세한 내용은 ARCHITECTURE.md의 "추천 모델" 참고.

## 카탈로그 확장 (`expand_catalog.py`, 1회 실행 완료)

섹션 큐레이션(4,463개) 이후 커버리지를 넓히려고, appid 순회로 모아뒀던
원본 데이터(SteamSpy 벌크 8만 2천개) 중 **리뷰 수(positive+negative) 상위
10,000개**만 골라 type 분류 + genre 보강을 해서 `sections='catalog'`로
편입했다. 전체 17만+ 백로그를 다 처리하면 3~4일 걸리는데(Store API
1req/sec 제약), 리뷰 상위로 자르면 실제 서비스 가치가 높은 게임(Skyrim,
Portal 2, Witcher 3급)부터 확보되면서 몇 시간 내로 끝난다. 결과: 6,024개
추가로 게임(type='game')임을 확인, 나머지는 DLC/demo/unknown으로 제외.

10만+ 전체로 더 넓히고 싶으면 이 스크립트를 다시 돌리되(TARGET_COUNT를
올리고), Store API 호출량이 선형으로 늘어난다는 점을 감안할 것.

## 운영 중 실제로 겪은 문제들 (재발 방지 메모)

- **SteamSpy `all` 벌크에는 genre/tags가 없다** — `price`/`owners`/리뷰
  수만 있음. genre/tags는 개별 `appdetails` 호출로만 얻을 수 있다.
- **SteamSpy `all`은 전체 카탈로그가 아니라 자체 상위 서브셋(~8만 6천개)만
  노출한다** — appid 정렬도 아니고, Dota 2처럼 초대형 게임도 빠져 있었다.
  공식 목록(`IStoreService/GetAppList`)이 진짜 마스터 목록.
- **Steam 헤더 이미지 CDN 경로가 두 가지다** — 오래된 게임은
  `cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg`로 바로
  받아지지만, 최근 게임(대략 appid 380만 이상)은 해시가 포함된 새 경로라
  예측 불가능 — Store appdetails의 `header_image` 필드로 폴백해야 한다.
  (`cv_embed.py`에 이미 반영됨)
- **SteamSpy/Store 둘 다 가끔 빈 응답이나 502/504를 준다** — 모든 수집
  스크립트에 재시도(최대 3회, 지수 백오프 아님, 고정 backoff)가 들어있다.
  없으면 몇 시간짜리 백그라운드 작업이 예외 하나로 통째로 죽는다.
- **DB에 인덱스 없이 `NOT EXISTS` 서브쿼리를 쓰면 테이블이 커질수록
  느려진다** — `reviews(appid)`, `games(sections)`에 인덱스를 걸어뒀다
  (`ai/collect/db.py`).

## 알려진 한계

- 리뷰는 게임당 최대 20개뿐이라 감성 분석의 표본이 작다.
- NLP 리뷰 임베딩은 Steam 리뷰 특유의 밈/유머 톤 때문에 "무슨 게임인지"보다
  "리뷰 쓰는 말투"를 더 강하게 반영하는 경향이 있다 — 하이브리드에서 가중치를
  낮게 준 이유(ARCHITECTURE.md 참고).
- 가격은 `cc=us` 고정이라 전부 USD 기준. 한국 원화 등 지역화는 아직 없음.
- CF는 로그인 유저 수에 전적으로 의존 — 유저가 적으면 추천이 사실상
  무의미하다 (콜드스타트).
