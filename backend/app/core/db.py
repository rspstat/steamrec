"""ai/collect가 채워둔 games.db(SQLite)를 읽기 전용으로 여는 헬퍼.

ai/(연구·수집)와 backend/(서빙)는 별도 requirements를 쓰는 분리 구조라,
backend는 ai/collect의 무거운 의존성 없이 이미 만들어진 SQLite 파일만
읽는다. Postgres로 옮길 때는 이 함수만 교체하면 된다.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "ai" / "data" / "games.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
