"""
Chat §6 — Attachments.

Files attached in chat: kept, read (OCR for images and scans), and served back to the app. (Split out of
interfaces/hud/chat.py; chat.py re-exports the names other code uses and includes this module's router.)
"""

import base64
import binascii
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core import documents, vision
from interfaces.hud import state
from interfaces.hud.models import AttachmentUpload

router = APIRouter()


@router.post("/api/chat/attachments")
def upload_attachment(req: AttachmentUpload):
    """Keeps a document and reads its text (OCR for images and scans). A plain def: OCR can take seconds."""
    try:
        data = base64.b64decode(req.data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="data_b64 isn't valid base64")
    try:
        record = documents.save(req.name, data, conversation_id=req.conversation_id)
    except documents.DocumentError as e:
        raise HTTPException(status_code=422, detail=str(e))
    rec = documents.load(record["id"]) or {}
    if record["kind"] in ("image", "video"):           # a scene note from the local vision model, in the background
        vision.enqueue(record["id"], state.get_memory())
    thumb_url = f"/api/chat/attachments/{record['id']}/thumb" if record.get("has_thumb") else None
    return {
        "status": "success",
        "attachment": {**record, "thumb_url": thumb_url},
        "preview": (rec.get("text") or "")[:300],
    }


@router.get("/api/chat/attachments/{attachment_id}/thumb")
def get_attachment_thumbnail(attachment_id: str):
    """Returns the visual thumbnail (thumb.jpg) for an attachment."""
    if not re.fullmatch(r"att-[0-9a-f]{12}", attachment_id or ""):
        raise HTTPException(status_code=404, detail="Attachment not found")
    thumb_path = documents.ATTACH_DIR / attachment_id / "thumb.jpg"
    if not thumb_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(thumb_path, media_type="image/jpeg")


@router.get("/api/chat/attachments/{attachment_id}/file")
def get_attachment_file(attachment_id: str):
    """Returns the original uploaded attachment file."""
    rec = documents.load(attachment_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Attachment not found")
    file_path = documents.ATTACH_DIR / attachment_id / rec["name"]
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(file_path, filename=rec["name"])


@router.get("/api/chat/attachments")
def list_attachments(conversation_id: str | None = None, limit: int = 10):
    items = documents.recent(conversation_id, max(1, min(limit, 50)))
    for a in items:
        if a.get("has_thumb") and "thumb_url" not in a:
            a["thumb_url"] = f"/api/chat/attachments/{a['id']}/thumb"
    return {"attachments": items}
