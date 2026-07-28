# 전체 카탈로그 수집 + 일일 자동 업데이트 설계

## 배경

기존 `ai/collect/fetch_games.py`는 SteamSpy `top100in2weeks`로 인기 게임 100개만 가져온다.
웹사이트로 서비스할 것을 고려하면 (1) Steam 전체 게임을 커버하고 (2) 신작이 나올 때마다
자동으로 반영되어야 한다. 이 문서는 그 확장 설계를 정리한다.

## 사전 검증 결과 (중요)

설계 전에 실제 API를 호출해서 확인한 사실:

| 엔드포인트 | 결과 |
|---|---|
| `ISteamApps/GetAppList/v2` (공식, 키 불필요로 알려짐) | **404 — 더 이상 존재하지 않음** (`Method 'GetAppList' not found in interface 'ISteamApps'`) |
| `IStoreService/GetAppList/v1` (공식, 전체 앱 목록의 후속 엔드포인트) | **403 — API 키 필요** (`Access is denied... verify your key= parameter`) |
| SteamSpy `request=all&page=N` | **정상 동작, 키 불필요**, 페이지당 1000개 (page=0,1,2… 순회) |
| SteamSpy `request=appdetails&appid=X` | 정상 동작, `genre`/`tags` 포함 |

즉 **"Steam 전체 앱 목록"을 공식 API로 얻으려면 이제 STEAM_API_KEY가 필수**다.
액션 아이템 1번(API 키 발급)을 이 작업 전에 먼저 해야 한다. 키 없이 진행하려면
SteamSpy의 `all` 벌크 목록으로 대체할 수 있지만, SteamSpy는 실제 플레이어 데이터가
쌓여야 반영되는 3rd-party 미러라 발매 당일 신작은 며칠 늦게 잡힐 수 있다.

## 데이터 소스 정리

| 소스 | 인증 | 용도 | 제한 |
|---|---|---|---|
| SteamSpy `all` (페이지네이션) | 불필요 | 전체 게임 벌크 메타데이터(장르/태그/오너수/가격/리뷰) 초기 백필 및 주기 갱신 | ~1 req/sec 권장 |
| `IStoreService/GetAppList/v1` | STEAM_API_KEY | 매일 "오늘 새로 생긴 appid" 탐지용 마스터 목록 (Valve 자체 목록이라 신작 반영이 가장 빠름) | 키만 있으면 대용량 호출 가능 |
| Store `appdetails` (store.steampowered.com/api/appdetails) | 불필요 | 신규 appid의 `type`(game/dlc/demo 판별), 출시일, 스크린샷, 짧은 설명 보강 | 커뮤니티 보고 기준 약 200 req/5min — 신규분만 조회하면 충분 |
| `IPlayerService/GetOwnedGames` | STEAM_API_KEY + steamid | (나중 단계) CF용 유저-게임 상호작용 데이터 | 유저별 호출, 별도 트랙 |

## 저장 스키마 (PostgreSQL, `games` 테이블)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `appid` | int, PK | Steam appid |
| `name` | text | 게임명 |
| `type` | text | game/dlc/demo — Store appdetails 기반, non-game 필터링용 |
| `genre` | text | SteamSpy genre (콤마 구분) |
| `tags` | jsonb | SteamSpy tags (태그명→투표수) |
| `price` | int | 현재가 (cent 단위) |
| `positive` / `negative` | int | 리뷰 수 |
| `owners_estimate` | text | SteamSpy owners 범위 |
| `release_date` | date | Store appdetails 기반 |
| `first_seen_at` | timestamp | DB에 처음 들어온 시각 (신작 감지 시점) |
| `last_updated_at` | timestamp | 마지막 메타데이터 갱신 시각 |
| `is_active` | bool | 목록에서 사라진(삭제/비공개) 게임 표시 |

`appid` 기준 upsert. `type != 'game'`인 행은 CF/추천 대상에서 제외하되, 원본은 남겨서
나중에 DLC 연관 추천 등에 재활용 가능하게 한다.

## 파이프라인 구성

### Job 1 — 초기 백필 (1회성) · 구현/실행 완료

`ai/collect/backfill_steamspy.py`. 실제 실행 결과:
- SteamSpy `all`은 page=0부터 순회하다 마지막 페이지(1000개 미만, 테스트 시점 기준
  86페이지)에서 자연 종료 — 그 이후 페이지는 빈 응답이 아니라 500/"Too many
  connections" 에러로 실패하므로, 종료 조건은 "빈 응답"이 아니라 "요청 실패
  또는 1000개 미만 페이지"로 구현했다.
- 총 **82,523개** appid를 `ai/data/games.db`(SQLite)에 upsert 완료.
- **중요 정정**: `all` 벌크 응답에는 `genre`/`tags`가 없다 — `price`, `owners`,
  `positive`/`negative`, `ccu`만 들어있다. `genre`/`tags`는 개별
  `appdetails` 호출(1req/sec)에만 존재한다. 82,523개를 전부 개별 조회하면
  약 23시간이 걸리므로, 전체 게임의 장르/태그 보강은 Job 1의 범위 밖으로
  분리하고 별도 결정이 필요하다 (아래 "남은 전제 조건" 참고).
- 이후 신규 appid에 한해서만 Store `appdetails`로 `type`/`release_date`/스크린샷 보강 (Job 3, 미구현)

### Job 2 — 일일 신규 게임 탐지 (Celery beat, 매일 1회) · 구현/실행 완료

`ai/collect/detect_new_games.py`. 실제 실행 결과:
- `IStoreService/GetAppList/v1`(50,000개씩 페이지네이션, `last_appid`로 이어받기)로
  전체 **176,250개** appid+name 확보.
- Job 1로 확보한 82,523개와 diff한 결과 **100,061개가 신규**로 잡혔다.
- **중요 발견**: SteamSpy `all` 벌크는 appid 정렬이 아니라 자체 순위/방식으로
  상위 ~86.5k개만 노출하는 것으로 보인다 — 심지어 **Dota 2(appid 570)조차
  SteamSpy 벌크 목록에 없었다**. 즉 SteamSpy `all`은 "전체 카탈로그"가 아니라
  "SteamSpy가 추적하는 서브셋"이라 이 Job 2(공식 Valve 목록)가 진짜 마스터
  목록 역할을 해야 하는 게 맞다는 걸 실측으로 확인한 셈.
- 다만 Job 2가 새로 등록한 10만 개는 appid/name만 있고 owners/price/genre가
  없다 (`enrich_appdetails.py`의 활성 필터가 `owners_estimate IS NOT NULL`을
  요구하므로 자동으로 보강 대상에서 제외됨). 그 결과 Dota 2처럼 SteamSpy
  벌크에는 없지만 실제로는 매우 활성인 게임이 현재 보강 파이프라인에서
  누락된다 — Job 3(타입 필터링 + 상세 보강)에서 반드시 다뤄야 할 문제.
- 10만 개 중 다수는 DLC/데모/사운드트랙/툴일 것으로 예상되나 아직 `type` 필터링
  전이라 구분되지 않은 상태 (Job 3 범위).

### Job 3 — 신규 게임 상세 보강 (큐 워커, rate-limit 적용) · 구현/1차 검증 완료

`ai/collect/classify_new_games.py`. `games` 테이블에 `type`/`release_date` 컬럼을
추가(`db.py` 마이그레이션)하고, 대상은 `type IS NULL AND owners_estimate IS NULL`
(Job 2가 발견했지만 아직 아무것도 모르는 행)로 한정.

처리 순서: Store `appdetails`로 `type` 확인 → `game`이 아니면 `is_active=0`
표시 후 SteamSpy 호출 생략 (호출 절약) → `game`이면 SteamSpy `appdetails`로
genre/tags/price/owners까지 보강.

30개 샘플 실행 결과: game 26 / unknown(비공개·삭제 등으로 조회 실패) 4,
DLC/demo/music은 이번 샘플(appid 오름차순 = Steam 초기 카탈로그)에는 없었음 —
낮은 appid 구간은 DLC/사운드트랙 관행이 자리잡기 전이라 최근 발매작 위주
appid 구간에서는 비율이 다를 것으로 예상됨.

**Dota 2(appid 570) 문제 해결 확인**: 이번 실행으로 type=`game`,
genre=`Action, Strategy, Free To Play`, price=0으로 정상 보강됨 — Job 2에서
발견됐지만 SteamSpy 벌크에 없어 누락되던 문제가 해소됨.

**남은 물량**: Job 2 백로그 100,061개 중 30개만 처리, 99,969개 남음. 요청당
Store 1req/sec + game이면 SteamSpy 1req/sec 추가라서 전체를 다 돌리면
게임 비율에 따라 대략 하루~하루 반 정도 소요 예상 (일일 운영 시에는 그날
신규 발생분 수십~수백 개만 처리하면 되므로 이 정도로 크지 않음 — 지금의
99,969개는 최초 1회성 백로그일 뿐).

## MVP 범위 재조정 — 섹션 기반 큐레이션 (전체 카탈로그 대신 5,000개)

Job 3의 대규모 백로그(99,969개, appid 순서로 처리 시 약 3.5일)를 실측해보니
너무 느렸고, 애초에 웹사이트가 보여줄 섹션(신규 인기 급상승 / 인기 인디 게임 /
최신 출시 반응 좋은 게임 / 멀티플레이 게임)에 맞춰 게임을 추리는 게 목적에도
더 맞았다. appid를 무작위로 훑는 대신, **Steam 스토어 자체 검색 API**
(`store.steampowered.com/search/results/`, 스토어 웹페이지가 쓰는 바로 그
엔드포인트)로 섹션별 후보를 직접 받아왔다.

이 검색 API는 페이지네이션(count=100) 한 번으로 appid/이름/출시일/가격/
리뷰 비율·리뷰 수까지 같이 내려주기 때문에, appid별 개별 appdetails 호출이
필요 없다. `category1=998`(게임) 필터가 이미 걸려 있어 DLC/사운드트랙/툴도
애초에 안 섞인다.

### 섹션 정의 (`ai/collect/discover_sections.py`)

| 섹션 | 검색 파라미터 | 목표 개수 | 최소 리뷰 수 | 최소 긍정률 |
|---|---|---|---|---|
| `trending` (신규 인기 급상승) | `filter=popularnew` | 1200 | - | - |
| `new_release` (최신 출시, 반응 좋은) | `sort_by=Released_DESC` | 1500 | 5 | 70% |
| `indie` (인기 인디) | `tags=492, sort_by=Reviews_DESC` | 1500 | 10 | - |
| `multiplayer` (멀티플레이) | `tags=3859, sort_by=Reviews_DESC` | 1500 | 10 | - |

`trending`은 Steam이 실제로 큐레이션한 "New & Trending" 목록이라 전체 풀이
356개뿐 (그 이상 없음). 한 게임이 여러 섹션에 걸치면 `games.sections`에
콤마로 합쳐 기록 (예: `"indie,multiplayer"`).

**실행 결과**: 고유 **4,463개** 후보 확보 (trending 356 / new_release 1,373 /
indie 1,274 / multiplayer 1,248, 중복 겹침 포함). Dota 2(570)·CS2(730)·
TF2(440)처럼 이전 Job 1(SteamSpy 벌크)에서 놓쳤던 초대형 게임들도 `trending`
섹션으로 정상 확보됨 — appid 무작위 크롤보다 목적에 맞고 훨씬 빠름(전체
수집이 몇 분 내 완료).

이 4,463개에는 아직 genre/tags/price/owners가 없어서 (검색 API가 안 주는
정보), `ai/collect/enrich_selected.py`로 SteamSpy `appdetails`를 appid당
1회만 호출해 채운다 — 4,463개 규모라 1req/sec로도 완료까지 약 1~1.5시간.
`genre`가 이미 채워진 행은 자동으로 제외되므로 중단 후 재실행해도 이어서
처리된다.

**전체 카탈로그(10만+)로의 확장은 나중으로 미룸.** Job 1~3(SteamSpy 전체
백필 82,523개 + Job 2 신규 탐지 100,061개)에서 모은 원본 데이터는 삭제하지
않고 `games` 테이블에 그대로 남겨뒀다 — `sections`가 없는 행들이라 지금의
5천 개 MVP에는 안 쓰이지만, 나중에 커버리지를 넓힐 때 재활용 가능하다.

### Job 4 — 기존 게임 메타데이터 주기 갱신 (Celery beat, 매일 또는 주 1회 야간)
- SteamSpy `all` 페이지 전체를 다시 순회해 가격/리뷰/오너수 갱신 (벌크라 저비용)
- Store `appdetails`로 개별 재조회는 하지 않음 (변경 빈도 낮은 필드는 스킵)

### Job 5 — 인기 게임 우선 갱신 (선택, 추후)
- ccu/owners 상위 N개는 할인/가격 변동 반영을 위해 더 자주(예: 6시간 주기) 갱신

## 스케줄링 요약

| Job | 주기 | 트리거 |
|---|---|---|
| Job 1 (백필) | 1회 | 수동 실행 |
| Job 2 (신작 탐지) | 매일 | Celery beat cron |
| Job 3 (신규 보강) | Job 2 직후 | Celery task chain |
| Job 4 (전체 갱신) | 매일 야간 또는 주 1회 | Celery beat cron |
| Job 5 (인기작 우선) | 6시간 | Celery beat cron (선택) |

## 남은 전제 조건

1. ~~STEAM_API_KEY 발급~~ — 완료. Job 2가 정상적으로 키를 사용해 호출됨을 확인.
2. Store `appdetails`의 비공식 rate limit은 커뮤니티 경험치이며 Valve가 공식 문서화하지
   않음 — 운영 중 429 응답 시 백오프 로직 필요.
3. CF 모델용 유저-게임 상호작용 데이터(`GetOwnedGames`)는 이 파이프라인과 별도 트랙으로,
   유저가 Steam 로그인(OpenID)한 시점에 개별 수집하는 방식이 될 것 (본 문서 범위 밖).
4. **미해결 — genre/tags 보강 범위 결정 필요**: 82,523개 전체를 1req/sec로 개별
   `appdetails` 조회하면 약 23시간. 선택지:
   - (a) 전체를 느린 백그라운드 잡으로 며칠에 걸쳐 채움 (완전하지만 느림)
   - (b) `owners`가 0이거나 극히 낮은 "죽은" 게임은 제외하고 활성 게임만 우선 보강
     (CF/추천에 실제로 의미 있는 대상만 추리므로 현실적)
   - (c) 추천 대상 게임 집합을 처음부터 작게(예: 리뷰 수 상위 N만) 잡고 나머지는
     "발견은 됐지만 미보강" 상태로 남겨둠
   → 사용자와 상의 후 결정, 아직 미구현.
