"""
The candidate pool: everything the agent might say on its own, before it's said (docs/PROACTIVE_RESEARCH.md 1b)
================================================================================================================
Until 2026-10-13 a proactive message was made at the moment it was sent: one AI call per chance, no second choice.
The research (Inner Thoughts, CHI 2025; Horvitz's mixed-initiative principles; ChatGPT Pulse) points the other
way: think of several things in the background, then voice only the best, at a good moment, and let the rest lapse.

Every candidate is one row: where it came from (`source`), what kind of message it would be, its text, one line
of why (shown to Alex with the message), what it's about (`about`, `ref`), how urgent it is, its scores, when it
expires, and what became of it (`status`, `reason`, and later `outcome`: answered or ignored). Kept after it's sent
or dropped: the log is how the thresholds get tuned (ProactiveBench's lesson).

This module is only the store. Scoring lives in core/candidates/candidates_scoring.py (steps 1.4, 1.5);
routing and sending live in core/proactive/ (1.6).

MAP (2026-11-04 package; every name below is re-exported here, so existing imports keep working):
  - Novelty / "already discussed" (Similarity, novelty, discussed, backfill_subjects, ...) -> core/candidates/candidates_novelty.py
  - Scoring (Scorer, RANK_WEIGHT, THRESHOLD, ...) -> core/candidates/candidates_scoring.py
  - The store itself (CandidatePool, SOURCES, EXPIRES, ...) lives here, in core/candidates/__init__.py.
  - The old flat paths (core/candidates.py, core/candidates_novelty.py,
    core/candidates_scoring.py) were thin shims re-exporting from this package; they were
    removed 2026-11-04, so import from this package.
"""

import datetime
import json
import logging
import sqlite3
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("AlwaysOnAgent.Candidates")

SOURCES = ("signal", "open_loop", "curiosity", "interest", "pattern", "research")
KINDS = ("notice", "question", "suggestion")
URGENCIES = ("now", "today", "whenever")
STATUSES = ("pending", "sent", "dropped", "lapsed")
# How long a candidate stays worth saying (docs/PROACTIVE_RESEARCH.md 1c): news goes stale fast, a curious thought
# keeps for a week
EXPIRES = {
    "signal": datetime.timedelta(days=1),
    "open_loop": datetime.timedelta(days=2),
    "research": datetime.timedelta(days=3),
    "curiosity": datetime.timedelta(days=7),
    "interest": datetime.timedelta(days=3),
    "pattern": datetime.timedelta(days=7),
}
MAX_TEXT, MAX_WHY, MAX_ABOUT = 1200, 300, 400
REPLY_WINDOW = datetime.timedelta(hours=6)


def _parse_iso(value: str) -> datetime.datetime | None:
    try:
        t = datetime.datetime.fromisoformat(str(value))
        return t if t.tzinfo else t.astimezone()
    except (TypeError, ValueError):
        return None


def _now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


class CandidatePool:
    def __init__(self, db, now: Callable[[], datetime.datetime] = _now):
        self.db, self.now = db, now
        with db.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS proactive_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    why TEXT,
                    about TEXT,
                    topic TEXT,                     -- its subject in a few words: what novelty compares (1.4)
                    ref TEXT,                       -- what it's about: "fact:38", "loop:38", "email:<id>"
                    urgency TEXT NOT NULL DEFAULT 'whenever',
                    scores TEXT,                    -- JSON: {"rank", "novelty", "recent", "variety", ...}
                    score REAL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reason TEXT,                    -- why it was dropped or lapsed
                    message_id INTEGER,
                    sent_at TEXT,
                    outcome TEXT                    -- answered | ignored (after it was sent)
                );
                CREATE INDEX IF NOT EXISTS idx_proactive_candidates ON proactive_candidates (status, urgency);
            """)
            # The full link of a finding, for its Today card (core/today/, 2026-10-28); ref keeps only a short key
            if "url" not in {r[1] for r in conn.execute("PRAGMA table_info(proactive_candidates)")}:
                conn.execute("ALTER TABLE proactive_candidates ADD COLUMN url TEXT")

    def _conn(self):
        return self.db.get_connection()

    # ------------------------------------------------------------------------------------------- writing
    def add(self, source: str, kind: str, text: str, why: str = "", about: str = "", topic: str = "",
            ref: str = "", urgency: str = "whenever", expires_in: datetime.timedelta | None = None,
            url: str = "") -> int:
        """Stores a candidate and returns its id. Raises ValueError for an unknown source, kind or urgency, or
        empty text: a bad candidate is a bug in its maker, not something to send."""
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        if urgency not in URGENCIES:
            raise ValueError(f"unknown urgency {urgency!r}")
        text = (text or "").strip()
        if not text:
            raise ValueError("empty candidate text")
        now = self.now()
        expires = now + (expires_in if expires_in is not None else EXPIRES[source])
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO proactive_candidates (source, kind, text, why, about, topic, ref, urgency, created_at, "
                "expires_at, status, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (source, kind, text[:MAX_TEXT], " ".join((why or "").split())[:MAX_WHY],
                 " ".join((about or "").split())[:MAX_ABOUT], (topic or "")[:80], (ref or "")[:120], urgency,
                 now.isoformat(), expires.isoformat(),
                 url.strip()[:600] if (url or "").strip().lower().startswith(("http://", "https://")) else None))
        return cur.lastrowid

    def set_score(self, cid: int, score: float, scores: dict[str, Any] | None = None) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE proactive_candidates SET score = ?, scores = ? WHERE id = ?",
                         (float(score), json.dumps(scores or {}, separators=(",", ":")), cid))

    def mark_sent(self, cid: int, message_id: int | None) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE proactive_candidates SET status = 'sent', message_id = ?, sent_at = ? "
                         "WHERE id = ? AND status = 'pending'", (message_id, self.now().isoformat(), cid))

    def drop(self, cid: int, reason: str) -> None:
        """Not sent, and never will be (a repeat, excluded, already discussed, low score...); kept with its reason."""
        with self._conn() as conn:
            conn.execute("UPDATE proactive_candidates SET status = 'dropped', reason = ? "
                         "WHERE id = ? AND status = 'pending'", ((reason or "")[:200], cid))

    def set_outcome(self, cid: int, outcome: str) -> None:
        if outcome not in ("answered", "ignored", "thumbs_up", "thumbs_down", "less_like_this"):
            raise ValueError(f"unknown outcome {outcome!r}")
        with self._conn() as conn:
            conn.execute("UPDATE proactive_candidates SET outcome = ? WHERE id = ? AND status = 'sent'",
                         (outcome, cid))

    def update_outcomes(self, has_replied: Callable[[str, str], bool] | None = None) -> dict[str, int]:
        """A reply within 6 hours marks a message answered; no reply marks it ignored (docs/PROACTIVE_RESEARCH.md 1e).
        Checks sent candidates whose outcome isn't known yet. Returns {'answered': N, 'ignored': M}."""
        now = self.now()
        answered_n, ignored_n = 0, 0
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, sent_at FROM proactive_candidates WHERE status = 'sent' AND outcome IS NULL AND sent_at IS NOT NULL"
        ).fetchall()
        for r in rows:
            cid, sent_at_str = r["id"], r["sent_at"]
            sent_at = _parse_iso(sent_at_str)
            if not sent_at:
                continue
            deadline = sent_at + REPLY_WINDOW
            if has_replied is not None:
                replied = has_replied(sent_at.isoformat(), min(now, deadline).isoformat())
            else:
                try:
                    res = conn.execute(
                        "SELECT 1 FROM mem_messages WHERE role = 'user' AND created_at > ? AND created_at <= ? LIMIT 1",
                        (sent_at.isoformat(), min(now, deadline).isoformat())).fetchone()
                    replied = bool(res)
                except sqlite3.Error as e:
                    logger.debug(f"candidates: outcome reply check failed: {e}")
                    replied = False

            if replied:
                self.set_outcome(cid, "answered")
                answered_n += 1
            elif now >= deadline:
                self.set_outcome(cid, "ignored")
                ignored_n += 1
        return {"answered": answered_n, "ignored": ignored_n}

    def reply_rate(self, source: str | None = None, days: int = 30) -> float | None:
        """The reply rate (answered / (answered + ignored)) for a source (or all sources) over recent days.
        Returns None if no messages have an outcome yet."""
        since = (self.now() - datetime.timedelta(days=days)).isoformat()
        sql = ("SELECT outcome, COUNT(*) FROM proactive_candidates "
               "WHERE status = 'sent' AND outcome IN ('answered', 'ignored') AND created_at >= ?")
        params: list[Any] = [since]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " GROUP BY outcome"
        counts = {r[0]: r[1] for r in self._conn().execute(sql, params).fetchall()}
        total = counts.get("answered", 0) + counts.get("ignored", 0)
        if total == 0:
            return None
        return round(counts.get("answered", 0) / total, 3)

    def threshold_adjustment(self, source: str | None = None) -> float:
        """Threshold adjustment from implicit feedback (1c, 1e):
        - Rises when reply rate is low (< 50%) or if the last sent candidate was ignored.
        - Falls slightly when reply rate is consistently high (>= 80%)."""
        rate = self.reply_rate(source)
        adj = 0.0
        if rate is not None:
            if rate < 0.5:
                adj += round((0.5 - rate) * 0.3, 3)
            elif rate >= 0.8:
                adj -= round((rate - 0.8) * 0.1, 3)
        sql = "SELECT outcome FROM proactive_candidates WHERE status = 'sent' AND outcome IS NOT NULL"
        params: list[Any] = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY sent_at DESC, id DESC LIMIT 1"
        last = self._conn().execute(sql, params).fetchone()
        if last and last["outcome"] == "ignored":
            adj += 0.05
        return round(adj, 3)

    def expire(self) -> int:
        """Pending candidates past their expiry lapse quietly. Returns how many."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE proactive_candidates SET status = 'lapsed', reason = 'expired' "
                               "WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at <= ?",
                               (self.now().isoformat(),))
        return cur.rowcount or 0

    # ------------------------------------------------------------------------------------------- reading
    @staticmethod
    def _row(r) -> dict[str, Any]:
        d = dict(r)
        try:
            d["scores"] = json.loads(d.get("scores") or "{}")
        except ValueError:
            d["scores"] = {}
        return d

    def get(self, cid: int) -> dict[str, Any] | None:
        r = self._conn().execute("SELECT * FROM proactive_candidates WHERE id = ?", (cid,)).fetchone()
        return self._row(r) if r else None

    def pending(self, urgency: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Unexpired pending candidates: "now" first, then by score (unscored last), oldest first."""
        sql = "SELECT * FROM proactive_candidates WHERE status = 'pending' AND (expires_at IS NULL OR expires_at > ?)"
        params: list[Any] = [self.now().isoformat()]
        if urgency:
            sql += " AND urgency = ?"
            params.append(urgency)
        sql += (" ORDER BY CASE urgency WHEN 'now' THEN 0 WHEN 'today' THEN 1 ELSE 2 END, "
                "score IS NULL, score DESC, id LIMIT ?")
        params.append(int(limit))
        return [self._row(r) for r in self._conn().execute(sql, params).fetchall()]

    def recent(self, days: int = 30, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        since = (self.now() - datetime.timedelta(days=days)).isoformat()
        sql = "SELECT * FROM proactive_candidates WHERE created_at >= ?"
        params: list[Any] = [since]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [self._row(r) for r in self._conn().execute(sql, params).fetchall()]

    def counts(self, days: int = 7) -> dict[str, Any]:
        """For the weekly numbers (1.15): by status, by source, and outcomes of what was sent."""
        since = (self.now() - datetime.timedelta(days=days)).isoformat()
        conn = self._conn()
        by = lambda col, extra="": {r[0]: r[1] for r in conn.execute(
            f"SELECT {col}, COUNT(*) FROM proactive_candidates WHERE created_at >= ? {extra} GROUP BY {col}",
            (since,)).fetchall()}
        return {"status": by("status"), "source": by("source"),
                "outcome": by("outcome", "AND status = 'sent' AND outcome IS NOT NULL"),
                "dropped_reason": by("reason", "AND status = 'dropped'")}


# --------------------------------------------------------------------------- re-exports
# The novelty/scoring sections live in .candidates_novelty / .candidates_scoring
# and are re-exported here, so existing imports keep working.
from .candidates_novelty import (
    ANSWERED,
    ANSWERED_DAYS,
    DISCUSSED,
    MIN_WORDS,
    SAME_SUBJECT,
    STOP,
    SUBJECTS_PROMPT,
    Similarity,
    backfill_subjects,
    discussed,
    distinctive_words,
    novelty,
)
from .candidates_scoring import (
    MAX_RANKED,
    NOVELTY_FULL,
    NOVELTY_WEIGHT,
    RANK_PROMPT,
    RANK_WEIGHT,
    SOURCES_THAT_MAY_REPEAT,
    THRESHOLD,
    VARIETY_PENALTY,
    Scorer,
)

__all__ = [
    "ANSWERED", "ANSWERED_DAYS", "DISCUSSED", "EXPIRES", "KINDS", "MAX_ABOUT",
    "MAX_RANKED", "MAX_TEXT", "MAX_WHY", "MIN_WORDS", "NOVELTY_FULL",
    "NOVELTY_WEIGHT", "RANK_PROMPT", "RANK_WEIGHT", "REPLY_WINDOW",
    "SAME_SUBJECT", "SOURCES", "SOURCES_THAT_MAY_REPEAT", "STATUSES", "STOP",
    "SUBJECTS_PROMPT", "THRESHOLD", "URGENCIES", "VARIETY_PENALTY",
    "CandidatePool", "Scorer", "Similarity", "backfill_subjects", "discussed",
    "distinctive_words", "novelty",
]
