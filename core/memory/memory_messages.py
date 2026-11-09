"""
Messages for the AlwaysOnAgent assistant.

Split out of core/memory.py §7 on 2026-11-03; MemoryManager inherits
these methods via _MessagesMixin, so add_message() / _recent_rows() /
_away_block() are still bound methods on the manager.
"""

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

    from core.database import DatabaseManager

try:
    from core.config import redact_secrets
    from core.database import get_iso_now

    from .memory_text import _vec_to_blob, count_tokens
except ImportError:
    from config import redact_secrets  # type: ignore[no-redef]
    from database import get_iso_now  # type: ignore[no-redef]

    from .memory_text import _vec_to_blob, count_tokens

class _MessagesMixin:
    # Host state, set by MemoryManager.__init__ (core/memory/manager.py).
    db: "DatabaseManager"
    conv: str

    def _embed(self, texts: list[str], query: bool = False, wait: bool = False) -> "np.ndarray | None":
        # Real implementation on _EmbeddingsMixin (earlier in the MRO).
        raise NotImplementedError

    def add_message(self, role: str, content: str, conv: str | None = None) -> int:
        content = redact_secrets(content or "").strip()
        vec = self._embed([content], wait=False)
        conn = self.db.get_connection()
        with conn:
            cur = conn.execute(
                "INSERT INTO mem_messages (conv, role, content, created_at, tokens, embedding, embedded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conv or self.conv, role, content, get_iso_now(), count_tokens(content),
                 _vec_to_blob(vec[0]) if vec is not None else None, time.time() if vec is not None else None)
            )
        assert cur.lastrowid is not None  # INSERT always sets it
        return cur.lastrowid

    def record_event(self, text: str) -> int:
        """Something that happened outside the chat (a task finished, a video was posted)."""
        return self.add_message("event", text)

    def note_capabilities(self, summary: str) -> bool:
        """
        Records an event when the assistant's abilities change. Placed after any older messages in
        which it said it couldn't do something, it outweighs them (the model otherwise stays
        consistent with its own outdated statements). Returns True if an event was recorded.
        """
        import hashlib
        digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
        if self.db.get_state("assistant_capabilities_hash", "") == digest:
            return False
        self.record_event("The assistant was updated. " + summary)
        self.db.set_state("assistant_capabilities_hash", digest)
        return True

    def _recent_rows(self, conv: str | None = None, events: bool = True) -> list[Any]:
        """The uncompressed messages, oldest first. events=False: the conversation only, without what background work
        recorded (tasks finished, schedules run, image notes): those went into the verbatim window and the summaries
        and on the Mini were 33% of the history's tokens (2026-10-28 audit 7.1); they're shown in a short "while you
        were away" block instead (_away_block), and the archive keeps them for search."""
        return self.db.get_connection().execute(
            "SELECT id, role, content, created_at, tokens FROM mem_messages "
            "WHERE conv = ? AND compressed = 0" + ("" if events else " AND role != 'event'") + " ORDER BY id",
            (conv or self.conv,)
        ).fetchall()

    AWAY_EVENTS = 6           # events shown in the "while you were away" block
    AWAY_EVENT_CHARS = 200    # each, cut to this

    def _away_block(self) -> str:
        """What background work recorded since the user's previous message, one line each (newest last)."""
        rows = self._recent_rows()
        users = [i for i, r in enumerate(rows) if r["role"] == "user"]
        since = users[-2] if len(users) >= 2 else -1          # the current message is the last user row
        events = [r for r in rows[since + 1:] if r["role"] == "event"][-self.AWAY_EVENTS:]
        if not events:
            return ""
        def one_line(text: str) -> str:
            text = " ".join(str(text or "").split())
            return text if len(text) <= self.AWAY_EVENT_CHARS else text[:self.AWAY_EVENT_CHARS - 1] + "…"
        lines = [f"- [{r['created_at'][:16]}] {one_line(r['content'])}" for r in events]
        return "### While you were away (background work since the user's last message)\n" + "\n".join(lines)
