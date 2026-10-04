import sqlite3

import pytest

from app.core import db as games_db

SECTIONS = ["trending", "new_release", "indie", "multiplayer"]


def ids(response):
    return [g["appid"] for g in response.json()]


# --- 기본 ---------------------------------------------------------------


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_get_game(client, add_game):
    add_game(
        1,
        name="A",
        genre="RPG",
        tags={"RPG": 120, "Indie": 80},
        price=999,
        positive=10,
        negative=1,
        review_pct=90,
        review_count=11,
        release_date="2024",
        sections="trending,indie",
    )
    r = client.get("/games/1")
    assert r.status_code == 200
    assert r.json() == {
        "appid": 1,
        "name": "A",
        "genre": "RPG",
        "tags": ["RPG", "Indie"],  # 태그 이름만, 투표 수는 버림
        "price": 999,
        "positive": 10,
        "negative": 1,
        "review_pct": 90,
        "review_count": 11,
        "release_date": "2024",
        "sections": ["trending", "indie"],
    }


def test_unknown_game_404(client):
    r = client.get("/games/999")
    assert r.status_code == 404
    assert r.json() == {"detail": "Game not found"}


def test_non_integer_appid_is_422(client):
    assert client.get("/games/abc").status_code == 422


@pytest.mark.parametrize("tags", [[], {}, None], ids=["empty-list", "empty-dict", "null"])
def test_game_with_empty_tags_does_not_500(client, add_game, tags):
    """SteamSpy는 태그가 없으면 {} 대신 [] (빈 리스트)를 주기도 한다 (games.py 주석)."""
    add_game(1, tags=tags)

    r = client.get("/games/1")
    assert r.status_code == 200
    assert r.json()["tags"] == []


def test_list_endpoint_survives_game_with_empty_list_tags(client, add_game):
    add_game(1, tags={"RPG": 1}, review_count=5)
    add_game(2, tags=[], review_count=10)

    r = client.get("/games/sections/trending")
    assert r.status_code == 200
    assert ids(r) == [2, 1]


# --- 섹션 ---------------------------------------------------------------


def test_invalid_section_404(client):
    r = client.get("/games/sections/nope")
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]


@pytest.mark.parametrize("section", SECTIONS)
def test_every_valid_section_is_served(client, add_game, section):
    add_game(1, sections=section)
    r = client.get(f"/games/sections/{section}")
    assert r.status_code == 200
    assert ids(r) == [1]


def test_section_sorted_by_review_count_desc(client, add_game):
    add_game(1, review_count=10)
    add_game(2, review_count=500)
    add_game(3, review_count=50)
    assert ids(client.get("/games/sections/trending")) == [2, 3, 1]


def test_section_excludes_inactive_games(client, add_game):
    """is_active=0은 목록에서 빠져야 한다 — 가장 인기 많은 게임이 비활성이어도 limit=1에 나오면 안 됨."""
    add_game(1, review_count=10)
    add_game(2, review_count=9999, is_active=0)

    assert ids(client.get("/games/sections/trending?limit=1&offset=0")) == [1]
    assert ids(client.get("/games/sections/trending")) == [1]


def test_section_limit_and_offset_page_through_results(client, add_game):
    for appid, count in [(1, 40), (2, 30), (3, 20), (4, 10)]:
        add_game(appid, review_count=count)

    assert ids(client.get("/games/sections/trending?limit=2&offset=0")) == [1, 2]
    assert ids(client.get("/games/sections/trending?limit=2&offset=2")) == [3, 4]
    assert ids(client.get("/games/sections/trending?limit=2&offset=4")) == []


def test_section_matches_whole_section_name_only(client, add_game):
    add_game(1, sections="indie", review_count=1)
    add_game(2, sections="indie_extra", review_count=2)  # 접두사가 같을 뿐 다른 섹션
    add_game(3, sections="not_indie", review_count=3)
    add_game(4, sections="trending,indie", review_count=4)  # 여러 섹션에 속함
    add_game(5, sections=None, review_count=5)  # 섹션 없음 → 어디에도 노출 안 됨

    assert ids(client.get("/games/sections/indie")) == [4, 1]
    assert ids(client.get("/games/sections/trending")) == [4]


def test_empty_section_returns_empty_list(client):
    r = client.get("/games/sections/trending")
    assert r.status_code == 200
    assert r.json() == []


# --- 유사 게임 ----------------------------------------------------------


def test_similar_ordered_and_limited(client, add_game, add_similar):
    add_game(1)
    for appid in (2, 3, 4):
        add_game(appid)
    add_similar(1, 2, rank=1, score=0.9)
    add_similar(1, 3, rank=2, score=0.8)
    add_similar(1, 4, rank=3, score=0.7)

    r = client.get("/games/1/similar?limit=5")
    assert r.status_code == 200
    assert ids(r) == [2, 3, 4]
    assert [g["score"] for g in r.json()] == [0.9, 0.8, 0.7]

    assert ids(client.get("/games/1/similar?limit=2")) == [2, 3]


def test_similar_order_follows_rank_not_insertion_or_score(client, add_game, add_similar):
    for appid in (1, 2, 3, 4):
        add_game(appid)
    add_similar(1, 4, rank=3, score=0.1)
    add_similar(1, 2, rank=1, score=0.9)
    add_similar(1, 3, rank=2, score=0.5)

    assert ids(client.get("/games/1/similar")) == [2, 3, 4]


def test_similar_defaults_to_ten(client, add_game, add_similar):
    add_game(1)
    for rank in range(1, 13):
        add_game(100 + rank)
        add_similar(1, 100 + rank, rank=rank, score=1 - rank / 100)

    assert len(client.get("/games/1/similar").json()) == 10


def test_similar_includes_game_fields_and_score(client, add_game, add_similar):
    add_game(1)
    add_game(2, name="Twin", tags={"RPG": 1}, review_count=7, sections="trending,indie")
    add_similar(1, 2, rank=1, score=0.75)

    (item,) = client.get("/games/1/similar").json()
    assert item["name"] == "Twin"
    assert item["tags"] == ["RPG"]
    assert item["sections"] == ["trending", "indie"]
    assert item["score"] == 0.75


def test_similar_for_game_without_results_is_empty_list(client, add_game):
    add_game(1)
    r = client.get("/games/1/similar")
    assert r.status_code == 200
    assert r.json() == []


# --- 읽기 전용 원칙 -----------------------------------------------------


def test_games_db_connection_is_read_only(db_path):
    """backend는 games.db를 절대 쓰지 않는다 (ai/backend 분리 원칙, core/db.py)."""
    conn = games_db.get_connection()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO similar_games VALUES (1, 2, 0.5, 1)")
    finally:
        conn.close()
