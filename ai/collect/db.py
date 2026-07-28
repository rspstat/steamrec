"""ai/data/games.db (SQLite)에 대한 공용 접근 헬퍼.

Docker/Postgres가 아직 없는 로컬 개발 단계라 SQLite로 시작한다. 컬럼 구조는
docs/DATA_PIPELINE_DESIGN.md의 `games` 테이블 설계와 동일하게 맞춰서, 나중에
infra/docker-compose.yml의 Postgres로 옮길 때 스키마를 그대로 재사용한다.
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "games.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
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
    is_active INTEGER NOT NULL DEFAULT 1
);
"""

# 기존에 만들어둔 games.db에는 없을 수 있는 컬럼들 (Job 3, 섹션 큐레이션에서 추가됨).
_MIGRATION_COLUMNS = {
    "type": "TEXT",
    "release_date": "TEXT",
    "sections": "TEXT",  # 콤마 구분: trending,indie,multiplayer,new_release
    "review_pct": "INTEGER",
    "review_count": "INTEGER",
}


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    for col, col_type in _MIGRATION_COLUMNS.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE games ADD COLUMN {col} {col_type}")
    return conn


def existing_appids(conn: sqlite3.Connection) -> set[int]:
    return {row[0] for row in conn.execute("SELECT appid FROM games")}


def _parse_price(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def upsert_steamspy_game(conn: sqlite3.Connection, game: dict, now: str) -> None:
    """SteamSpy 'all'/'appdetails' 응답 한 건을 upsert (Job 1, Job 4용)."""
    conn.execute(
        """
        INSERT INTO games (appid, name, genre, tags, price, positive, negative,
                            owners_estimate, first_seen_at, last_updated_at, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(appid) DO UPDATE SET
            name=COALESCE(excluded.name, games.name),
            genre=excluded.genre,
            tags=excluded.tags,
            price=excluded.price,
            positive=excluded.positive,
            negative=excluded.negative,
            owners_estimate=excluded.owners_estimate,
            last_updated_at=excluded.last_updated_at,
            is_active=1
        """,
        (
            game["appid"],
            game.get("name"),
            game.get("genre", ""),
            json.dumps(game.get("tags", {}), ensure_ascii=False),
            _parse_price(game.get("price")),
            game.get("positive"),
            game.get("negative"),
            game.get("owners"),
            now,
            now,
        ),
    )


def upsert_store_genre_fallback(
    conn: sqlite3.Connection,
    appid: int,
    genre: str,
    price: int | None,
    release_date: str | None,
    now: str,
) -> None:
    """Store appdetails 기반 genre/price/release_date 보강.

    SteamSpy가 아직 못 따라잡은 신작(genre가 비어있는 섹션 후보)용 — 이미
    값이 있는 컬럼은 덮어쓰지 않는다 (COALESCE).
    """
    conn.execute(
        """
        UPDATE games
        SET genre = COALESCE(NULLIF(genre, ''), ?),
            price = COALESCE(price, ?),
            release_date = COALESCE(release_date, ?),
            last_updated_at = ?
        WHERE appid = ?
        """,
        (genre, price, release_date, now, appid),
    )


def upsert_store_classification(
    conn: sqlite3.Connection,
    appid: int,
    app_type: str,
    release_date: str | None,
    now: str,
) -> None:
    """Store appdetails로 얻은 type/release_date 반영 (Job 3용).

    type이 'game'이 아니면 추천 대상에서 제외하되 행 자체는 남긴다.
    """
    is_active = 1 if app_type == "game" else 0
    conn.execute(
        """
        UPDATE games
        SET type = ?, release_date = ?, is_active = ?, last_updated_at = ?
        WHERE appid = ?
        """,
        (app_type, release_date, is_active, now, appid),
    )


def upsert_section_candidate(
    conn: sqlite3.Connection,
    appid: int,
    name: str,
    release_date: str | None,
    review_pct: int | None,
    review_count: int | None,
    section: str,
    now: str,
) -> None:
    """웹사이트 섹션(신규 인기 급상승/인디/멀티/최신 출시) 후보로 뽑힌 게임을 upsert.

    이미 다른 섹션 후보로 등록된 appid면 `sections`에 섹션 이름을 합쳐 넣는다
    (예: Indie이면서 Multiplayer인 게임은 "indie,multiplayer"가 됨).
    Store 검색 결과 기준이라 category1=998(게임) 필터가 이미 적용돼 있으므로
    DLC/사운드트랙/툴이 섞일 걱정은 없다.
    """
    row = conn.execute(
        "SELECT sections FROM games WHERE appid = ?", (appid,)
    ).fetchone()
    if row and row[0]:
        existing_sections = set(row[0].split(","))
        existing_sections.add(section)
        merged_sections = ",".join(sorted(existing_sections))
    else:
        merged_sections = section

    conn.execute(
        """
        INSERT INTO games (appid, name, type, release_date, review_pct, review_count,
                            sections, first_seen_at, last_updated_at, is_active)
        VALUES (?, ?, 'game', ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(appid) DO UPDATE SET
            name=COALESCE(NULLIF(excluded.name, ''), games.name),
            release_date=COALESCE(games.release_date, excluded.release_date),
            review_pct=COALESCE(excluded.review_pct, games.review_pct),
            review_count=COALESCE(excluded.review_count, games.review_count),
            sections=excluded.sections,
            last_updated_at=excluded.last_updated_at
        """,
        (
            appid,
            name,
            release_date,
            review_pct,
            review_count,
            merged_sections,
            now,
            now,
        ),
    )


def upsert_new_appid(
    conn: sqlite3.Connection, appid: int, name: str, last_modified: int, now: str
) -> None:
    """IStoreService/GetAppList로 발견한 신규 appid를 최소 정보로 upsert (Job 2용)."""
    conn.execute(
        """
        INSERT INTO games (appid, name, last_modified, first_seen_at, last_updated_at, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(appid) DO UPDATE SET
            name=excluded.name,
            last_modified=excluded.last_modified,
            last_updated_at=excluded.last_updated_at
        """,
        (appid, name, last_modified, now, now),
    )
