"""Split out of core/away_shift.py §8 on 2026-11-03; core/away_shift/__init__.py re-exports these names.
Not repeating itself: subjects already covered, new material, findings seen.
"""
import datetime
import json
import logging
import re
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .away_shift_constants import (
        COVERED_DAYS_ASKED,
        COVERED_DAYS_DROPPED,
        GEN_NEW_FACTS,
        GEN_NEW_MESSAGES,
        GEN_WAIT_AFTER_EMPTY,
        STATE_GEN_PREFIX,
    )
    from .away_shift_dates import STATE_SEEN_FINDINGS, _parse_iso
except ImportError:
    from core.away_shift.away_shift_constants import (
        COVERED_DAYS_ASKED,
        COVERED_DAYS_DROPPED,
        GEN_NEW_FACTS,
        GEN_NEW_MESSAGES,
        GEN_WAIT_AFTER_EMPTY,
        STATE_GEN_PREFIX,
    )
    from core.away_shift.away_shift_dates import STATE_SEEN_FINDINGS, _parse_iso

logger = logging.getLogger("AlwaysOnAgent.AwayShift")


class _DedupMixin:
    # Host-class state (set by AwayShift.__init__); annotation-only so the mixin type-checks.
    db: "DatabaseManager"

    def _covered_subjects(self, cur: datetime.datetime, limit: int = 40) -> list[str]:
        """Subjects already asked or sent (COVERED_DAYS_ASKED) and candidates dropped lately (COVERED_DAYS_DROPPED), for
        the generators' "don't suggest these" list (the cheap form of Inner Thoughts' originality)."""
        conn = self.db.get_connection()
        asked = (cur - datetime.timedelta(days=COVERED_DAYS_ASKED)).isoformat()
        dropped = (cur - datetime.timedelta(days=COVERED_DAYS_DROPPED)).isoformat()
        out: list[str] = []
        try:
            rows = conn.execute("SELECT subject FROM proactive_messages WHERE subject IS NOT NULL AND subject != '' "
                                "AND created_at >= ? ORDER BY id DESC", (asked,)).fetchall()
            rows += conn.execute("SELECT topic FROM proactive_candidates WHERE topic IS NOT NULL AND topic != '' "
                                 "AND created_at >= ? ORDER BY id DESC", (dropped,)).fetchall()
        except sqlite3.Error as e:
            logger.debug(f"covered subjects: {e}")
            return []
        for (s,) in rows:
            if s and s.lower() not in {o.lower() for o in out}:
                out.append(s)
        return out[:limit]

    def _material_mark(self) -> dict[str, int]:
        conn = self.db.get_connection()
        try:
            msg = conn.execute("SELECT COALESCE(MAX(id), 0) FROM mem_messages WHERE role = 'user'").fetchone()[0]
            fact = conn.execute("SELECT COALESCE(MAX(id), 0) FROM mem_facts").fetchone()[0]
        except sqlite3.Error as e:
            logger.debug(f"material mark: {e}")
            msg, fact = 0, 0
        return {"msg": int(msg), "fact": int(fact)}

    def _generator_ready(self, step: str, source: str, cur: datetime.datetime) -> bool:
        """Whether a generator should run: yes the first time, when there's new material since its last run, or when
        its last batch produced something worth keeping; after an all-dropped batch it waits GEN_WAIT_AFTER_EMPTY."""
        try:
            st = json.loads(self.db.get_state(STATE_GEN_PREFIX + step, "") or "{}")
        except ValueError:
            st = {}
        at = _parse_iso(st.get("at") or "")
        if at is None:
            return True
        mark = self._material_mark()
        # A few new facts, not one (2026-10-28 audit 0.7)
        if mark["msg"] >= int(st.get("msg", 0)) + GEN_NEW_MESSAGES or mark["fact"] >= int(st.get("fact", 0)) + GEN_NEW_FACTS:
            return True
        kept = self.db.get_connection().execute(
            "SELECT COUNT(*) FROM proactive_candidates WHERE source = ? AND created_at >= ? AND status IN "
            "('pending', 'sent')", (source, at.isoformat())).fetchone()[0]
        if kept:
            return True
        wait = GEN_WAIT_AFTER_EMPTY[min(int(st.get("empty", 0)), len(GEN_WAIT_AFTER_EMPTY) - 1)]
        return cur - at >= wait

    def _mark_generated(self, step: str, source: str, cur: datetime.datetime) -> None:
        """Records this run; counts consecutive batches that were all dropped (they lengthen the wait)."""
        try:
            st = json.loads(self.db.get_state(STATE_GEN_PREFIX + step, "") or "{}")
        except ValueError:
            st = {}
        empty = 0
        at = _parse_iso(st.get("at") or "")
        if at is not None:
            kept = self.db.get_connection().execute(
                "SELECT COUNT(*) FROM proactive_candidates WHERE source = ? AND created_at >= ? AND status IN "
                "('pending', 'sent')", (source, at.isoformat())).fetchone()[0]
            empty = 0 if kept else int(st.get("empty", 0)) + 1
        self.db.set_state(STATE_GEN_PREFIX + step, json.dumps({"at": cur.isoformat(), **self._material_mark(),
                                                               "empty": empty}))

    # ------------------------------------------------------------------------------------------- step helpers

    @staticmethod
    def _link_key(url: str) -> str:
        u = re.sub(r"^https?://(www\.)?", "", (url or "").strip().lower()).split("#")[0]
        return re.sub(r"[?&](utm_[a-z]+|ref|fbclid|gclid)=[^&]*", "", u).rstrip("/?&")

    def _seen_findings(self) -> list[str]:
        try:
            seen = json.loads(self.db.get_state(STATE_SEEN_FINDINGS, "[]") or "[]")
        except ValueError:
            return []
        return seen if isinstance(seen, list) else []
