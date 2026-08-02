"""ai/collect가 채워둔 games.db(SQLite)를 읽기 전용으로 여는 헬퍼.

ai/(연구·수집)와 backend/(서빙)는 별도 requirements를 쓰는 분리 구조라,
backend는 ai/collect의 무거운 의존성 없이 이미 만들어진 SQLite 파일만
읽는다. Docker 환경에서는 GAMES_DB_PATH 환경 변수로 마운트된 경로를
가리키게 오버라이드한다 (infra/docker-compose.yml 참고).
"""

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(
    os.environ.get("GAMES_DB_PATH")
    or (Path(__file__).resolve().parents[3] / "ai" / "data" / "games.db")
)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
