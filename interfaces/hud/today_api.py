"""Today: DeskAI's edition screen (core/today.py). The edition, reacting to a card, and a card's kept image."""

import sqlite3

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.config import WORKSPACE_DIR
from core.today import ACTIONS, RECIPE_MAX_CHARS, Today, llm_asker, recipe, save_recipe
from interfaces.hud import state

router = APIRouter()
TODAY_IMAGES_DIR = WORKSPACE_DIR / "data" / "today_images"

_today: Today | None = None
_today_db = None


def _location() -> str:
    try:
        from core.proactive import Proactive
        return Proactive(state.db).settings().get("location") or ""
    except (ImportError, OSError, sqlite3.Error):
        return ""


def get_today() -> Today:
    """One per database (the tests swap the database)."""
    global _today, _today_db
    if _today is None or _today_db is not state.db:
        _today, _today_db = Today(state.db, TODAY_IMAGES_DIR, location=_location), state.db
    return _today


class TodayReaction(BaseModel):
    action: str = Field(..., min_length=1, max_length=20)


class TodayRecipe(BaseModel):
    text: str = Field("", max_length=RECIPE_MAX_CHARS)


def _set_location(town: str) -> None:
    from core.proactive import Proactive
    Proactive(state.db).change(location=town)


@router.get("/api/today/recipe")
def today_get_recipe():
    return recipe(state.db)


@router.put("/api/today/recipe")
def today_put_recipe(req: TodayRecipe):
    """Alex's words for Today -> rules (one AI call). 503 when the AI service didn't answer: nothing is changed."""
    try:
        return save_recipe(state.db, req.text, llm_asker(getattr(state.get_memory(), "llm", None)), _set_location)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/today")
def today_edition(edition: int | None = None, refresh: bool = False):
    # A plain def: building an edition reads pages and images for a few seconds (FastAPI's thread pool)
    return get_today().edition(edition, refresh=refresh)


@router.get("/api/today/saved")
def today_saved():
    return {"items": get_today().saved()}


@router.post("/api/today/items/{item_id}/react")
def today_react(item_id: int, req: TodayReaction):
    try:
        return {"item": get_today().react(item_id, req.action)}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"action is one of {', '.join(ACTIONS)}")
    except KeyError:
        raise HTTPException(status_code=404, detail="No such card")


@router.get("/api/today/items/{item_id}/image")
def today_image(item_id: int):
    path = get_today().image_path(item_id)
    if path is None:
        raise HTTPException(status_code=404, detail="This card has no image")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
