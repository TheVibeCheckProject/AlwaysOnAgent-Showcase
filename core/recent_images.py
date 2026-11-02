"""
Short-term memory for image requests
====================================
The owner wanted image prompts kept out of long-term memory (no archive, no facts pulled from them) but still
reachable for follow-ups like "enhance my last prompt" or "make that one more cinematic". So each /image request
is kept here instead: the newest KEEP requests, for up to KEEP_HOURS, in system_state (it survives restarts).
The chat assistant reads it with the recent_image_prompts tool.
"""

import datetime
import json
from typing import Any, Dict, List

try:
    from core.database import DatabaseManager, get_iso_now
except ImportError:
    from database import DatabaseManager, get_iso_now

STATE_KEY = "recent_images"
KEEP = 10
KEEP_HOURS = 24


def _load(db: DatabaseManager) -> List[Dict[str, Any]]:
    try:
        items = json.loads(db.get_state(STATE_KEY, "[]") or "[]")
        return items if isinstance(items, list) else []
    except ValueError:
        return []


def _fresh(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cutoff = (datetime.datetime.now().astimezone() - datetime.timedelta(hours=KEEP_HOURS)).isoformat()
    return [i for i in items if str(i.get("at", "")) >= cutoff]


def remember(db: DatabaseManager, prompt: str, *, enhanced: str = "", style: str = "",
             model: str = "", filename: str = "", ok: bool = True) -> None:
    """Records one image request (newest first). Never raises: this is a convenience, not a record."""
    try:
        entry = {"at": get_iso_now(), "prompt": (prompt or "")[:1000], "enhanced": (enhanced or "")[:1500],
                 "style": style or "", "model": model or "", "filename": filename or "",
                 "ok": bool(ok)}
        items = [entry] + _fresh(_load(db))
        db.set_state(STATE_KEY, json.dumps(items[:KEEP], ensure_ascii=False))
    except Exception:
        pass


def recent(db: DatabaseManager, limit: int = 3) -> List[Dict[str, Any]]:
    """The newest image requests from the last KEEP_HOURS, newest first."""
    return _fresh(_load(db))[:max(1, min(int(limit or 3), KEEP))]
