"""When to research: check dates, research cadence, cheap signals that make it due sooner (core/open_loops.py §6).

Split out of core/open_loops.py on 2026-11-03; moved into core/open_loops/ on 2026-11-04. The methods are
unchanged apart from being dedented one level, and the names they use are imported from the sibling modules below.
"""

import datetime
from typing import TYPE_CHECKING, Any

from .open_loops_constants import CADENCE_MAX, CADENCE_NEAR, CADENCE_NORMAL, NEAR_WINDOW_DAYS, logger
from .open_loops_helpers import _norm, _parse_day, _parse_iso, _queries


class _TimingMixin:
    # Host-class state set in OpenLoops.__init__ (core/open_loops/__init__.py).
    # Annotation-only declaration: no runtime value exists, so nothing here can
    # shadow anything at runtime.
    connectors: Any | None

    # _conn is defined on OpenLoops itself and _mail on _CheckingMixin, both of
    # which come after this mixin in the MRO, so these live under TYPE_CHECKING
    # only: a runtime stub would shadow the real methods.
    if TYPE_CHECKING:
        def _conn(self) -> Any: ...
        def _mail(self, loop: dict[str, Any], since: str) -> list[dict[str, Any]]: ...

    @staticmethod
    def is_near_check_after(loop: dict[str, Any], now: datetime.datetime) -> bool:
        day = _parse_day(loop.get("check_after"))
        if day is None:
            return False
        return (day - now.date()).days <= NEAR_WINDOW_DAYS

    @classmethod
    def research_cadence(cls, loop: dict[str, Any], now: datetime.datetime) -> datetime.timedelta:
        base = CADENCE_NEAR if cls.is_near_check_after(loop, now) else CADENCE_NORMAL
        quiet = int(loop.get("quiet_runs") or 0)
        return min(CADENCE_MAX, base * (2 ** min(quiet, 3)))

    def _cheap_flagged(self, fact: dict[str, Any], loop: dict[str, Any], since_iso: str) -> bool:
        """True if a cheap signal arrived since since_iso: new matching email, Alex mentioning it, or a newer fact about it."""
        words = {w for w in _norm(f"{loop.get('waiting_on', '')} {fact['content']}").split() if len(w) > 3}
        asked = loop.get("asked_at")
        # 1. Alex mentioning it in chat since since_iso (or replying to an asked question)
        for r in self._conn().execute(
                "SELECT content, created_at FROM mem_messages WHERE role = 'user' AND created_at > ? ORDER BY id DESC LIMIT 50",
                (since_iso,)).fetchall():
            if (asked and r["created_at"] >= asked) or (words & set(_norm(r["content"] or "").split())):
                return True
        # 2. A newer fact about it saved since since_iso
        for r in self._conn().execute(
                "SELECT content FROM mem_facts WHERE status = 'active' AND invalid_from IS NULL AND id != ? "
                "AND created_at > ? ORDER BY id DESC LIMIT 20",
                (fact["id"], since_iso)).fetchall():
            if words & set(_norm(r["content"] or "").split()):
                return True
        # 3. New email matching its mail words since since_iso
        if loop.get("mail") and self.connectors is not None:
            try:
                emails = self._mail(loop, since=since_iso)
                if emails:
                    return True
            except Exception as e:
                logger.debug(f"open loops: mail flag check failed: {e}")
        return False

    def research_due(self, loop: dict[str, Any], fact: dict[str, Any], now: datetime.datetime) -> bool:
        """Whether web research should run for this loop (docs/PROACTIVE_RESEARCH.md 1o):
        True if never researched yet, or 48h passed (24h within 3 days of check_after),
        or flagged by cheap signals (new matching email, Alex mentioning it, newer fact)."""
        if not _queries(loop.get("research")):
            return False
        researched_at_str = loop.get("researched_at")
        if not researched_at_str:
            return True
        researched_at = _parse_iso(researched_at_str)
        if researched_at is None:
            return True
        cadence = self.research_cadence(loop, now)
        if now - researched_at >= cadence:
            return True
        return bool(self._cheap_flagged(fact, loop, researched_at_str))
