"""§7  THE CANDIDATE POOL AND TIMING — split out of core/proactive.py on 2026-11-03.

One mixin per section: every method here stays a bound method of Proactive
(the class inherits this mixin), so all existing callers work unchanged.
"""
import datetime
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.candidates import CandidatePool
    from core.database import DatabaseManager

try:
    from core.candidates import SOURCES_THAT_MAY_REPEAT, Scorer, Similarity, discussed, novelty
    from core.llm_usage import usage_purpose

    from .proactive_constants import (
        ACTIVE_WITHIN,
        LULL,
        NOW_PER_CYCLE,
        SILENCE_CHECKIN_BACKOFF_1,
        SILENCE_CHECKIN_BACKOFF_2,
        SILENCE_CHECKIN_INITIAL,
        SILENCE_STEP,
        STATE_RANKED_UPTO,
        STATE_SILENCE_CHECKIN_AT,
        STATE_SILENCE_LEVEL,
        THRESHOLD_FLOOR,
        THRESHOLD_STEP,
        logger,
    )
    from .proactive_helpers import _parse_iso
except ImportError:
    from candidates import SOURCES_THAT_MAY_REPEAT, Scorer, Similarity, discussed, novelty  # type: ignore[no-redef]
    from llm_usage import usage_purpose  # type: ignore[no-redef]

    from core.proactive.proactive_constants import (
        ACTIVE_WITHIN,
        LULL,
        NOW_PER_CYCLE,
        SILENCE_CHECKIN_BACKOFF_1,
        SILENCE_CHECKIN_BACKOFF_2,
        SILENCE_CHECKIN_INITIAL,
        SILENCE_STEP,
        STATE_RANKED_UPTO,
        STATE_SILENCE_CHECKIN_AT,
        STATE_SILENCE_LEVEL,
        THRESHOLD_FLOOR,
        THRESHOLD_STEP,
        logger,
    )
    from core.proactive.proactive_helpers import _parse_iso


class _PoolMixin:
    # Host state (set on Proactive.__init__); annotation-only, no runtime value.
    db: "DatabaseManager"
    memory: Any
    llm: Any
    now: Callable[[], datetime.datetime]
    pool: "CandidatePool"
    _sim: Similarity | None
    _scorer: Scorer | None

    # Methods on earlier mixins in the Proactive MRO (Settings, Inbox,
    # Delivery, Cycle): runtime stubs are safe — the real method wins in the MRO.
    def is_excluded(self, text: str) -> bool:
        raise NotImplementedError

    def switch_for_source(self, source: str | None) -> bool:
        raise NotImplementedError

    def quiet(self, t: datetime.datetime) -> bool:
        raise NotImplementedError

    def _rows(self, since: datetime.datetime) -> list[dict[str, Any]]:
        raise NotImplementedError

    def deliver(self, kind: str, text: str, topic: str = "", canvas: dict[str, Any] | None = None,
                why: str = "", subject: str = "", candidate_id: int | None = None,
                urgent: bool = False) -> dict[str, Any]:
        raise NotImplementedError

    if TYPE_CHECKING:
        # Methods on later mixins in the Proactive MRO (Signals, Brief,
        # Notices, Questions): declared for mypy only — a runtime stub here
        # would shadow the real method in the MRO.
        def _facts(self, dated_only: bool = False, limit: int = 40) -> list[str]:
            ...

        @classmethod
        def _violates_guardrails(cls, text: str, recent_owner_text: str = "") -> str | None:
            ...

        def _effective_questions_per_day(self, now: datetime.datetime) -> int:
            ...

        def _question_allowed(self, now, today) -> bool:
            ...

    def _embed(self, texts: list[str]):
        return self.memory._embed(texts, wait=True) if getattr(self.memory, "embedder", None) else None

    def _similarity(self) -> Similarity:
        if self._sim is None:
            self._sim = Similarity(self._embed if getattr(self.memory, "embedder", None) else None)
        return self._sim

    def _past_subjects(self) -> list[str]:
        """Subjects of every question ever asked and of every message of the last 30 days (what novelty compares)."""
        since = (self.now() - datetime.timedelta(days=30)).isoformat()
        return [r["subject"] for r in self.db.get_connection().execute(
            "SELECT subject FROM proactive_messages WHERE subject IS NOT NULL AND subject != '' "
            "AND (kind = 'question' OR created_at >= ?)", (since,)).fetchall()]

    def _owner_messages_since(self, since_iso: str) -> list[str]:
        try:
            return [r["content"] or "" for r in self.db.get_connection().execute(
                "SELECT content FROM mem_messages WHERE role = 'user' AND created_at > ? ORDER BY id DESC LIMIT 100",
                (since_iso,)).fetchall()]
        except (AttributeError, sqlite3.Error):
            return []

    def _last_sent_source(self) -> str | None:
        r = self.db.get_connection().execute(
            "SELECT source FROM proactive_candidates WHERE status = 'sent' ORDER BY sent_at DESC, id DESC LIMIT 1").fetchone()
        return r["source"] if r else None

    def _scorer_for(self) -> Scorer:
        if self._scorer is None:
            def chat(messages):
                with usage_purpose("proactive"):
                    return self.llm.chat(messages, max_tokens=500, temperature=0.2, json_mode=True, timeout=40,
                                         models=getattr(self.llm, "BACKGROUND_MODELS", None))
            self._scorer = Scorer(self.pool, self._similarity(), chat, past_subjects=self._past_subjects,
                                  owner_messages=self._owner_messages_since, facts=lambda: self._facts(limit=200),
                                  last_sent_source=self._last_sent_source)
        return self._scorer

    def _last_message_at(self, roles=("user", "assistant")) -> datetime.datetime | None:
        try:
            r = self.db.get_connection().execute(
                f"SELECT MAX(created_at) FROM mem_messages WHERE role IN ({','.join('?' * len(roles))})",
                tuple(roles)).fetchone()
        except (AttributeError, sqlite3.Error):
            return None
        return _parse_iso(r[0]) if r and r[0] else None

    def threshold(self, now: datetime.datetime, source: str | None = None) -> float:
        """The score a "whenever" candidate needs: candidates.THRESHOLD, lower the longer nothing was sent (as in
        Inner Thoughts: the urge to speak grows with silence), adjusted by implicit feedback (1e), never below THRESHOLD_FLOOR."""
        try:
            from core.candidates import THRESHOLD
        except ImportError:
            from candidates import THRESHOLD  # type: ignore[no-redef]
        last = self.db.get_connection().execute(
            "SELECT MAX(created_at) FROM proactive_messages WHERE kind IN ('notice', 'suggestion', 'question')").fetchone()[0]
        quiet = now - (_parse_iso(last) or now)
        steps = int(quiet / SILENCE_STEP) if quiet > datetime.timedelta(0) else 0
        base = max(THRESHOLD_FLOOR, THRESHOLD - THRESHOLD_STEP * steps)
        adj = self.pool.threshold_adjustment(source) if source else 0.0
        return round(max(THRESHOLD_FLOOR, min(0.85, base + adj)), 3)

    def _good_moment(self, now: datetime.datetime) -> bool:
        """A lull (nothing either way for LULL) while he's been around (ACTIVE_WITHIN): never cut into a
        conversation, never out of the blue (docs/PROACTIVE_RESEARCH.md 1n)."""
        last_any, last_owner = self._last_message_at(), self._last_message_at(("user",))
        if last_any is not None and now - last_any < LULL:
            return False
        return last_owner is not None and now - last_owner <= ACTIVE_WITHIN

    def _silence_level(self) -> int:
        last_owner = self._last_message_at(("user",))
        last_checkin_str = self.db.get_state(STATE_SILENCE_CHECKIN_AT)
        if not last_checkin_str:
            return 0
        last_checkin = _parse_iso(last_checkin_str)
        if last_owner is not None and last_checkin is not None and last_owner > last_checkin:
            self.db.set_state(STATE_SILENCE_LEVEL, "0")
            return 0
        try:
            return int(self.db.get_state(STATE_SILENCE_LEVEL, "0") or 0)
        except (ValueError, TypeError):
            return 0

    def _silence_checkin_due(self, now: datetime.datetime) -> bool:
        """Due when Alex has sent nothing for 6 hours (outside quiet hours), backing off to 12 then 24 hours
        if ignored, reset by any message from him (docs/PROACTIVE_RESEARCH.md 1k; checklist 1.7)."""
        if self.quiet(now):
            return False
        last_any, last_owner = self._last_message_at(), self._last_message_at(("user",))
        if last_any is not None and now - last_any < LULL:
            return False
        if last_owner is None:
            return False
        level = self._silence_level()
        last_checkin_str = self.db.get_state(STATE_SILENCE_CHECKIN_AT)
        last_checkin = _parse_iso(last_checkin_str) if last_checkin_str else None

        if level == 0:
            return (now - last_owner) >= SILENCE_CHECKIN_INITIAL
        elif level == 1:
            if last_checkin is None:
                return (now - last_owner) >= SILENCE_CHECKIN_INITIAL
            return (now - last_checkin) >= SILENCE_CHECKIN_BACKOFF_1
        else:
            if last_checkin is None:
                return (now - last_owner) >= SILENCE_CHECKIN_INITIAL
            return (now - last_checkin) >= SILENCE_CHECKIN_BACKOFF_2

    def _record_silence_checkin(self, now: datetime.datetime) -> None:
        level = self._silence_level()
        self.db.set_state(STATE_SILENCE_CHECKIN_AT, now.isoformat())
        self.db.set_state(STATE_SILENCE_LEVEL, str(level + 1))

    def _still_fresh(self, c: dict[str, Any]) -> str | None:
        """Just before sending, with no AI call: why it's no longer worth saying, or None."""
        if not self.switch_for_source(c.get("source")):
            return f"switch for source '{c.get('source')}' is off"
        if self.is_excluded(c.get("topic") or "") or self.is_excluded(c.get("text") or ""):
            return "excluded by owner preference"
        viol = self._violates_guardrails(c.get("text") or "")
        if viol:
            return viol
        subject = c.get("topic") or ""
        if not subject or c["source"] in SOURCES_THAT_MAY_REPEAT:
            return None
        sim = self._similarity()
        nov = novelty(sim, subject, self._past_subjects())
        if nov["repeat"]:
            return f"repeat of {nov['nearest'][:80]!r}"
        # His messages since it was made, and in the 24 hours before (as the scorer counts them, 1c)
        since = (_parse_iso(c["created_at"]) or self.now()) - datetime.timedelta(hours=24)
        disc = discussed(sim, subject, self._owner_messages_since(since.isoformat()), self._facts(limit=200))
        if disc["discussed"]:
            return f"discussed lately ({disc['by']})"
        return None

    def _send_from_pool(self, now: datetime.datetime, today: list[dict[str, Any]], room: int) -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        self._pool_rank()
        pending = self.pool.pending(limit=30)
        # 1. Time-sensitive: now, within the cap
        sent, room = self._pool_send_now(pending, room, sent)
        if room <= 0:
            return sent
        # 2. One "whenever" (or a "today" the brief didn't carry) at a good moment, or as a silence check-in
        sent, room = self._pool_send_whenever(now, today, pending, room, sent)
        return sent

    def _pool_rank(self) -> None:
        newest = self.db.get_connection().execute(
            "SELECT MAX(id) FROM proactive_candidates WHERE status = 'pending'").fetchone()[0] or 0
        if newest > int(self.db.get_state(STATE_RANKED_UPTO, "0") or 0):
            # One ranking call, only when the pool has something the ranker hasn't seen
            self._scorer_for().score_pending()
            self.db.set_state(STATE_RANKED_UPTO, str(newest))
        else:
            self.pool.expire()

    def _pool_send_now(self, pending, room, sent):
        for c in [c for c in pending if c["urgency"] == "now"][:NOW_PER_CYCLE]:
            if room <= 0:
                return sent, room
            if not self.switch_for_source(c.get("source")):
                continue
            if self.is_excluded(c.get("topic") or "") or self.is_excluded(c.get("text") or ""):
                self.pool.drop(c["id"], "excluded by owner preference")
                continue
            sent.append(self._send(c))
            room -= 1
        return sent, room

    def _pool_send_whenever(self, now, today, pending, room, sent):
        is_good = self._good_moment(now)
        is_silence = self._silence_checkin_due(now)
        if not is_good and not is_silence:
            return sent, room

        candidates = [c for c in pending if c["urgency"] != "now"]
        if is_silence:
            # Silence check-in takes the highest-scored candidate from the pool (1k)
            candidates.sort(key=lambda c: (c.get("score") is None, -(c.get("score") or 0.0), c["id"]))
        for c in candidates:
            bar = self.threshold(now, source=c.get("source"))
            if c.get("score") is None or c["score"] < bar:
                continue
            if c["kind"] == "question":
                if is_good and not self._question_allowed(now, self._rows(
                        now.replace(hour=0, minute=0, second=0, microsecond=0))):
                    continue
                if is_silence and len([m for m in today if m["kind"] == "question"]) >= self._effective_questions_per_day(now):
                    continue
            stale = self._still_fresh(c)
            if stale:
                self.pool.drop(c["id"], stale)
                logger.info(f"candidate #{c['id']} dropped before sending: {stale}")
                continue
            sent.append(self._send(c))
            if is_silence:
                self._record_silence_checkin(now)
            break
        return sent, room

    def _send(self, c: dict[str, Any]) -> dict[str, Any]:
        # The pool already picked the moment (a "now" signal, or a lull while he's around): not held again
        msg = self.deliver(c["kind"], c["text"], c.get("topic") or c["source"], why=c.get("why") or "",
                           subject=c.get("topic") or "", candidate_id=c["id"], urgent=True)
        self.pool.mark_sent(c["id"], msg.get("id"))
        return msg

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
