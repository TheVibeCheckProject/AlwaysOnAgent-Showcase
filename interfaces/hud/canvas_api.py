"""Canvas: the owner's drafts (core/canvas.py), listed and previewed for DeskAI's canvas screen."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from core.canvas import PREVIEW_CSP, Canvas, CanvasError
from interfaces.hud import state

router = APIRouter()


@router.get("/api/canvas")
def list_canvases(limit: int = 20):
    return {"canvases": Canvas(state.db).list(limit=max(1, min(limit, 100)))}


@router.get("/api/canvas/{doc_id}")
def get_canvas(doc_id: int, version: Optional[int] = None):
    """The draft's details and version list (the content itself is shown through /preview)."""
    try:
        doc = Canvas(state.db).get(doc_id, version)
    except CanvasError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {k: doc[k] for k in ("id", "title", "kind", "version", "latest", "versions", "created_at", "updated_at")}


@router.get("/api/canvas/{doc_id}/preview", response_class=HTMLResponse)
def preview_canvas(doc_id: int, version: Optional[int] = None):
    """The draft as a page. Written by an AI, so nothing in it may run: no scripts, no requests but styles,
    fonts and images, and a sandboxed origin that can't reach this API."""
    try:
        page = Canvas(state.db).render(doc_id, version)
    except CanvasError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return HTMLResponse(page, headers={"Content-Security-Policy": PREVIEW_CSP, "X-Content-Type-Options": "nosniff",
                                       "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
