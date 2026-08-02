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
