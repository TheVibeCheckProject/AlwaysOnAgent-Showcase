"""Keeping attachments for documents.py §5
=======================================
Each attachment in its own folder with a JSON record; older than KEEP_DAYS, it's removed.

Split out of core/documents.py §5 on 2026-11-04; core/documents/__init__.py re-exports
the public names for backwards compatibility.
Moved into the core.documents package on 2026-11-04; the old
core/documents_<name>.py paths were thin shims, removed 2026-11-04.
"""

import datetime
import json
import logging
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("Documents")
MAX_BYTES = 15 * 1024 * 1024
KEEP_DAYS = 30


def _dc():
    """core.documents, looked up at call time so tests patching names on it take effect."""
    import core.documents
    return core.documents


# ---------------------------------------------------------------------------- storing
# Files the attachment folder holds beside the upload: an upload with one of these names was overwritten by it
# (2026-10-28 audit 1.6)
RESERVED_NAMES = {"meta.json", "thumb.jpg", "ai_view.jpg", "temp_frame.jpg"}


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §5  KEEPING ATTACHMENTS
#     Each attachment in its own folder with a JSON record; older than KEEP_DAYS, it's removed.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def _safe_name(name: str) -> str:
    base = Path(name or "file").name
    safe = re.sub(r"[^\w.\- ()]+", "_", base)[:120].strip(". ") or "file"
    return f"upload_{safe}" if safe.lower() in RESERVED_NAMES else safe


def save(name: str, data: bytes, conversation_id: str | None = None) -> dict[str, Any]:
    """Reads and keeps an uploaded file. Returns its record (without the text); raises DocumentError."""
    if not data:
        raise _dc().DocumentError("the file is empty")
    if len(data) > MAX_BYTES:
        raise _dc().DocumentError(f"the file is {len(data) / 1e6:.1f} MB; the limit is {MAX_BYTES // (1024 * 1024)} MB")
    name = _safe_name(name)
    started = time.time()
    info = _dc().extract(name, data)
    attachment_id = "att-" + secrets.token_hex(6)
    folder = _dc().ATTACH_DIR / attachment_id
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / name
    file_path.write_bytes(data)

    media_meta: dict[str, Any] = {}
    try:
        if info["kind"] == "image":
            media_meta = _dc().process_image_attachment(data, folder)
        elif info["kind"] == "pdf":
            media_meta = _dc().extract_pdf_cover(data, folder)
        elif info["kind"] == "video":
            media_meta = _dc().extract_video_thumbnail(file_path, folder)
        else:
            media_meta = _dc().generate_document_card(name, info["kind"], folder, preview_text=info.get("text", "")[:120])
    except Exception as e:
        logger.warning(f"Thumbnail generation error for {name}: {e}")

    record = {"id": attachment_id, "name": name, "bytes": len(data), "conversation_id": conversation_id,
              "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
              "seconds": round(time.time() - started, 1), "chars": len(info["text"]),
              **{k: v for k, v in info.items() if k != "text"},
              **media_meta}
    (folder / "meta.json").write_text(json.dumps({**record, "text": info["text"]}, ensure_ascii=False),
                                      encoding="utf-8")
    logger.info(f"Attachment {attachment_id}: {name} ({info['kind']}, {info['method']}, {record['chars']} chars, "
                f"{record['seconds']}s)")
    return record


def update(attachment_id: str, **fields) -> None:
    """Adds fields to an attachment's record (the scene note, or that it's been distilled into memory)."""
    rec = _dc().load(attachment_id)
    if rec is None:
        return
    rec.update(fields)
    path = _dc().ATTACH_DIR / attachment_id / "meta.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load(attachment_id: str) -> dict[str, Any] | None:
    """The record with its text, or None."""
    if not re.fullmatch(r"att-[0-9a-f]{12}", attachment_id or ""):
        return None
    try:
        return json.loads((_dc().ATTACH_DIR / attachment_id / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def recent(conversation_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """The newest attachments (records without text), optionally only one conversation's."""
    if not _dc().ATTACH_DIR.is_dir():
        return []
    items = []
    for folder in _dc().ATTACH_DIR.iterdir():
        rec = _dc().load(folder.name)
        if rec and (conversation_id is None or rec.get("conversation_id") == conversation_id):
            rec.pop("text", None)
            items.append(rec)
    items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return items[:limit]


def prune(max_age_days: int = KEEP_DAYS) -> int:
    """Removes attachments older than max_age_days. Returns how many."""
    if not _dc().ATTACH_DIR.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for folder in _dc().ATTACH_DIR.iterdir():
        try:
            if folder.is_dir() and folder.name.startswith("att-") and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder)
                removed += 1
        except OSError:
            pass
    return removed
