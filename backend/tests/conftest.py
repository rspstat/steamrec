"""games.db를 임시 파일로 대체하는 fixture들.

backend/app/core/db.py의 get_connection()은 `mode=ro`로 파일을 열기 때문에 `:memory:` DB는
쓸 수 없고, tmp_path의 실제 파일 DB가 필요하다. DB_PATH는 get_connection() 호출 시점에 읽는
모듈 전역이라 monkeypatch가 먹는다.

스키마는 ai/collect/db.py의 games 테이블(price는 INTEGER, is_active는 NOT NULL DEFAULT 1)과
ai/models/hybrid.py의 similar_games 테이블을 그대로 따른다 (backend는 ai/를 import하지 않으므로
여기에 복제). 컬럼 타입이 실제와 다르면 응답 검증(Game 스키마)이 실제와 다르게 동작한다.
"""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

SCHEMA = """
CREATE TABLE games (
    appid INTEGER PRIMARY KEY,
    name TEXT,
    type TEXT,
    genre TEXT,
    tags TEXT,
    price INTEGER,
    positive INTEGER,
    negative INTEGER,
    owners_estimate TEXT,
    release_date TEXT,
    last_modified INTEGER,
    first_seen_at TEXT,
    last_updated_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    sections TEXT,
    review_pct INTEGER,
    review_count INTEGER
);
CREATE TABLE similar_games (
    appid INTEGER,
    similar_appid INTEGER,
    score REAL,
    rank INTEGER,
    PRIMARY KEY (appid, rank)
);
"""


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "games.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.close()
    monkeypatch.setattr("app.core.db.DB_PATH", path)
    return path


_UNSET = object()


@pytest.fixture
def add_game(db_path):
    """games 행을 넣는 헬퍼. backend는 요청마다 DB를 새로 열므로 테스트 중간에 추가해도 반영된다.

    tags: 안 주면 '{}', dict/list는 JSON으로 직렬화, str은 그대로(깨진 값 포함), None은 NULL.
    """

    def _add(appid, *, name=None, tags=_UNSET, **fields):
        if tags is _UNSET:
            tags = {}
        if tags is not None and not isinstance(tags, str):
            tags = json.dumps(tags)
        row = {
            "appid": appid,
            "name": name if name is not None else f"Game {appid}",
            "genre": "RPG",
            "tags": tags,
            "price": 0,
            "review_count": 0,
            "sections": "trending",
            "is_active": 1,
        }
        row.update(fields)
        columns = ", ".join(row)
        placeholders = ", ".join("?" * len(row))
        conn = sqlite3.connect(db_path)
        conn.execute(f"INSERT INTO games ({columns}) VALUES ({placeholders})", list(row.values()))
        conn.commit()
        conn.close()

    return _add


@pytest.fixture
def add_similar(db_path):
    def _add(appid, similar_appid, rank, score):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO similar_games (appid, similar_appid, score, rank) VALUES (?, ?, ?, ?)",
            (appid, similar_appid, score, rank),
        )
        conn.commit()
        conn.close()

    return _add


@pytest.fixture
def client(db_path):
    from app.main import app

    return TestClient(app)
