from pydantic import BaseModel


class User(BaseModel):
    steamid: str
    persona_name: str | None = None
    avatar_url: str | None = None
    owned_games_count: int = 0
