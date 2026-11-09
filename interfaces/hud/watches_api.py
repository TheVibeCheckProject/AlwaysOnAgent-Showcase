"""Watches (core/watches.py, docs/WATCHES.md: mail, web pages, web searches, the calendar) for DeskAI's Watches tab:
see them, add one by hand, narrow one, stop one. The owner asked for it on 2026-11-03, beside Schedules in the hub. The same rules as in chat: a plain
search every few minutes, no AI; a watch made here marks what's already in the mailbox as seen."""

from typing import Any

from fastapi import APIRouter, HTTPException

from core import watches
from interfaces.hud import state
from interfaces.hud.models import WatchCreateRequest, WatchPatchRequest

router = APIRouter()


def _store() -> watches.Watches:
    return watches.Watches(state.db)


def _view(w: dict[str, Any]) -> dict[str, Any]:
    """A watch for the app: its fields, a plain description, and how it's doing (never the seen-email list)."""
    keys = ("id", "kind", "source", "target", "sender", "subject", "words", "gone", "ask", "every_minutes",
            "lead_minutes", "once", "active", "matches", "created_at", "last_check", "last_match", "last_error",
            "deadline", "note", "everything")
    out = {k: w.get(k) for k in keys}
    out["kind"] = out["kind"] or "mail"
    out["once"], out["active"], out["every_email"] = bool(w.get("once")), bool(w.get("active")), bool(w.get("everything"))
    out.pop("everything")
    out["description"] = watches.Watches.describe(w).split(": ", 1)[-1]
    return out


def _note(text: str) -> None:
    """The assistant remembers watch changes made in the app, like schedule changes."""
    try:
        state.get_memory().record_event(text)
    except Exception as e:
        state.logger.warning(f"Could not record the watch change in memory: {e}")


@router.get("/api/watches")
def list_watches(include_stopped: bool = False):
    return {"watches": [_view(w) for w in _store().list(include_stopped=include_stopped)],
            "sources": list(watches.SOURCES), "every_minutes": watches.WATCH_EVERY_MINUTES,
            "kinds": list(watches.KINDS), "default_every_minutes": watches.EVERY}


@router.post("/api/watches")
def create_watch(req: WatchCreateRequest):
    what = {"mail": "search that mailbox", "page": "open that page", "search": "run that search",
            "calendar": "read the calendar"}[req.kind]
    try:
        if req.kind == "mail":
            w = _store().add(req.source, req.sender, req.subject, req.words, req.once, deadline=req.deadline,
                             note=req.note, everything=req.every_email)
        elif req.kind == "calendar":
            w = _store().add_calendar(req.words, req.lead_minutes, req.once)
        else:
            w = _store().add_web(req.kind, req.target, req.words, req.gone, req.ask, req.every_minutes, req.once)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # The mailbox, page, search or calendar couldn't be reached (not connected, 404): nothing was saved
        raise HTTPException(status_code=502, detail=f"Couldn't {what}, so nothing was saved: {e}")
    view = _view(w)
    _note(f"The user added watch #{w['id']} in DeskAI: {view['description']}.")
    return {"status": "success", "watch": view}


@router.patch("/api/watches/{watch_id}")
def update_watch(watch_id: int, req: WatchPatchRequest):
    try:
        w = _store().change(watch_id, req.sender, req.subject, req.words, req.once)
    except ValueError as e:
        raise HTTPException(status_code=404 if "no active watch" in str(e) else 400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Couldn't search that mailbox, so nothing was changed: {e}")
    view = _view(w)
    _note(f"The user changed watch #{watch_id} in DeskAI: now {view['description']}.")
    return {"status": "success", "watch": view}


@router.delete("/api/watches/{watch_id}")
def stop_watch(watch_id: int):
    w = _store().get(watch_id)
    if w is None or not _store().stop(watch_id):
        raise HTTPException(status_code=404, detail="No active watch with that number")
    _note(f"The user stopped watch #{watch_id} in DeskAI ({_view(w)['description']}).")
    return {"status": "success", "id": watch_id}
