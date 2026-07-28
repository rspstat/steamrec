# ai

AI 모델 개발 전용 (데이터 수집 → 학습 → 평가).

- `data/raw/`: 수집 원본 데이터
- `data/processed/`: 전처리 완료 데이터
- `collect/`: Steam API, 스크래핑 수집 스크립트
- `features/`: 텍스트/이미지 임베딩 추출 로직
- `models/`: 모델 학습 코드
- `evaluation/`: RMSE, Precision@K 등 평가 스크립트
- `notebooks/`: EDA, 실험용 Jupyter
- `artifacts/`: 학습 완료된 모델 가중치 (git 추적 제외)
