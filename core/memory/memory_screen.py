"""
The Memory screen for the AlwaysOnAgent assistant.

Split out of core/memory.py §10 on 2026-11-03; MemoryManager inherits
these methods via _MemoryScreenMixin, so list_facts() / set_pinned() /
_mark_dirty() are still bound methods on the manager.
CATEGORIES lives in core/memory/memory_text.py (§1); set_fact_fields imports it lazily
inside the method so this module has no circular import back to memory.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core.database import get_iso_now
except ImportError:
    from database import get_iso_now  # type: ignore[no-redef]

class _MemoryScreenMixin:
    # Host state set by MemoryManager.__init__ (core/memory/manager.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime.
    db: "DatabaseManager"  # quoted: imported only under TYPE_CHECKING
    conv: str

    # get_fact and _fact_rows live on _FactsMixin, which precedes this mixin in
    # MemoryManager's MRO, so these runtime stubs never shadow the real methods.
    def get_fact(self, fact_id: int, follow_superseded: bool = True) -> dict[str, Any] | None:
        raise NotImplementedError

    def _fact_rows(self, where: str = "status = 'active' AND invalid_from IS NULL",
                   params: Sequence[Any] = ()) -> list[Any]:
        raise NotImplementedError

    def set_fact_fields(self, fact_id: int, importance: int | None = None, category: str | None = None,
                        status: str | None = None) -> bool:
        """Edits a fact's metadata (from the HUD). status: 'active' restores a faded fact."""
        try:
            from core.memory import CATEGORIES  # lazy: avoids a circular import at module load
        except ImportError:
            from memory import CATEGORIES  # type: ignore[no-redef]
        sets: list[str] = []
        params: list[Any] = []
        if importance is not None:
            sets.append("importance = ?")
            params.append(int(min(5, max(1, importance))))
        if category is not None and category in CATEGORIES:
            sets.append("category = ?")
            params.append(category)
        if status in ("active", "faded"):
            sets.append("status = ?")
            params.append(status)
        if not sets:
            return bool(self.get_fact(fact_id))
        conn = self.db.get_connection()
        with conn:
            cur = conn.execute(f"UPDATE mem_facts SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
                               params + [get_iso_now(), fact_id])
        if cur.rowcount:
            self._mark_dirty()
        return bool(cur.rowcount)

    def list_messages(self, before_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """A page of the conversation, oldest first, ending just before before_id (newest page by default)."""
        sql = "SELECT id, role, content, created_at, compressed FROM mem_messages WHERE conv = ?"
        params: list[Any] = [self.conv]
        if before_id:
            sql += " AND id < ?"
            params.append(before_id)
        rows = self.db.get_connection().execute(sql + " ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()
        return [dict(r) for r in reversed(rows)]

    def list_summaries(self) -> list[dict[str, Any]]:
        """The compressed history currently in use (not yet rolled into a higher level), newest first."""
        return [dict(r) for r in self.db.get_connection().execute(
            "SELECT id, level, content, start_at, end_at, tokens FROM mem_summaries "
            "WHERE conv = ? AND rolled_up = 0 ORDER BY start_msg_id DESC", (self.conv,)
        ).fetchall()]

    def set_pinned(self, fact_id: int, pinned: bool) -> bool:
        conn = self.db.get_connection()
        with conn:
            cur = conn.execute("UPDATE mem_facts SET pinned = ?, updated_at = ? WHERE id = ?",
                               (int(pinned), get_iso_now(), fact_id))
        if cur.rowcount:
            self._mark_dirty()
        return bool(cur.rowcount)

    def list_facts(self, limit: int = 50, category: str | None = None, include_faded: bool = False) -> list[dict[str, Any]]:
        where = "1=1" if include_faded else "status = 'active'"
        params: list[Any] = []
        if category:
            where += " AND category = ?"
            params.append(category)
        return [dict(r) for r in self._fact_rows(where, params)[:limit]]

    def _mark_dirty(self) -> None:
        self.db.set_state("memory_profile_dirty", "1")
