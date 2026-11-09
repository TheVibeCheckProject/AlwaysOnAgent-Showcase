"""Split out of core/away_shift.py §6 on 2026-11-03; core/away_shift/__init__.py re-exports these names.
Status: the plain-English status line and the details report.
"""
import concurrent.futures
import datetime
import json
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .away_shift_constants import DAILY_SEARCH_CAP, STATE_LAST_RECORD, STATE_LAST_SESSION, STATE_LAST_SUMMARY
    from .away_shift_dates import _parse_iso
except ImportError:
    from core.away_shift.away_shift_constants import (
        DAILY_SEARCH_CAP,
        STATE_LAST_RECORD,
        STATE_LAST_SESSION,
        STATE_LAST_SUMMARY,
    )
    from core.away_shift.away_shift_dates import _parse_iso

logger = logging.getLogger("AlwaysOnAgent.AwayShift")


class _StatusMixin:
    # Host state set by AwayShift.__init__ (core/away_shift/__init__.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime. Types mirror AwayShift.__init__.
    db: "DatabaseManager"  # quoted: imported only under TYPE_CHECKING
    proactive: Any
    _lock: threading.Lock
    _active_future: concurrent.futures.Future | None
    _session_start_time: float | None
    clock: Callable[[], float]
    now_fn: Callable[[], datetime.datetime]

    # Methods defined on earlier mixins in the AwayShift MRO (_SchedulingMixin
    # 1st, _BudgetMixin 2nd; this mixin is 3rd), so runtime stubs are safe:
    # they never shadow a later mixin's real method.
    def searches_today(self, now: datetime.datetime | None = None) -> int:
        raise NotImplementedError

    def search_budget_remaining(self, now: datetime.datetime | None = None) -> int:
        raise NotImplementedError

    def is_app_open(self) -> bool:
        raise NotImplementedError

    def away_seconds(self) -> float:
        raise NotImplementedError

    def status_line(self) -> str:
        """A plain-English status line for chat / HUD context."""
        with self._lock:
            if self._active_future is not None and not self._active_future.done():
                t = datetime.datetime.fromtimestamp(self._session_start_time or self.clock()).astimezone()
                step_str = f": {self._step_label}" if self._step_label else ""
                return f"Background work in progress (started {t:%H:%M}){step_str}."
        pool = getattr(self.proactive, "pool", None) if self.proactive else None
        if pool:
            pending = pool.pending()
            # The pool's column is `topic`; until 2026-10-31 this read "subject", which no row has, so it never listed any
            subjects = [c.get("topic") for c in pending if c.get("topic")]
            if subjects:
                return f"Found in the background today: {', '.join(subjects[:3])}."
        last_summary = self.db.get_state(STATE_LAST_SUMMARY, "")
        if last_summary:
            return f"Found in the background today: {last_summary}."
        return ""

    def details_report(self) -> str:
        """Returns details about the away shift: current step or last session steps and findings."""
        cur = self.now_fn()
        running = self._running_report(cur)
        if running is not None:
            return running
        record_json = self.db.get_state(STATE_LAST_RECORD, "")
        last_session_iso = self.db.get_state(STATE_LAST_SESSION, "")
        if not last_session_iso and not record_json:
            return self._no_session_report(cur)
        lines: list[str] = []
        self._report_last_session(lines, last_session_iso, record_json)
        self._report_pending(lines)
        searches = self.searches_today(cur)
        remaining = self.search_budget_remaining(cur)
        lines.append(f"Searches used today: {searches}/{DAILY_SEARCH_CAP} (budget remaining: {remaining}).")
        lines.append(f"DeskAI presence: {'open' if self.is_app_open() else f'closed for {int(self.away_seconds() // 60)}m'}.")
        return "\n".join(lines)

    def _set_step(self, step: str, label: str) -> None:
        with self._lock:
            self._current_step = step
            self._step_label = label
        try:
            from core.away_shift import publish_activity
        except ImportError:
            from away_shift import publish_activity  # type: ignore[no-redef]
        publish_activity("step", label=label, step=step)

    def _running_report(self, cur: datetime.datetime) -> str | None:
        """The running-session report, or None when no session is active."""
        with self._lock:
            if self._active_future is not None and not self._active_future.done():
                t = datetime.datetime.fromtimestamp(self._session_start_time or self.clock()).astimezone()
                step_str = f"Current step: {self._step_label}" if self._step_label else "Current step: initializing"
                return (
                    f"Background work is currently running (started {t:%H:%M}).\n"
                    f"{step_str}\n"
                    f"Searches today: {self.searches_today(cur)}/{DAILY_SEARCH_CAP} (budget remaining: {self.search_budget_remaining(cur)})."
                )
        return None

    def _no_session_report(self, cur: datetime.datetime) -> str:
        """The report when no session has run yet."""
        return (
            "No background away shift session has run yet.\n"
            f"Next session will start after DeskAI has been closed for 2 hours (away time: {int(self.away_seconds() // 60)}m, app open: {self.is_app_open()}).\n"
            f"Daily search cap: {self.searches_today(cur)}/{DAILY_SEARCH_CAP} used."
        )

    def _report_last_session(self, lines: list, last_session_iso: str, record_json: str) -> None:
        """Appends the last session time, summary, and completed steps to the report lines."""
        if last_session_iso:
            dt = _parse_iso(last_session_iso)
            time_str = dt.strftime("%Y-%m-%d %H:%M") if dt else last_session_iso
            lines.append(f"Last background session ran: {time_str}")
        last_summary = self.db.get_state(STATE_LAST_SUMMARY, "")
        if last_summary:
            lines.append(f"Summary: {last_summary}")
        if record_json:
            try:
                rec = json.loads(record_json)
                steps = rec.get("steps") or []
                if steps:
                    lines.append("Steps completed:")
                    for s in steps:
                        lines.append(f"  • {s.get('label', s.get('step', ''))}: {s.get('outcome', 'done')}")
            except (json.JSONDecodeError, AttributeError, TypeError) as e:
                logger.debug(f"away shift status: unreadable session record ({e})")

    def _report_pending(self, lines: list) -> None:
        """Appends the pending pool findings to the report lines."""
        pool = getattr(self.proactive, "pool", None) if self.proactive else None
        if pool:
            pending = pool.pending()
            if pending:
                lines.append(f"Pending findings in pool ({len(pending)}):")
                for c in pending[:4]:
                    src = c.get("source", "unknown")
                    subj = c.get("topic") or "unnamed"
                    # Scores are 0-1, and NULL until the scorer has run: "{None:.0f}" crashed this report (2026-10-31)
                    score = c.get("score")
                    lines.append(f"  • [{src}] {subj} (score: {'not scored yet' if score is None else f'{score:.2f}'})")
