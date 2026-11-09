"""
Stats for the AlwaysOnAgent assistant.

Split out of core/memory.py §17 on 2026-11-03; MemoryManager inherits
this method via _StatsMixin, so stats() is still a bound method on
the manager.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager


class _StatsMixin:
    # Host state, set by MemoryManager.__init__ (annotation-only: no runtime
    # value, so nothing here shadows a sibling mixin's attribute).
    db: "DatabaseManager"
    conv: str
    embedder: Any

    if TYPE_CHECKING:
        # Real method on _PromptMixin (earlier in the MRO); declared here
        # only so mypy sees it, never at runtime.
        def get_profile(self) -> str: ...

    def stats(self) -> dict[str, Any]:
        conn = self.db.get_connection()
        one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]
        return {
            "facts_active": one("SELECT COUNT(*) FROM mem_facts WHERE status = 'active' AND invalid_from IS NULL"),
            "facts_superseded": one("SELECT COUNT(*) FROM mem_facts WHERE status = 'superseded' OR invalid_from IS NOT NULL"),
            "facts_faded": one("SELECT COUNT(*) FROM mem_facts WHERE status = 'faded'"),
            "facts_pinned": one("SELECT COUNT(*) FROM mem_facts WHERE pinned = 1"),
            "messages": one("SELECT COUNT(*) FROM mem_messages WHERE conv = ?", self.conv),
            "recent_tokens": one("SELECT COALESCE(SUM(tokens), 0) FROM mem_messages WHERE conv = ? AND compressed = 0", self.conv),
            "summaries": one("SELECT COUNT(*) FROM mem_summaries WHERE conv = ? AND rolled_up = 0", self.conv),
            "summary_levels": one("SELECT COALESCE(MAX(level), 0) FROM mem_summaries WHERE conv = ?", self.conv),
            "profile_built": bool(self.get_profile()),
            "semantic_search": bool(self.embedder and self.embedder.ready),
        }
