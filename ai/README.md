# ai

AI 모델 개발 전용 (데이터 수집 → 학습 → 평가).

- `data/raw/`: 수집 원본 데이터
- `data/processed/`: 전처리 완료 데이터
- `collect/`: Steam API, 스크래핑 수집 스크립트
- `features/`: 텍스트/이미지 임베딩 추출 로직
- `models/`: 모델 학습 코드
- `evaluation/`: 추천 평가 — `metrics.py`(지표 함수), `eval_similar.py`(신호별 비교). 결과 `results.md`/`results.json`은 실행하면 생성됨
- `tests/`: pytest (`evaluation/`, `models/hybrid.py`의 순수 함수 대상)
- `notebooks/`: EDA, 실험용 Jupyter
- `artifacts/`: 학습 완료된 모델 가중치 (git 추적 제외)

## 평가

### 무엇을 비교하는가

운영 중인 추천은 게임 간 유사도(item-item)라 "이 게임에는 이 게임이 정답"이라는 라벨이 없다.
그래서 `hybrid.py`의 가중치(장르 0.6 / CV 0.25 / NLP 0.15)와 "NLP는 신뢰도가 낮다", "CV는 장르 신호가 약하다"는
주석은 직관이었다. `eval_similar.py`는 이를 네 설정으로 나눠 같은 정답으로 비교한다.

| 설정 | 사용 신호 |
|---|---|
| genre-only | 장르 TF-IDF |
| cv-only | CLIP 이미지 임베딩 |
| nlp-only | 리뷰 임베딩 (Sentence-BERT) |
| hybrid | 위 셋을 현재 `WEIGHTS`로 결합 |

**정답(대리 지표)**: 두 게임의 Steam 유저 태그 집합의 자카드 유사도가 임계값(기본 0.3) 이상이면 "관련 있음".

**지표**: Precision@K, NDCG@K(이진 relevance), 평균 Tag-Jaccard@K, coverage, 리스트 내 다양성,
추천된 게임의 `review_count` 중앙값(인기 편향). 후보에서 무작위로 뽑았을 때의 기대값을 기준선으로 함께 기록한다.
각 지표의 정의는 생성되는 `results.md`에 있다.

### 순환 평가 방지

운영 `hybrid.py`는 `genre`와 `tags`를 합쳐 모델 입력으로 쓴다. 그 상태로 `tags`를 정답으로 쓰면
장르/태그 신호가 trivially 이기므로, **평가용 genre-only/hybrid는 입력에서 `tags`를 빼고 `genre`만 쓴다**
(`load_genre_texts(conn, include_tags=False)`). 정답은 `tags`만으로 계산한다.
이를 어기면 실패하는 회귀 테스트가 있다 (`tests/test_eval_similar.py`).

### 실행

필요한 입력은 `ai/data/games.db`와 `ai/artifacts/{cv,nlp}_embeddings.npz` (루트 README의 "로컬 실행"으로 생성).
없으면 어떤 파일이 왜 필요한지 안내하고 종료한다.

```bash
cd ai
python -m evaluation.eval_similar --k 10 --jaccard-threshold 0.3 --seed 42
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--k` | 10 | Top-K |
| `--jaccard-threshold` | 0.3 | 이 값 이상이면 정답 (0 초과 1 이하) |
| `--seed` | 42 | 동점 처리 순서를 섞는 seed (장르-only는 동점이 매우 흔함) |
| `--db`, `--artifacts-dir` | `data/games.db`, `artifacts/` | 입력 경로 |
| `--out-dir` | `evaluation/` | `results.json`, `results.md` 저장 위치 |
| `--block-size` | 256 | 한 번에 처리할 쿼리 수 (메모리 조절. n×n 행렬을 통째로 만들지 않고 블록 단위로 계산) |

출력에는 평가 대상 게임 수, 태그가 비어 있어 제외한 게임 수, 정답이 없어 제외한 쿼리 수가 포함된다.

참고: 합성 데이터 1만 개(CLIP 512차원, MiniLM 384차원 크기)로 한 번 재본 결과 약 1분, 파이썬 할당 기준 메모리
최대 약 0.5GB였다 (tracemalloc을 켠 상태라 실제 시간은 이보다 짧을 수 있음. 실제 데이터로 잰 값이 아님).

### 현재 상태

**실제 데이터로 아직 실행하지 않았다.** 평가 코드와 테스트는 합성 데이터(군집 구조를 심은 가짜 게임)로만
검증했으므로, 이 문서에는 측정 수치가 없고 어느 신호가 낫다는 결론도 없다. 실행 결과가 기대와 달라도
(예: 하이브리드가 장르-only보다 낮게 나와도) `results.md`에 그대로 기록한다.

### 한계

1. 정답은 태그 기반 **대리 지표**이며 사람의 유사도 판단이 아니다. 태그를 신호로 쓰지 않는 CV/NLP가 불리할 수 있다.
2. 모델 입력에서 태그는 뺐지만 **장르와 태그는 독립이 아니다**(Action, RPG, Indie 등이 겹침). 장르 신호가 유리했을 가능성이 남는다.
3. 평가의 hybrid는 장르-only 신호를 쓰므로 운영 hybrid(장르+태그)와 다르다. 운영 모델 자체의 수치가 아니다.
4. 정답은 임계값에 의존한다. 정답이 하나도 없는 쿼리는 제외한다.
5. **가중치 튜닝은 하지 않았다.** 이 정답으로 가중치를 고르고 같은 정답으로 점수를 보고하면 안 되므로,
   튜닝하려면 게임을 검증/테스트로 분할해 검증 셋으로 고르고 테스트 셋으로만 보고해야 한다.
6. **CF(SVD)는 평가하지 않는다.** 로그인 유저가 1명뿐이라 Recall@K 등이 의미가 없다.
7. 내 라이브러리 보유 게임을 숨기고 Hit@K를 보는 홀드아웃 평가는 구현하지 않았다 (유저 1명이라 표본이 너무 작음).

## 테스트

```bash
cd ai
pip install -r requirements-dev.txt   # numpy, scikit-learn, pytest만. torch 등 불필요
pytest
```

테스트는 모델·DB·npz 파일 없이 순수 함수와 합성 데이터만 쓴다.
`collaborative.py`(scikit-surprise)와 임베딩 추출(`features/`)은 테스트 범위에 포함하지 않는다.
