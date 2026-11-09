"""Split out of core/away_shift.py §4 on 2026-11-03; core/away_shift/__init__.py re-exports these names.
AwayShift: when it runs — app open, how long away, is a session due.
"""
import datetime
from collections.abc import Callable
from typing import TYPE_CHECKING

try:
    from core.events import PRESENCE
except ImportError:
    from events import PRESENCE  # type: ignore[no-redef]

try:
    from .away_shift_constants import AWAY_TRIGGER_SECONDS, SESSION_GAP_SECONDS, STATE_LAST_SESSION
    from .away_shift_dates import _parse_iso
except ImportError:
    from core.away_shift.away_shift_constants import AWAY_TRIGGER_SECONDS, SESSION_GAP_SECONDS, STATE_LAST_SESSION
    from core.away_shift.away_shift_dates import _parse_iso

import logging

if TYPE_CHECKING:
    import concurrent.futures
    import threading

    try:
        from core.database import DatabaseManager
    except ImportError:
        from database import DatabaseManager  # type: ignore[no-redef]

logger = logging.getLogger("AlwaysOnAgent.AwayShift")


class _SchedulingMixin:
    # Host state set on AwayShift.__init__ (main class, not a later mixin),
    # declared here so mypy sees the attributes. Quoted annotations: the
    # names are only imported under TYPE_CHECKING.
    _lock: "threading.Lock"
    _active_future: "concurrent.futures.Future | None"
    now_fn: "Callable[[], datetime.datetime]"
    db: "DatabaseManager"

    def is_running(self) -> bool:
        with self._lock:
            return self._active_future is not None and not self._active_future.done()

    def is_app_open(self) -> bool:
        return PRESENCE.is_open()

    def away_seconds(self) -> float:
        return PRESENCE.away_seconds()

    def due(self, now: datetime.datetime | None = None) -> bool:
        """True when DeskAI has been closed for 2 hours, last session started >= 2 hours ago, and not running."""
        if self.is_running():
            return False
        if self.is_app_open():
            return False
        if self.away_seconds() < AWAY_TRIGGER_SECONDS:
            return False
        cur = now or self.now_fn()
        last_iso = self.db.get_state(STATE_LAST_SESSION, "")
        if last_iso:
            last_dt = _parse_iso(last_iso)
            if last_dt and (cur - last_dt).total_seconds() < SESSION_GAP_SECONDS:
                return False
        return True

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
