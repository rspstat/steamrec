# steamrec
Hybrid AI recommender for Steam games combining NLP, computer vision, and collaborative filtering

## 문서
- [기획](docs/PROJECT_PLAN.md) — 초기 기획 의도 + 계획 대비 실제 진행 상황
- [아키텍처](docs/ARCHITECTURE.md) — 지금 실제로 어떻게 동작하는지
- [데이터 파이프라인](docs/DATA_PIPELINE_DESIGN.md) — 수집 로직 세부

## 로컬 실행

```bash
# 1. .env에 STEAM_API_KEY 설정 (.env.example 참고)

# 2. 데이터 파이프라인 (최초 1회, ai/data/games.db 생성)
cd ai/collect && python discover_sections.py && python enrich_selected.py && python enrich_genre_fallback.py
cd ../features && python fetch_reviews.py 2>/dev/null; python cv_embed.py && python nlp_embed.py
cd ../models && python hybrid.py && python collaborative.py

# 3. 매일 자동 갱신 (선택, 계속 떠 있어야 함)
cd ai && python scheduler.py

# 4. backend
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload

# 5. frontend
cd frontend && npm install && npm run dev
```

Docker로 띄우려면 `docker compose -f infra/docker-compose.yml up --build`
(설정은 작성되어 있으나 개발 환경에 Docker가 없어 실행 자체는 미검증).

## 테스트

```bash
# ai — 순수 함수와 합성 데이터만 쓴다. games.db/임베딩 파일/torch 없이 numpy, scikit-learn, pytest만 있으면 된다
cd ai && pip install -r requirements-dev.txt && pytest

# backend — 임시 파일 DB로 games.db를 대체한다
cd backend && pip install -r requirements-dev.txt && pytest
```

## 평가

신호별(장르 / CV 이미지 / NLP 리뷰 / 하이브리드) 유사 게임 추천 품질을 **같은 정답으로 비교**하는
평가 스크립트가 있다 (`ai/evaluation/eval_similar.py`). 상세는 [ai/README.md](ai/README.md#평가).

```bash
cd ai && python -m evaluation.eval_similar --k 10 --jaccard-threshold 0.3 --seed 42
# → ai/evaluation/results.md, results.json 생성
```

> **현재 상태: 평가 코드와 테스트는 있지만, 실제 데이터(`games.db`, 임베딩 npz)로 실행한 결과는 아직 없다.**
> 그래서 이 문서에는 평가 수치를 싣지 않는다. 하이브리드 가중치(장르 0.6 / CV 0.25 / NLP 0.15)는
> 직관으로 정한 값이고 아직 평가로 검증된 적이 없다. 위 명령을 실제 데이터로 돌리면
> 측정된 표와 한계 설명이 `ai/evaluation/results.md`에 생성된다.

평가의 핵심 한계 (전체는 `results.md`에 같이 기록됨):

- 서비스 추천은 게임 간 유사도(item-item)라 정답 라벨이 없다. 정답은 **유저 태그 자카드 ≥ 0.3**이라는 **대리 지표**다.
- 정답이 태그라서 모델 입력에서 태그를 빼고 장르만 쓴다(순환 평가 방지). 그래도 장르와 태그는 독립이 아니라서 장르 신호가 유리할 수 있다.
- 따라서 평가에서 재는 하이브리드는 운영 하이브리드(장르+태그)와 입력이 다르다.
- **CF(협업 필터링)는 평가하지 않는다.** 로그인 유저가 1명뿐이라 지표가 의미가 없다.
