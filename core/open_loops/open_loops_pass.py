"""A pass: run(): tag, close, check a few, ask about one that's overdue (core/open_loops.py §8).

Split out of core/open_loops.py on 2026-11-03; moved into core/open_loops/ on 2026-11-04. The methods are
unchanged apart from being dedented one level, and the names they use are imported from the sibling modules below.
"""

import datetime
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

from .open_loops_constants import ASK_AGAIN_DAYS, CHECK_EVERY, MAX_CHECKS, STATE_LAST, logger
from .open_loops_helpers import _clip, _parse_day, _parse_iso


class _PassMixin:
    # Host-class state set in OpenLoops.__init__ (core/open_loops/__init__.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime. DatabaseManager is imported only under
    # TYPE_CHECKING, so its annotation must stay quoted.
    db: "DatabaseManager"
    memory: Any
    proactive: Any
    _run_lock: threading.Lock

    # _conn is defined on OpenLoops itself, which comes after every mixin in
    # the MRO, so it lives under TYPE_CHECKING only: a runtime stub would
    # shadow the real method.
    if TYPE_CHECKING:
        def _conn(self) -> Any: ...

    # tag_new and close_settled_elsewhere live on _FindingMixin, research_due on
    # _TimingMixin, check on _CheckingMixin — all of which come before this
    # mixin in the MRO, so these runtime stubs never shadow the real methods.
    def tag_new(self, now: datetime.datetime) -> int:
        raise NotImplementedError

    def close_settled_elsewhere(self) -> int:
        raise NotImplementedError

    def research_due(self, loop: dict[str, Any], fact: dict[str, Any],
                     now: datetime.datetime) -> bool:
        raise NotImplementedError

    def check(self, fact: dict[str, Any], loop: dict[str, Any], answered: bool = False,
              include_research: bool = True, now: datetime.datetime | None = None) -> dict[str, Any] | None:
        raise NotImplementedError

    def open_loops(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn().execute(
            "SELECT l.*, f.content, f.created_at AS fact_created_at FROM mem_open_loops l JOIN mem_facts f "
            "ON f.id = l.fact_id WHERE l.status = 'open' ORDER BY l.check_after, l.fact_id").fetchall()]

    @staticmethod
    def _what(s: dict[str, Any]) -> str:
        w = s["waiting_on"].rstrip(".")
        return w[:1].upper() + w[1:]

    def run(self, now: datetime.datetime, room: int = 2, may_ask: bool = True,
            include_research: bool = True) -> dict[str, Any]:
        """One pass, as a step of the proactive cycle (core/proactive.py), within its budget: room = messages the
        day's cap still allows, may_ask = whether a question is allowed now (questions_per_day, the gap, never one
        while the last is unanswered). What doesn't fit waits for a later cycle. "sent": the messages delivered.
        While another run is going (the other thread), this one does nothing and says so ("busy": True)."""
        result: dict[str, Any] = {"tagged": 0, "closed_elsewhere": 0, "settled": [], "news": [], "asked": None,
                                  "sent": []}
        if not self._run_lock.acquire(blocking=False):
            logger.info("open loops: another run is in progress; skipping this one")
            return {**result, "busy": True}
        try:
            return self._run(now, result, room, may_ask, include_research)
        finally:
            self._run_lock.release()

    def _run(self, now: datetime.datetime, result: dict[str, Any], room: int, may_ask: bool,
             include_research: bool) -> dict[str, Any]:
        self.db.set_state(STATE_LAST, now.isoformat())
        try:
            result["tagged"] = self.tag_new(now)
        except Exception as e:
            logger.warning(f"open loops: tagging failed: {e}")
        result["closed_elsewhere"] = self.close_settled_elsewhere()
        loops = self.open_loops()
        last_owner = self._conn().execute("SELECT MAX(created_at) FROM mem_messages WHERE role = 'user'").fetchone()[0]
        self._run_checks(now, result, loops, last_owner, include_research)
        if self.proactive is None:
            return result
        room -= self._run_announce_settled(result)
        told, room = self._run_report_news(now, result, may_ask, room)
        result["asked"] = told
        if told is None and may_ask and room > 0:
            done_ids = {s["fact_id"] for s in result["settled"] + result["news"]}
            asked = self._maybe_ask(now, [l for l in loops if l["fact_id"] not in done_ids])
            if asked:
                result["asked"] = asked[0]
                result["sent"].append(asked[1])
        if result["tagged"] or result["closed_elsewhere"] or result["settled"] or result["news"] or result["asked"]:
            logger.info(f"open loops: tagged {result['tagged']}, closed elsewhere {result['closed_elsewhere']}, "
                        f"settled {[s['fact_id'] for s in result['settled']]}, news "
                        f"{[n['fact_id'] for n in result['news']]}, asked about {result['asked']}")
        return result

    def _run_checks(self, now: datetime.datetime, result: dict[str, Any], loops: list[dict[str, Any]],
                    last_owner: str | None, include_research: bool) -> None:
        for loop in loops[:MAX_CHECKS]:
            fact = self.memory.get_fact(loop["fact_id"], follow_superseded=False)
            if not fact:
                continue
            checked = _parse_iso(loop.get("checked_at"))
            answered = bool(loop.get("asked_at")) and (last_owner or "") > loop["asked_at"] \
                and (loop.get("checked_at") or "") < (last_owner or "")
            research_pending = include_research and self.research_due(loop, fact, now)
            if checked is not None and now - checked < CHECK_EVERY and not answered and not research_pending:
                continue
            try:
                done = self.check(fact, loop, answered=answered, include_research=include_research, now=now)
            except Exception as e:
                logger.warning(f"open loops: checking #{loop['fact_id']} failed: {e}")
                continue
            if done:
                result[done["kind"]].append(done)
            if research_pending and not getattr(self, "check_failed", False):
                # Count research runs that found nothing (CADENCE_MAX back-off); anything found starts over. A run whose
                # AI call failed found nothing only because nothing was read: it doesn't lengthen the back-off
                with self._conn() as conn:
                    conn.execute("UPDATE mem_open_loops SET quiet_runs = ? WHERE fact_id = ?",
                                 (0 if done else int(loop.get("quiet_runs") or 0) + 1, loop["fact_id"]))

    def _run_announce_settled(self, result: dict[str, Any]) -> int:
        if not result["settled"]:
            return 0
        # The memory already changed, so this is always told (the cycle calls this only with room for one)
        lines = ["🧠 I updated my memory:"] + [
            f"• {self._what(s)} → “{_clip(s['quote'], 200)}” (from {s['where']}"
            + (f": {s['url']}" if s["url"] else "") + ")" for s in result["settled"]]
        result["sent"].append(self.proactive.deliver("notice", "\n".join(lines), "open-loops"))
        return 1

    def _run_report_news(self, now: datetime.datetime, result: dict[str, Any], may_ask: bool,
                         room: int) -> tuple:
        told = None
        for n in result["news"]:
            if told is None and may_ask and room > 0:
                # "waiting on" marks it as the question an answer replies to (see _evidence)
                result["sent"].append(self.proactive.deliver("question", (
                    f"🔎 You were waiting on {n['waiting_on'].rstrip('.')}. I looked it up ({n['where']}): "
                    f"{n['content']}" + (f" ({n['url']})" if n["url"] else "")
                    + " It doesn't say how it came out. Do you know? Tell me and I'll update my memory."), "open-loop"))
                with self._conn() as conn:
                    conn.execute("UPDATE mem_open_loops SET asked_at = ?, news = ? WHERE fact_id = ?",
                                 (now.isoformat(), n["item"], n["fact_id"]))
                told, room = n["fact_id"], room - 1
            else:                               # no room for it now: looked at again next cycle
                with self._conn() as conn:
                    conn.execute("UPDATE mem_open_loops SET evidence_key = NULL, researched_at = NULL WHERE fact_id = ?",
                                 (n["fact_id"],))
        return told, room

    def _maybe_ask(self, now: datetime.datetime, loops: list[dict[str, Any]]) -> tuple | None:
        """Asks about one loop that's past its check_after and wasn't asked about lately: (fact id, message)."""
        if self.proactive is None:
            return None
        today = now.date()
        for loop in loops:
            day = _parse_day(loop.get("check_after"))
            asked = _parse_iso(loop.get("asked_at"))
            if day is None or day > today or (asked is not None and now - asked < datetime.timedelta(days=ASK_AGAIN_DAYS)):
                continue
            since = _parse_iso(loop.get("fact_created_at"))
            text = (f"Last I knew, you were waiting on {loop['waiting_on'].rstrip('.')}"
                    + (f" (since {since:%b} {since.day})" if since else "")
                    + ". Did that come through? Tell me how it went and I'll update my memory.")
            msg = self.proactive.deliver("question", text, "open-loop")
            with self._conn() as conn:
                conn.execute("UPDATE mem_open_loops SET asked_at = ? WHERE fact_id = ?",
                             (now.isoformat(), loop["fact_id"]))
            return loop["fact_id"], msg
        return None
