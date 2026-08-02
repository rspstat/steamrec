# steamrec — Steam 게임 추천 AI 서비스

## 개요

Steam 게임을 추천해주는 AI 모델을 개발하고, 웹 서비스로 연계하는 프로젝트.
협업 필터링(CF), 자연어처리(NLP), 컴퓨터 비전(CV)을 결합한 하이브리드 추천 시스템을 목표로 함.

- **저장소명**: `steamrec`
- **저장소 설명**: Hybrid AI recommender for Steam games combining NLP, computer vision, and collaborative filtering.

> 이 문서는 초기 기획 의도를 남겨둔 것이다. **지금 실제로 어떻게 돌아가는지는
> [ARCHITECTURE.md](ARCHITECTURE.md)를, 데이터 수집 세부는
> [DATA_PIPELINE_DESIGN.md](DATA_PIPELINE_DESIGN.md)를 보는 게 정확하다.**
> 아래 기술 스택/로드맵 중 실제와 달라진 부분은 각주로 표시함.

---

## 필요 역량

### 1. 데이터 수집/전처리
- Steam Web API를 통한 사용자 라이브러리, 플레이타임, 리뷰, 태그 데이터 수집
- 보강용 크롤링(Playwright/Selenium) — 리뷰 뉘앙스, 세부 태그 등 API 미제공 정보
- 결측치·이상치 처리, 게임 메타데이터(장르, 태그, 가격) 스키마 설계

### 2. 추천 시스템 알고리즘
- 협업 필터링(Collaborative Filtering) — 사용자-게임 행렬 기반
- 콘텐츠 기반 필터링 — 게임 태그/장르/텍스트/이미지 유사도
- 하이브리드 방식, 콜드스타트 문제 해결

### 3. NLP (자연어처리)
- 리뷰/태그 텍스트 임베딩 (Sentence-BERT, KoBERT)
- 감성분석 — 긍정/부정, 세부 불만 포인트 추출
- 핵심 키워드 추출 및 요약

### 4. CV (컴퓨터 비전)
- 스크린샷/커버 이미지에서 아트스타일 임베딩 추출
- CLIP(이미지-텍스트 멀티모달) 또는 ResNet(사전학습 특징 추출기) 활용
- 텍스트로는 안 맞지만 비주얼 스타일이 맞는 케이스 보완

### 5. 모델 서빙 백엔드
- FastAPI로 학습된 모델을 API로 노출
- Redis로 추천 결과 캐싱
- Celery/APScheduler로 배치 재학습 스케줄링

### 6. 프론트엔드 + 배포
- React 기반 추천 결과 UI
- Docker, CI/CD 배포 파이프라인

---

## 기술 스택 요약

| 영역 | 계획 당시 | 실제 (2026-08 기준) |
|---|---|---|
| 데이터 수집 | Steam Web API, `steamspypi`, Playwright/Selenium, PostgreSQL | Steam Web API + SteamSpy + 스토어 검색 API(전부 `requests` 직호출), **SQLite** |
| NLP | Sentence-BERT / KoBERT | Sentence-BERT(다국어), KoBERT는 안 씀 (다국어 리뷰라 범용 모델로 충분) |
| CV | CLIP / ResNet | CLIP (`clip-ViT-B-32`) |
| 추천 알고리즘 | `scikit-learn`, `Surprise`, PyTorch, FAISS / pgvector | `scikit-learn`(TF-IDF), `Surprise`(SVD) — FAISS는 설치만 해두고 아직 미사용(현재 규모론 dense 코사인 유사도로 충분) |
| 서빙 | FastAPI, Redis, Celery / APScheduler | FastAPI, **APScheduler** (Redis/Celery는 안 씀 — 브로커 필요 없는 규모) |
| 프론트엔드 | React, Axios/Fetch | React + Vite, Axios |
| 인프라 | Docker, Docker Compose, GitHub Actions, Nginx, AWS/GCP | Docker Compose, Nginx 설정까지 작성 완료 / **미검증**(개발 환경에 Docker 없음) — CI/CD·클라우드 배포는 아직 안 함 |

---

## 폴더 구조

AI 모델 개발(연구/실험)과 백엔드(서빙)를 분리한 모노레포 구조.

```
steamrec/
├── ai/                        # AI 모델 개발 전용
│   ├── data/
│   │   ├── raw/                # 수집 원본 데이터
│   │   └── processed/          # 전처리 완료 데이터
│   ├── collect/                # Steam API, 스크래핑 수집 스크립트
│   ├── features/               # 텍스트/이미지 임베딩 추출 로직
│   │   ├── nlp_embed.py
│   │   └── cv_embed.py
│   ├── models/                 # 모델 학습 코드
│   │   ├── collaborative.py    # CF (SVD 등)
│   │   ├── hybrid.py           # CF+NLP+CV 결합 모델
│   │   └── train.py
│   ├── evaluation/             # RMSE, Precision@K 등 평가 스크립트
│   ├── notebooks/              # EDA, 실험용 Jupyter
│   ├── artifacts/               # 학습 완료된 모델 가중치 (.gitignore 대상)
│   ├── requirements.txt
│   └── README.md
│
├── backend/                    # API 서빙 전용 (경량화)
│   ├── app/
│   │   ├── api/                 # FastAPI 라우터 (games, auth, users)
│   │   ├── schemas/              # Pydantic 스키마
│   │   ├── core/                 # 설정, DB(SQLite 읽기전용/자체 운영 DB)
│   │   └── main.py
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── api/
│   │   └── App.tsx
│   ├── public/
│   ├── package.json
│   └── Dockerfile
│
├── infra/
│   ├── docker-compose.yml
│   └── nginx/
│
├── docs/                      # 프로젝트 문서
├── .env.example
├── .gitignore
└── README.md
```

**분리 원칙** (실제로 지금까지 잘 지켜지고 있음 — 세부는 ARCHITECTURE.md)
- `ai/`: 데이터 수집 → 학습까지 모델 개발 전체 라이프사이클 (연구 영역), `ai/data/games.db`+`ai/artifacts/`에 씀
- `backend/`: `games.db`를 **읽기 전용**으로만 열어 API로 노출 (서빙 영역), 자기 운영 데이터(유저/세션)는 별도 `backend/data/app.db`
- `ai/requirements.txt`(PyTorch, transformers 등 무거운 라이브러리)와 `backend/requirements.txt`(FastAPI 등 가벼운 라이브러리)를 분리해 배포 이미지 크기 관리
- CF 학습(`ai/models/collaborative.py`)처럼 유저 데이터가 필요한 경우도 `app.db`를 읽기 전용으로만 참조 — backend는 절대 무거운 학습 의존성을 필요로 하지 않음

---

## 개발 로드맵 — 계획 대비 실제

계획은 "CF 베이스라인부터"였는데, 실제로는 **CF를 1단계에 못 놓았다** —
협업 필터링은 실제 유저-게임 상호작용 데이터가 있어야 하는데, 그건 Steam
로그인 기능이 있어야 모을 수 있고, 로그인 기능은 웹사이트 뼈대(backend+
frontend)가 있어야 의미가 있다. 그래서 순서를 "유저 데이터 없이도 당장
값어치를 낼 수 있는 것부터"로 뒤집었다. 실제로 밟은 순서:

| 순서 | 내용 | 상태 |
|---|---|---|
| 1 | 데이터 수집 파이프라인 (섹션 큐레이션 4,463개) | ✅ 완료 |
| 2 | 콘텐츠 기반 추천 (장르/태그 TF-IDF) | ✅ 완료 |
| 3 | Backend(FastAPI) + Frontend(React) 뼈대 | ✅ 완료 |
| 4 | NLP 임베딩 (리뷰 Sentence-BERT) | ✅ 완료 (품질 한계 있음, ARCHITECTURE.md 참고) |
| 5 | CV 임베딩 (헤더 이미지 CLIP) | ✅ 완료 |
| 6 | 하이브리드 결합 (장르 60% + CV 25% + NLP 15%) | ✅ 완료 |
| 7 | 카탈로그 확장 (10,487개) | ✅ 완료 |
| 8 | Steam 로그인(OpenID) + 유저 라이브러리 수집 | ✅ 완료 |
| 9 | CF 코드 기반 (SVD) | ✅ 완료 — 콜드스타트 상태(유저 수 적음) |
| 10 | Docker 배포 인프라 | ✅ 작성 완료, **Docker 미설치 환경이라 미검증** |
| 11 | 자동화 (APScheduler, 매일 04:00) | ✅ 완료 |
| — | 실제 도메인/서버 배포, CI/CD | ⬜ 아직 |

세부 구현/검증 내역은 [ARCHITECTURE.md](ARCHITECTURE.md),
[DATA_PIPELINE_DESIGN.md](DATA_PIPELINE_DESIGN.md) 참고.

### 액션 아이템 (전부 완료)
1. ~~Steam API 키 발급~~ 완료
2. ~~저장소 기본 구조 세팅~~ 완료
3. ~~Steam API로 게임 데이터 가져오는 스크립트 작성~~ 완료 (100개 → 4,463개 → 10,487개로 확장)

---

## 실제 출시 시 고려사항

### 법적/약관
- Steam Web API 이용약관상 상업적 이용 제약 가능성 — Valve 측 Steamworks API 약관 확인 필요 (전면 재판매/경쟁 서비스는 금지되는 경우가 많음)
- 스크래핑 보강 데이터의 상업적 사용은 리스크가 더 큼 → 출시 단계에서는 공식 API 범위로 축소 권장
- 한국 개인정보보호법 — Steam 연동 로그인 시 개인정보처리방침, 이용약관 구비 필요
- 실제 출시 전 변호사 자문 또는 Valve 측 문의 권장 (법률 전문가 확인 필요 사항)

### 시장 포지셔닝
- 국내 스팀인벤, 도탁스 등 기존 커뮤니티 서비스와의 차별점 필요
- 니치 포지셔닝 예시: 인디게임 특화 추천, 한국 유저 리뷰 기반 추천

### 운영 현실
- 사용자 증가에 따른 서버 비용(모델 서빙, DB, 캐싱) 발생
- 신작/리뷰 갱신을 위한 지속적 데이터 파이프라인 유지보수
- 트래픽 대응을 위한 부하테스트, 오토스케일링 고려

### 단계적 출시 제안
1. MVP를 무료 개인 서비스로 소규모 오픈 (베타, 저트래픽)
2. Steam API 약관 준수 여부 재확인
3. 반응 확인 후 도메인/서버 투자 결정
4. 필요 시 수익모델(광고, 프리미엄 추천 등) 설계