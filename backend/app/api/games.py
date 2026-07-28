import json
import sqlite3

from fastapi import APIRouter, HTTPException

from app.core.db import get_connection
from app.schemas.game import Game, SimilarGame

router = APIRouter(prefix="/games", tags=["games"])

VALID_SECTIONS = {"trending", "new_release", "indie", "multiplayer"}


def _row_to_game(row: sqlite3.Row) -> Game:
    # SteamSpy는 태그가 없으면 {} 대신 [] (빈 리스트)를 주기도 한다 (PHP 백엔드 특성).
    tags_raw = json.loads(row["tags"]) if row["tags"] else {}
    tags = list(tags_raw.keys()) if isinstance(tags_raw, dict) else []
    sections = row["sections"].split(",") if row["sections"] else []
    return Game(
        appid=row["appid"],
        name=row["name"],
        genre=row["genre"],
        tags=tags,
        price=row["price"],
        positive=row["positive"],
        negative=row["negative"],
        review_pct=row["review_pct"],
        review_count=row["review_count"],
        release_date=row["release_date"],
        sections=sections,
    )


@router.get("/sections/{section}", response_model=list[Game])
def get_section(section: str, limit: int = 50, offset: int = 0) -> list[Game]:
    if section not in VALID_SECTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown section: {section}")

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM games
        WHERE sections IS NOT NULL
          AND (',' || sections || ',') LIKE ?
          AND is_active = 1
        ORDER BY review_count DESC
        LIMIT ? OFFSET ?
        """,
        (f"%,{section},%", limit, offset),
    ).fetchall()
    conn.close()
    return [_row_to_game(row) for row in rows]


@router.get("/{appid}", response_model=Game)
def get_game(appid: int) -> Game:
    conn = get_connection()
    row = conn.execute("SELECT * FROM games WHERE appid = ?", (appid,)).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return _row_to_game(row)


@router.get("/{appid}/similar", response_model=list[SimilarGame])
def get_similar_games(appid: int, limit: int = 10) -> list[SimilarGame]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT g.*, s.score AS similarity_score FROM similar_games s
        JOIN games g ON g.appid = s.similar_appid
        WHERE s.appid = ?
        ORDER BY s.rank
        LIMIT ?
        """,
        (appid, limit),
    ).fetchall()
    conn.close()
    return [
        SimilarGame(**_row_to_game(row).model_dump(), score=row["similarity_score"])
        for row in rows
    ]
