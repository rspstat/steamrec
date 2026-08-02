"""로그인한 유저 전용 엔드포인트 — CF(협업 필터링) 추천 노출.

ai/models/collaborative.py가 games.db에 저장해둔 cf_recommendations을
읽기만 한다 (scikit-surprise 등 학습 의존성은 backend에 없음).
"""

from fastapi import APIRouter, Cookie, HTTPException

from app.api.games import _row_to_game
from app.core import user_db
from app.core.db import get_connection
from app.schemas.game import Game

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me/recommendations", response_model=list[Game])
def get_my_recommendations(
    session_token: str | None = Cookie(default=None), limit: int = 20
) -> list[Game]:
    if not session_token:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    user_conn = user_db.get_connection()
    steamid = user_db.get_steamid_from_session(user_conn, session_token)
    user_conn.close()
    if not steamid:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT g.* FROM cf_recommendations c
        JOIN games g ON g.appid = c.appid
        WHERE c.steamid = ?
        ORDER BY c.rank
        LIMIT ?
        """,
        (steamid, limit),
    ).fetchall()
    conn.close()
    return [_row_to_game(row) for row in rows]
