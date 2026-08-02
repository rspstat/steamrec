# 아키텍처

steamrec 전체 시스템이 지금 실제로 어떻게 짜여 있는지 정리한 문서.
데이터 수집 로직 자체는 [DATA_PIPELINE_DESIGN.md](DATA_PIPELINE_DESIGN.md)를,
초기 기획 의도는 [PROJECT_PLAN.md](PROJECT_PLAN.md)를 참고.

## 폴더 구조와 분리 원칙

```
steamrec/
├── ai/                 # 데이터 수집 + 모델 학습 (연구 영역, 무거운 의존성)
│   ├── collect/         # 수집 스크립트 + games.db 공용 접근 헬퍼(db.py)
│   ├── features/         # NLP/CV 임베딩 추출
│   ├── models/           # 콘텐츠 유사도 / CF / 하이브리드 결합
│   ├── data/games.db      # 메인 SQLite DB (gitignore 대상)
│   ├── artifacts/          # 학습 산출물: *_embeddings.npz (gitignore 대상)
│   └── scheduler.py         # 매일 파이프라인 재실행 (APScheduler)
├── backend/             # FastAPI 서빙 (경량, ai의 산출물을 읽기만 함)
│   └── app/
│       ├── api/           # games.py, auth.py, users.py
│       ├── core/           # db.py(games.db 읽기전용), user_db.py(자체 DB), config.py
│       └── schemas/         # Pydantic 모델
├── frontend/            # React + Vite
├── infra/               # docker-compose.yml, nginx
└── docs/
```

**읽기/쓰기 방향이 핵심 원칙이다**: `ai/`는 `ai/data/games.db`와
`ai/artifacts/`에만 쓴다. `backend/`는 `games.db`를 **읽기 전용**으로
열고(`sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)`), 자기 자신의
운영 데이터(유저/세션/보유 게임)는 별도 `backend/data/app.db`에 직접
쓴다. 그래서 `ai/models/collaborative.py`처럼 CF 학습에 유저 데이터가
필요한 경우도 `app.db`를 읽기 전용으로만 참조하고, 결과는 다시
`games.db`에 쓴다 — backend가 scikit-learn/PyTorch 같은 무거운 학습
의존성을 절대 필요로 하지 않게 하기 위함.

## 데이터베이스 두 개

### `ai/data/games.db` — 게임 데이터 (ai가 쓰고, backend가 읽음)

| 테이블 | 용도 |
|---|---|
| `games` | 게임 메타데이터 (appid PK, name, genre, tags, price, sections, review_pct/count, type, is_active 등) |
| `reviews` | 리뷰 본문 (게임당 최대 20개, NLP 임베딩 입력) |
| `similar_games` | `appid`별 상위 10개 유사 게임 + 점수 (`ai/models/hybrid.py`가 매번 통째로 재계산) |
| `cf_recommendations` | `steamid`별 상위 20개 CF 추천 + 점수 (`ai/models/collaborative.py`가 재계산) |

`games.sections`가 채워진 행만 "서비스에 노출되는" 게임이다 (콤마 구분:
`trending`, `new_release`, `indie`, `multiplayer`, `catalog`). 스키마
세부사항과 수집 로직은 DATA_PIPELINE_DESIGN.md 참고.

### `backend/data/app.db` — 운영 데이터 (backend가 쓰고, ai가 CF 학습 시 읽음)

| 테이블 | 용도 |
|---|---|
| `users` | steamid PK, persona_name, avatar_url |
| `sessions` | 로그인 세션 토큰 (쿠키로 발급, 30일 만료) |
| `owned_games` | 유저별 보유 게임 + 플레이타임 (`GetOwnedGames` 결과) |

## 추천 모델 (`ai/models/`)

세 가지 콘텐츠 신호를 가중 결합한다 (`hybrid.py`):

| 신호 | 소스 | 실측 품질 | 가중치 |
|---|---|---|---|
| 장르/태그 TF-IDF | `games.genre` + `games.tags` | 가장 정확함 (Terraria→Starbound류) | 0.6 |
| CV 이미지 (CLIP) | 헤더 이미지 | 브랜드/비주얼 스타일은 잘 잡음 | 0.25 |
| NLP 리뷰 임베딩 (Sentence-BERT) | 리뷰 본문 | 리뷰 말투/밈 톤에 끌려가 노이즈 많음 | 0.15 |

가중치가 동일하지 않은 이유는 실제로 세 방식을 각각 검증해본 결과라서다
— 자세한 비교는 DATA_PIPELINE_DESIGN.md의 "알려진 한계" 참고. 결과는
`similar_games` 테이블에 저장되고 `GET /games/{appid}/similar`가 그대로
서빙한다.

**CF(협업 필터링)** — `collaborative.py`가 `backend/data/app.db`의
`owned_games`(플레이타임)를 `log1p`로 눌러 암묵적 평점처럼 취급하고
scikit-surprise의 SVD로 학습한다. 로그인 유저가 극소수(현재 1명)라 아직
교차 유저 신호가 없어 사실상 전역 평균으로 수렴하는 콜드스타트 상태 —
유저가 늘어날수록 자동으로 개인화된다. 결과는 `GET /users/me/recommendations`
(로그인 필요)로 서빙.

## 인증 흐름 (Steam OpenID)

Steam은 OAuth가 아니라 **OpenID 2.0**이라 앱 등록/client_id가 필요 없다
(`STEAM_API_KEY`만 있으면 됨). `backend/app/api/auth.py`:

1. `GET /auth/login` → `steamcommunity.com/openid/login`으로 리다이렉트
2. Steam에서 로그인 후 `GET /auth/callback`으로 돌아옴 → 응답을 Steam에
   재검증(`check_authentication`) → steamid64 추출 → 세션 쿠키 발급 →
   `IPlayerService/GetOwnedGames`로 라이브러리 자동 수집
3. `GET /auth/me`, `POST /auth/logout`

로컬 개발(Vite :5173)과 Docker(nginx :3000) 둘 다, **콜백 URL을 항상
프론트 origin의 `/api/...` 경로로 잡는다** — 그래야 브라우저가 프론트/
백엔드를 같은 오리진으로 인식해서 쿠키 도메인이 안 꼬인다 (프론트 서버가
`/api/*`를 백엔드로 프록시: dev는 Vite `server.proxy`, 배포는 nginx
`location /api/`). `backend/app/core/config.py`의 `BACKEND_PUBLIC_BASE_URL`
/ `FRONTEND_BASE_URL`로 조정한다.

## 백엔드 API 요약

| 엔드포인트 | 설명 |
|---|---|
| `GET /games/sections/{section}` | 섹션별 게임 목록 (`trending`/`new_release`/`indie`/`multiplayer`) |
| `GET /games/{appid}` | 게임 상세 |
| `GET /games/{appid}/similar` | 하이브리드 콘텐츠 유사도 상위 10개 |
| `GET /auth/login` `/auth/callback` `/auth/me` `POST /auth/logout` | Steam 로그인 |
| `GET /users/me/recommendations` | CF 추천 (로그인 필요) |

## 자동화 (`ai/scheduler.py`)

APScheduler로 매일 04:00에 파이프라인 전체를 재실행한다 (Celery는 브로커가
필요해서 지금 규모엔 과함). 각 단계 스크립트가 이미 resumable해서, 매일
재실행해도 그날 새로 생긴 게임/리뷰만 증분 처리된다. `hybrid.py`/
`collaborative.py`만 매번 전체 재계산(증분 갱신 로직 없음, 지금 규모
~1만 개에서는 몇 분이면 끝나서 문제 없음).

실행: `python ai/scheduler.py` (계속 떠 있어야 함 — 컴퓨터가 켜져 있고
절전 모드가 아니어야 스케줄이 유지된다). `--run-now`로 즉시 1회 실행 가능.

## 배포 (`infra/`)

`docker-compose.yml`은 Postgres/Redis를 안 쓴다 — 실제로 SQLite 두
파일(`games.db`, `app.db`)만 쓰는 구조라, backend는 `ai/data`를 읽기전용
볼륨마운트하고 자체 데이터는 named volume에 저장한다. frontend는 nginx가
정적 파일을 서빙하면서 `/api`를 backend 컨테이너로 프록시한다
(`infra/nginx/default.conf`).

로컬 경로 대신 컨테이너 마운트 경로를 쓰도록 `GAMES_DB_PATH`/
`APP_DB_PATH` 환경변수로 오버라이드 가능 (`ai/collect/db.py`,
`backend/app/core/db.py`, `user_db.py`).

**주의**: 이 프로젝트 개발 환경에는 Docker가 없어서 `docker compose up`
실행 자체는 검증되지 않았다 — 설정 파일 문법과 Python 쪽 환경변수
오버라이드 로직만 확인된 상태.

## 알려진 미완성/향후 과제

- `ai/models/train.py`는 빈 스텁 (학습은 각 모델 파일에서 직접 실행)
- `ai/collect/content_based.py`는 `hybrid.py`로 완전히 대체됨 (더 이상
  단독 실행 안 함, 참고용으로만 남아있음)
- CF는 유저가 늘어야 의미 있어짐 — 지금은 파이프라인 검증 수준
- 가격 지역화(원화 등) 없음, 전부 USD
- 카탈로그는 17만+ 전체가 아니라 10,487개 (섹션 큐레이션 4,463 + 리뷰
  상위 확장 6,024) — 확장 방법은 DATA_PIPELINE_DESIGN.md 참고
