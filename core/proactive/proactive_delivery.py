"""§5  DELIVERING — split out of core/proactive.py on 2026-11-03.

One mixin per section: every method here stays a bound method of Proactive
(the class inherits this mixin), so all existing callers work unchanged.
"""
import datetime
import json
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.events import Presence

try:
    from .proactive_constants import (
        ACTIVE_WITHIN,
        LULL,
        OFFLINE_NOTICE_AFTER,
        OUTBOX_KEEP,
        OUTBOX_SPACING,
        PUSH_TEXT,
        logger,
    )
    from .proactive_helpers import _parse_iso
except ImportError:
    from core.proactive.proactive_constants import (
        ACTIVE_WITHIN,
        LULL,
        OFFLINE_NOTICE_AFTER,
        OUTBOX_KEEP,
        OUTBOX_SPACING,
        PUSH_TEXT,
        logger,
    )
    from core.proactive.proactive_helpers import _parse_iso


class _DeliveryMixin:
    # Host state (set on Proactive.__init__); annotation-only, no runtime value.
    db: "DatabaseManager"
    memory: Any
    now: Callable[[], datetime.datetime]
    presence: "Presence | None"
    send_push: Callable[..., int] | None

    if TYPE_CHECKING:
        # _last_message_at lives on _PoolMixin, which comes AFTER _DeliveryMixin
        # in the Proactive MRO: declared for mypy only — a runtime stub here
        # would shadow the real method in the MRO.
        def _last_message_at(self, roles=("user", "assistant")) -> datetime.datetime | None:
            ...

    def owner_around(self, now: datetime.datetime) -> bool:
        """The app is open or was in the last ACTIVE_WITHIN, or he wrote that recently."""
        if self.presence is not None:
            try:
                if self.presence.is_open() or self.presence.away_seconds() <= ACTIVE_WITHIN.total_seconds():
                    return True
            except Exception as e:
                logger.debug(f"presence check failed: {e}")
        last = self._last_message_at(("user",))
        return last is not None and now - last <= ACTIVE_WITHIN

    def deliver(self, kind: str, text: str, topic: str = "", canvas: dict[str, Any] | None = None,
                why: str = "", subject: str = "", candidate_id: int | None = None,
                urgent: bool = False) -> dict[str, Any]:
        """Sends it now, or, when it isn't urgent and Alex isn't around, holds it in the outbox: the cycle releases
        held messages one at a time when he is (release_outbox). Every sender goes through here, so this is the one
        gate: until 2026-10-27 the away shift's open loops, the research digest and the review sent straight away at
        any hour (a question at 00:47). Urgent: a watched email, an answer he asked for, a time-sensitive notice.
        A held message comes back as {"id": None, "held": True, "outbox_id": ...}."""
        text = (text or "").strip()
        now = self.now()
        if not urgent and kind != "brief" and self.presence is not None and not self.owner_around(now):
            with self.db.get_connection() as conn:
                cur = conn.execute(
                    "INSERT INTO proactive_outbox (kind, text, topic, canvas_json, why, subject, candidate_id, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (kind, text, topic[:80], json.dumps(canvas) if canvas else None, (why or "")[:300] or None,
                     (subject or "")[:120] or None, candidate_id, now.isoformat()))
            logger.info(f"Proactive {kind} held until Alex is around ({topic or 'no topic'})")
            return {"id": None, "held": True, "outbox_id": cur.lastrowid, "kind": kind, "text": text, "topic": topic}
        with self.db.get_connection() as conn:
            cur = conn.execute("INSERT INTO proactive_messages (kind, text, topic, created_at, canvas_json, why, subject, "
                               "candidate_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (kind, text, topic[:80], self.now().isoformat(), json.dumps(canvas) if canvas else None,
                                (why or "")[:300] or None, (subject or "")[:120] or None, candidate_id))
        msg = {"id": cur.lastrowid, "kind": kind, "text": text, "topic": topic, **({"canvas": canvas} if canvas else {})}
        if self.memory is not None:
            try:
                self.memory.add_message("assistant", text)          # a reply then has its context
            except (AttributeError, sqlite3.Error) as e:
                logger.warning(f"couldn't add the message to the conversation: {e}")
        if self.send_push is not None:
            title, body = PUSH_TEXT.get(kind, PUSH_TEXT["notice"])
            try:
                self.send_push("agent_message", title, body, channel="assistant",
                               extra={"message_id": str(msg["id"]), "kind": kind})
            except Exception as e:
                logger.warning(f"push for proactive message failed: {e}")
        logger.info(f"Proactive {kind} #{msg['id']} sent ({topic or 'no topic'})")
        return msg

    def held(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.get_connection().execute(
            "SELECT * FROM proactive_outbox ORDER BY id").fetchall()]

    def release_outbox(self, now: datetime.datetime) -> list[dict[str, Any]]:
        """One held message, when Alex is around, no conversation is going on (LULL) and the last proactive message
        is OUTBOX_SPACING old: what piled up while he was away comes one at a time, not as a burst of pushes."""
        rows = self.held()
        if not rows:
            return []
        conn = self.db.get_connection()
        for r in rows:
            if now - (_parse_iso(r["created_at"]) or now) > OUTBOX_KEEP:
                with conn:
                    conn.execute("DELETE FROM proactive_outbox WHERE id = ?", (r["id"],))
                logger.info(f"held {r['kind']} dropped: older than {OUTBOX_KEEP.days} days ({r['topic']})")
        rows = self.held()
        if not rows or not self.owner_around(now):
            return []
        last_any = self._last_message_at()
        if last_any is not None and now - last_any < LULL:
            return []
        last_sent = conn.execute("SELECT MAX(created_at) FROM proactive_messages").fetchone()[0]
        if last_sent and now - (_parse_iso(last_sent) or now) < OUTBOX_SPACING:
            return []
        r = rows[0]
        canvas = json.loads(r["canvas_json"]) if r["canvas_json"] else None
        msg = self.deliver(r["kind"], r["text"], r["topic"] or "", canvas=canvas, why=r["why"] or "",
                           subject=r["subject"] or "", candidate_id=r["candidate_id"], urgent=True)
        with conn:
            conn.execute("DELETE FROM proactive_outbox WHERE id = ?", (r["id"],))
        return [msg]

    def offline_notice(self, last_heartbeat: str) -> dict[str, Any] | None:
        """After the agent (or the Mini) was off for OFFLINE_NOTICE_AFTER or more: one message saying when, and which
        scheduled runs it missed. Missed runs are skipped, never made up later (schedules.MISSED_GRACE), so without
        this he'd never know (2026-10-17 to 22: off 5 days after a power cut; 10 posts and 10 pins missed, silently)."""
        then, now = _parse_iso(last_heartbeat), self.now()
        if then is None or now - then < OFFLINE_NOTICE_AFTER:
            return None
        try:
            try:
                from core.schedules import MISSED_GRACE, ScheduleStore, parse
            except ImportError:
                from schedules import MISSED_GRACE, ScheduleStore, parse  # type: ignore[no-redef]
            missed = []
            # Cron works in naive local time (schedules.Cron.next_after)
            start, end = then.astimezone().replace(tzinfo=None), (now - MISSED_GRACE).astimezone().replace(tzinfo=None)
            for s in ScheduleStore(self.db).list():
                if not s.get("enabled"):
                    continue
                n, t = 0, start
                while n < 500:
                    t = parse(s["cron"]).next_after(t)
                    if t >= end:                         # due within the grace: the schedule runs it now
                        break
                    n += 1
                if n:
                    missed.append(f"{s['name']} ×{n}")
        except Exception as e:
            logger.warning(f"offline notice: couldn't count missed runs: {e}")
            missed = []
        gap = now - then
        took = (f"{gap.days} day{'s' if gap.days != 1 else ''} {gap.seconds // 3600} h" if gap.days
                else f"{gap.seconds // 3600} h {gap.seconds % 3600 // 60} min")
        text = (f"⚡ I was off from {then:%a %b %d %H:%M} to {now:%a %b %d %H:%M} ({took}), probably a power cut or "
                "restart. " + (f"Missed while off: {', '.join(missed)}. Missed runs aren't made up later."
                               if missed else "No scheduled runs were missed."))
        return self.deliver("notice", text, "offline", why="The Mini's heartbeat had a gap")

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
