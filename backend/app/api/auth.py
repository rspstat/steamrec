"""Steam OpenID 2.0 로그인.

Steam은 OAuth가 아니라 OpenID 2.0을 쓴다 — 구글/페이스북처럼 개발자 포털에
앱을 등록하고 client_id를 받는 절차가 없고, STEAM_API_KEY만 있으면 된다.
steamcommunity.com/openid/login으로 리다이렉트했다가 돌아오는 방식이고,
콜백에서 응답을 Steam에 재검증(check_authentication)한 뒤 steamid64를
얻으면 GetOwnedGames로 그 유저의 보유 게임(라이브러리)을 수집해 저장한다.
CF(협업 필터링)에 필요한 유저-게임 상호작용 데이터가 여기서부터 쌓인다.
"""

import re
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.core import user_db
from app.core.config import BACKEND_PUBLIC_BASE_URL, FRONTEND_BASE_URL, STEAM_API_KEY
from app.schemas.user import User

router = APIRouter(prefix="/auth", tags=["auth"])

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
CLAIMED_ID_RE = re.compile(r"/id/(\d+)$")
SESSION_COOKIE = "session_token"


@router.get("/login")
def login() -> RedirectResponse:
    return_to = f"{BACKEND_PUBLIC_BASE_URL}/auth/callback"
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": FRONTEND_BASE_URL,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return RedirectResponse(f"{STEAM_OPENID_URL}?{urlencode(params)}")


@router.get("/callback")
def callback(request: Request) -> RedirectResponse:
    params = dict(request.query_params)
    if params.get("openid.mode") != "id_res":
        raise HTTPException(status_code=400, detail="Invalid OpenID response")

    verify_params = dict(params)
    verify_params["openid.mode"] = "check_authentication"
    verify_resp = requests.post(STEAM_OPENID_URL, data=verify_params, timeout=15)
    if "is_valid:true" not in verify_resp.text:
        raise HTTPException(status_code=401, detail="OpenID verification failed")

    claimed_id = params.get("openid.claimed_id", "")
    match = CLAIMED_ID_RE.search(claimed_id)
    if not match:
        raise HTTPException(status_code=400, detail="Could not parse steamid from claimed_id")
    steamid = match.group(1)

    persona_name, avatar_url = _fetch_player_summary(steamid)

    conn = user_db.get_connection()
    user_db.upsert_user(conn, steamid, persona_name, avatar_url)
    token = user_db.create_session(conn, steamid)

    owned_games = _fetch_owned_games(steamid)
    if owned_games:
        user_db.save_owned_games(conn, steamid, owned_games)
    conn.close()

    redirect = RedirectResponse(FRONTEND_BASE_URL)
    redirect.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return redirect


@router.get("/me", response_model=User | None)
def me(session_token: str | None = Cookie(default=None)) -> User | None:
    if not session_token:
        return None
    conn = user_db.get_connection()
    steamid = user_db.get_steamid_from_session(conn, session_token)
    if not steamid:
        conn.close()
        return None
    row = conn.execute("SELECT * FROM users WHERE steamid = ?", (steamid,)).fetchone()
    owned_count = conn.execute(
        "SELECT COUNT(*) FROM owned_games WHERE steamid = ?", (steamid,)
    ).fetchone()[0]
    conn.close()
    if row is None:
        return None
    return User(
        steamid=row["steamid"],
        persona_name=row["persona_name"],
        avatar_url=row["avatar_url"],
        owned_games_count=owned_count,
    )


@router.post("/logout")
def logout(response: Response, session_token: str | None = Cookie(default=None)) -> dict:
    if session_token:
        conn = user_db.get_connection()
        user_db.delete_session(conn, session_token)
        conn.close()
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "ok"}


def _fetch_player_summary(steamid: str) -> tuple[str | None, str | None]:
    resp = requests.get(
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
        params={"key": STEAM_API_KEY, "steamids": steamid},
        timeout=15,
    )
    players = resp.json().get("response", {}).get("players", [])
    if not players:
        return None, None
    return players[0].get("personaname"), players[0].get("avatarfull")


def _fetch_owned_games(steamid: str) -> list[dict]:
    resp = requests.get(
        "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
        params={
            "key": STEAM_API_KEY,
            "steamid": steamid,
            "include_appinfo": 1,
            "include_played_free_games": 1,
        },
        timeout=30,
    )
    return resp.json().get("response", {}).get("games", [])
