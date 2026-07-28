import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "")

# 개발 환경 기준: 브라우저는 항상 프론트엔드 origin(:5173)만 보고, /api/*는
# Vite dev 프록시가 백엔드(:8010)로 그대로 전달한다. Steam OpenID 콜백도
# 이 경로로 돌아오게 하면 쿠키가 항상 같은 도메인(:5173)에 실려서, 포트가
# 다른 프론트/백엔드 사이의 쿠키 도메인 문제를 피할 수 있다.
BACKEND_PUBLIC_BASE_URL = os.environ.get(
    "BACKEND_PUBLIC_BASE_URL", "http://localhost:5173/api"
)
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:5173")
