"""§6  THE CYCLE — split out of core/proactive.py on 2026-11-03.

One mixin per section: every method here stays a bound method of Proactive
(the class inherits this mixin), so all existing callers work unchanged.
"""
import datetime
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.candidates import CandidatePool
    from core.database import DatabaseManager
    from core.open_loops import OpenLoops

try:
    from .proactive_constants import NOW_PER_CYCLE, logger
except ImportError:
    from core.proactive.proactive_constants import NOW_PER_CYCLE, logger


class _CycleMixin:
    # Host state (set on Proactive.__init__); annotation-only, no runtime value.
    db: "DatabaseManager"
    llm: Any
    now: Callable[[], datetime.datetime]
    open_loops: "OpenLoops | None"
    pool: "CandidatePool"

    # Methods on earlier mixins in the Proactive MRO (Settings, Inbox, Delivery):
    # runtime stubs are safe — the real method wins in the MRO.
    def settings(self) -> dict[str, str]:
        raise NotImplementedError

    def quiet(self, t: datetime.datetime) -> bool:
        raise NotImplementedError

    def is_excluded(self, text: str) -> bool:
        raise NotImplementedError

    def switch(self, name: str) -> bool:
        raise NotImplementedError

    def _rows(self, since: datetime.datetime) -> list[dict[str, Any]]:
        raise NotImplementedError

    def release_outbox(self, now: datetime.datetime) -> list[dict[str, Any]]:
        raise NotImplementedError

    if TYPE_CHECKING:
        # Methods on later mixins in the Proactive MRO (Pool, Signals, Brief,
        # Notices, Questions): declared for mypy only — a runtime stub here
        # would shadow the real method in the MRO.
        def _brief_step(self, now: datetime.datetime, today: list[dict[str, Any]],
                        s: dict[str, str]) -> dict[str, Any] | None:
            ...

        def _send_from_pool(self, now: datetime.datetime, today: list[dict[str, Any]],
                            room: int) -> list[dict[str, Any]]:
            ...

        def _signals(self, now: datetime.datetime) -> list[dict[str, str]]:
            ...

        def _decide(self, signals, today, room: int, now) -> list[dict[str, str]]:
            ...

        def _question_allowed(self, now, today) -> bool:
            ...

        def _question_gen_due(self, now: datetime.datetime) -> bool:
            ...

        def _question(self, now) -> dict[str, str] | None:
            ...

        def _record_question_gen(self, now: datetime.datetime, cid: int | None) -> None:
            ...

    def run_cycle(self) -> list[dict[str, Any]]:
        now = self.now()
        self.db.set_state("proactive_last_run", now.isoformat())
        s = self.settings()
        if s["enabled"] != "1" or self.quiet(now) or self.llm is None:
            return []
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today = self._rows(start_of_day)
        room = int(s["daily_cap"]) - len(today)
        sent: list[dict[str, Any]] = []
        if room <= 0:
            return sent
        self.pool.update_outcomes()

        brief = self._brief_step(now, today, s)
        if brief:
            sent.append(brief)
            room -= 1
        if room > 0:
            released = self.release_outbox(now)          # one held message, if he's around and the timing is right
            sent += released
            room -= len(released)

        # Open loops before the rest: what they send is about the owner's own pending matters
        # The daytime check uses only cheap evidence (docs/PROACTIVE_RESEARCH.md 1a, 1o)
        added, room, today = self._cycle_open_loops(now, room, today, start_of_day)
        sent += added

        # Everything else goes through the candidate pool (docs/PROACTIVE_RESEARCH.md 1b): made, scored, then sent
        # only at a good moment. Signals are time-sensitive ("now"); a curious question waits for one.
        self._cycle_signals(now, room, today)
        self._cycle_curiosity(now, room, today)
        if room > 0:
            sent += self._send_from_pool(now, today, room)
        return sent

    def _cycle_open_loops(self, now, room, today, start_of_day):
        added: list[dict[str, Any]] = []
        if room > 0 and self.switch("open_loops") and self.open_loops is not None and self.open_loops.due(now):
            try:
                out = self.open_loops.run(now, room=room, may_ask=self._question_allowed(now, today),
                                          include_research=False)
                added += out["sent"]
                room -= len(out["sent"])
                today = self._rows(start_of_day)
            except Exception as e:
                logger.warning(f"open loop check failed: {e}")
        return added, room, today

    def _cycle_signals(self, now, room, today) -> None:
        signals = self._signals(now)
        if signals and room > 0:
            for m in self._decide(signals, today, min(room, NOW_PER_CYCLE), now):
                if not self.is_excluded(m.get("topic") or "") and not self.is_excluded(m.get("text") or ""):
                    self.pool.add("signal", m["kind"], m["text"], topic=m.get("topic", ""), urgency="now",
                                  why=m.get("why", ""))

    def _cycle_curiosity(self, now, room, today) -> None:
        if (room > 0 and self.switch("curiosity") and self._question_allowed(now, today)
                and not self.pool.pending(urgency="whenever", limit=1) and self._question_gen_due(now)):
            q = self._question(now)
            cid = None
            if q:
                cid = self.pool.add("curiosity", "question", q["question"], topic=q.get("subject") or q.get("topic", ""),
                                    about=q.get("about", ""), why=q.get("why", ""))
            self._record_question_gen(now, cid)

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
