from pydantic import BaseModel


class Game(BaseModel):
    appid: int
    name: str
    genre: str | None = None
    tags: list[str] = []
    price: int | None = None
    positive: int | None = None
    negative: int | None = None
    review_pct: int | None = None
    review_count: int | None = None
    release_date: str | None = None
    sections: list[str] = []


class SimilarGame(Game):
    score: float
