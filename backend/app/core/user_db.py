"""backend 자체 운영 데이터(유저/세션/보유 게임) 저장소.

ai/data/games.db는 ai/collect가 채우는 읽기 전용 참고 데이터라 backend가
쓰기 접근을 하면 안 된다 (ai/backend 분리 원칙, core/db.py는 mode=ro로만
연다). 로그인한 유저/세션/보유 게임처럼 backend가 직접 써야 하는 데이터는
여기, 별도 SQLite(app.db)로 관리한다. Docker 환경에서는 APP_DB_PATH
환경 변수로 마운트된 볼륨 경로를 가리키게 오버라이드한다.
"""

import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(
    os.environ.get("APP_DB_PATH")
    or (Path(__file__).resolve().parents[2] / "data" / "app.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    steamid TEXT PRIMARY KEY,
    persona_name TEXT,
    avatar_url TEXT,
    created_at TEXT,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_token TEXT PRIMARY KEY,
    steamid TEXT NOT NULL,
    created_at TEXT,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS owned_games (
    steamid TEXT NOT NULL,
    appid INTEGER NOT NULL,
    name TEXT,
    playtime_forever INTEGER,
    collected_at TEXT,
    PRIMARY KEY (steamid, appid)
);
"""

SESSION_TTL_DAYS = 30


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_user(
    conn: sqlite3.Connection, steamid: str, persona_name: str | None, avatar_url: str | None
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO users (steamid, persona_name, avatar_url, created_at, last_login_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(steamid) DO UPDATE SET
            persona_name=excluded.persona_name,
            avatar_url=excluded.avatar_url,
            last_login_at=excluded.last_login_at
        """,
        (steamid, persona_name, avatar_url, now, now),
    )
    conn.commit()


def create_session(conn: sqlite3.Connection, steamid: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    conn.execute(
        "INSERT INTO sessions (session_token, steamid, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, steamid, now.isoformat(), expires.isoformat()),
    )
    conn.commit()
    return token


def get_steamid_from_session(conn: sqlite3.Connection, token: str) -> str | None:
    row = conn.execute(
        "SELECT steamid, expires_at FROM sessions WHERE session_token = ?", (token,)
    ).fetchone()
    if row is None:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return None
    return row["steamid"]


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE session_token = ?", (token,))
    conn.commit()


def save_owned_games(conn: sqlite3.Connection, steamid: str, games: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT INTO owned_games (steamid, appid, name, playtime_forever, collected_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(steamid, appid) DO UPDATE SET
            name=excluded.name,
            playtime_forever=excluded.playtime_forever,
            collected_at=excluded.collected_at
        """,
        [
            (steamid, g["appid"], g.get("name"), g.get("playtime_forever", 0), now)
            for g in games
        ],
    )
    conn.commit()
