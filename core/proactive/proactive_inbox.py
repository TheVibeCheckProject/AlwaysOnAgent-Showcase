"""§4  THE INBOX AND FEEDBACK — split out of core/proactive.py on 2026-11-03.

One mixin per section: every method here stays a bound method of Proactive
(the class inherits this mixin), so all existing callers work unchanged.
"""
import datetime
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .proactive_helpers import _parse_iso
except ImportError:
    from core.proactive.proactive_helpers import _parse_iso


class _InboxMixin:
    # Interface this mixin requires from the host class (Proactive):
    # db / now are set by Proactive.__init__; add_exclusion lives on
    # _SettingsMixin (earlier in the MRO than this mixin, so this runtime
    # stub can never shadow its real implementation). Declared here so
    # mypy can see them.
    db: "DatabaseManager"
    now: Callable[[], datetime.datetime]

    def add_exclusion(self, topic: str) -> list[str]:
        raise NotImplementedError
    def _rows(self, since: datetime.datetime) -> list[dict[str, Any]]:
        rows = self.db.get_connection().execute(
            "SELECT * FROM proactive_messages ORDER BY id DESC LIMIT 200").fetchall()
        return [dict(r) for r in rows if (_parse_iso(r["created_at"]) or since) >= since]

    def inbox(self, after: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.get_connection().execute(
            "SELECT id, kind, text, created_at, canvas_json, why FROM proactive_messages WHERE id > ? "
            "AND NOT (kind = 'brief' AND COALESCE(topic, '') = 'today') ORDER BY id LIMIT ?",   # Today shows those
            (int(after or 0), int(limit))).fetchall()
        out = []
        for r in rows:
            m = {"id": r["id"], "kind": r["kind"], "text": r["text"], "created_at": r["created_at"]}
            if r["why"]:
                m["why"] = r["why"]
            if r["canvas_json"]:
                try:
                    m["canvas"] = json.loads(r["canvas_json"])        # DeskAI shows the draft card, like a chat reply's
                except ValueError:
                    pass
            out.append(m)
        return out

    def record_feedback(self, message_id: int, reaction: str, reason: str = "") -> dict[str, Any]:
        reaction = str(reaction).strip().lower()
        if reaction in ("thumbs_up", "up", "👍"):
            reaction = "thumbs_up"
        elif reaction in ("thumbs_down", "down", "👎"):
            reaction = "thumbs_down"
        elif reaction in ("less_like_this", "less"):
            reaction = "less_like_this"
        now = self.now()
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM proactive_messages WHERE id = ?", (int(message_id),)).fetchone()
            if not row:
                raise ValueError(f"message #{message_id} not found")
            conn.execute("UPDATE proactive_messages SET feedback = ?, feedback_at = ? WHERE id = ?",
                         (reaction, now.isoformat(), int(message_id)))
            cid = row["candidate_id"]
            if cid:
                conn.execute("UPDATE proactive_candidates SET outcome = ? WHERE id = ?", (reaction, cid))
            # If "less_like_this" or "thumbs_down", automatically add topic/subject to exclusions (1d)
            topic = (row["subject"] or row["topic"] or "").strip()
            if reaction in ("less_like_this", "thumbs_down") and topic:
                self.add_exclusion(topic)
        return {"id": int(message_id), "reaction": reaction, "recorded_at": now.isoformat()}

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
