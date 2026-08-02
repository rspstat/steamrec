from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.games import router as games_router
from app.api.users import router as users_router

app = FastAPI(title="steamrec API")
app.include_router(games_router)
app.include_router(auth_router)
app.include_router(users_router)


@app.get("/health")
def health():
    return {"status": "ok"}
