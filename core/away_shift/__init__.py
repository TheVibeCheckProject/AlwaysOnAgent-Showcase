"""
Away shift: background thinking and research while Alex is away (docs/PROACTIVE_RESEARCH.md 1a, 1l)
===================================================================================================
Alex: "If the app hasnt been opened in 2 hours, im okay with the agent doing research and background
things then. So i can still comunicate with the AI, but it needs to know that research is happening
in the background."

Trigger:
1. The app has been closed for 2 hours (no live SSE stream connected; core/events.py PRESENCE).
2. The last session started at least 2 hours ago.
3. The day's search budget is not exceeded (DAILY_SEARCH_CAP = 15).

Steps in order:
1. Memory upkeep: consolidate() (embed, dedupe, fade, rebuild profile).
2. Open loops: tag, then check with research (24h/48h cadence or cheap signals).
3. Interests: check updates for topics Alex cares about (up to 3 web searches).
4. Patterns: weekly reflection on work rhythms / focus times to form curious questions.
5. Curiosity pool: proposes candidate thoughts with "about" grounding quotes.
6. AI Opportunities: automated research worker (task-189; 3-day cadence) for free tiers/credits.

Execution:
- Runs on its own executor (ThreadPoolExecutor), never blocking chat, tasks, or the proactive cycle.
- Publishes activity events for the DeskAI icon (core/events.py).
- Records its state so chat knows what background work is running or was found today.

How it runs: the daemon asks due() (§4) on its heartbeat; when the app has been closed for 2 hours it starts a
session (§7) on its own thread. A session runs its steps (§9: followed channels, interests, patterns, curious
questions, plus memory upkeep and open loops), within a daily search budget (§5), skipping what was already
covered (§8). What it finds goes to the candidate pool; chat and the HUD can ask what it's doing (§2, §6).

MAP OF THIS PACKAGE
-------------------
Each section starts with a banner like "# §2  ...". Search for the § number to jump straight to it.
The sections live in their own module under core/away_shift/; this __init__ keeps the class, its
setup, the §2 status helpers, and re-exports every name the old flat file had, so
`from core.away_shift import X` still works. The old core/away_shift.py was a thin shim (the package won
over the module file when both existed); it was removed 2026-11-04.

  §1   Setup                         imports, timings, the daily search budget, how long things count as covered
                                      -> core/away_shift/away_shift_constants.py
  §2   Status for chat and the HUD   the one running shift; its status line and report
  §3   Dates                         reading the publication dates search results and pages give
                                      -> core/away_shift/away_shift_dates.py
  §4   AwayShift: when it runs       the class; is the app open, how long you've been away, is a session due
                                      -> core/away_shift/away_shift_scheduling.py (_SchedulingMixin)
  §5   The search budget             searches today, the cap, a capped search, a result's real date
                                      -> core/away_shift/away_shift_budget.py (_BudgetMixin)
  §6   Status                        the plain-English status line and the details report
                                      -> core/away_shift/away_shift_status.py (_StatusMixin)
  §7   A session                     starting and stopping; run_session(): every step in order
                                      -> core/away_shift/away_shift_session.py (_SessionMixin)
  §8   Not repeating itself          subjects already covered, new material since last time, findings seen
                                      -> core/away_shift/away_shift_dedup.py (_DedupMixin)
  §9   The steps                     followed channels, interests, weekly patterns, curious questions
                                      -> core/away_shift/away_shift_steps.py (_StepsMixin)
"""
import concurrent.futures
import datetime
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

try:
    from core.candidates import backfill_subjects
    from core.database import DatabaseManager, get_iso_now
    from core.events import ACTIVITY, PRESENCE, publish_activity
    from core.llm_usage import usage_purpose
except ImportError:
    from candidates import backfill_subjects  # type: ignore[no-redef]
    from database import DatabaseManager, get_iso_now  # type: ignore[no-redef]
    from events import ACTIVITY, PRESENCE, publish_activity  # type: ignore[no-redef]
    from llm_usage import usage_purpose  # type: ignore[no-redef]

logger = logging.getLogger("AlwaysOnAgent.AwayShift")

try:
    from .away_shift_budget import _BudgetMixin
    from .away_shift_constants import (
        AWAY_TRIGGER_SECONDS,
        COVERED_DAYS_ASKED,
        COVERED_DAYS_DROPPED,
        DAILY_SEARCH_CAP,
        FULL_UPKEEP_EVERY,
        GEN_NEW_FACTS,
        GEN_NEW_MESSAGES,
        GEN_WAIT_AFTER_EMPTY,
        MAX_INTEREST_SEARCHES,
        POOL_LOW_WATERMARK,
        SESSION_GAP_SECONDS,
        STATE_GEN_PREFIX,
        STATE_LAST_FULL_UPKEEP,
        STATE_LAST_PATTERNS,
        STATE_LAST_RECORD,
        STATE_LAST_SESSION,
        STATE_LAST_SUMMARY,
        STATE_SEARCHES_PREFIX,
    )
    from .away_shift_dates import (
        CURIOSITY_POOL_PROMPT,
        FINDING_MAX_AGE,
        INTEREST_EVAL_PROMPT,
        INTEREST_EVERY,
        INTERESTS_PROMPT,
        NOT_AN_INTEREST,
        PATTERNS_PROMPT,
        STATE_INTERESTS_AT,
        STATE_SEEN_FINDINGS,
        _now,
        _parse_iso,
        parse_published,
    )
    from .away_shift_dedup import _DedupMixin
    from .away_shift_scheduling import _SchedulingMixin
    from .away_shift_session import _SessionMixin
    from .away_shift_status import _StatusMixin
    from .away_shift_steps import _StepsMixin
except ImportError:
    from core.away_shift.away_shift_budget import _BudgetMixin
    from core.away_shift.away_shift_constants import (
        AWAY_TRIGGER_SECONDS,
        COVERED_DAYS_ASKED,
        COVERED_DAYS_DROPPED,
        DAILY_SEARCH_CAP,
        FULL_UPKEEP_EVERY,
        GEN_NEW_FACTS,
        GEN_NEW_MESSAGES,
        GEN_WAIT_AFTER_EMPTY,
        MAX_INTEREST_SEARCHES,
        POOL_LOW_WATERMARK,
        SESSION_GAP_SECONDS,
        STATE_GEN_PREFIX,
        STATE_LAST_FULL_UPKEEP,
        STATE_LAST_PATTERNS,
        STATE_LAST_RECORD,
        STATE_LAST_SESSION,
        STATE_LAST_SUMMARY,
        STATE_SEARCHES_PREFIX,
    )
    from core.away_shift.away_shift_dates import (
        CURIOSITY_POOL_PROMPT,
        FINDING_MAX_AGE,
        INTEREST_EVAL_PROMPT,
        INTEREST_EVERY,
        INTERESTS_PROMPT,
        NOT_AN_INTEREST,
        PATTERNS_PROMPT,
        STATE_INTERESTS_AT,
        STATE_SEEN_FINDINGS,
        _now,
        _parse_iso,
        parse_published,
    )
    from core.away_shift.away_shift_dedup import _DedupMixin
    from core.away_shift.away_shift_scheduling import _SchedulingMixin
    from core.away_shift.away_shift_session import _SessionMixin
    from core.away_shift.away_shift_status import _StatusMixin
    from core.away_shift.away_shift_steps import _StepsMixin

_CURRENT_AWAY_SHIFT: "AwayShift | None" = None


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §2  STATUS FOR CHAT AND THE HUD
#     Module-level access to the running shift, and its status line / report for the chat's context and the HUD.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def get_current_away_shift() -> "AwayShift | None":
    return _CURRENT_AWAY_SHIFT


def set_current_away_shift(shift: "AwayShift | None") -> None:
    global _CURRENT_AWAY_SHIFT
    _CURRENT_AWAY_SHIFT = shift


def get_status_line(db: DatabaseManager | None = None) -> str:
    """Returns the background work status line for chat / HUD context."""
    if _CURRENT_AWAY_SHIFT is not None:
        line = _CURRENT_AWAY_SHIFT.status_line()
        if line:
            return line
    if db is not None:
        last_summary = db.get_state(STATE_LAST_SUMMARY, "")
        if last_summary:
            return f"Found in the background today: {last_summary}."
    return ""


def get_details_report(db: DatabaseManager) -> str:
    """Returns report of recent background work when no AwayShift instance is active."""
    last_session = db.get_state(STATE_LAST_SESSION, "")
    if not last_session:
        return "No background away shift session has run yet. Next session will start after DeskAI has been closed for 2 hours."
    lines = [f"Last background session: {last_session}"]
    last_summary = db.get_state(STATE_LAST_SUMMARY, "")
    if last_summary:
        lines.append(f"Summary: {last_summary}")
    rec_json = db.get_state(STATE_LAST_RECORD, "")
    if rec_json:
        try:
            rec = json.loads(rec_json)
            steps = rec.get("steps", [])
            if steps:
                lines.append("Steps:")
                for s in steps:
                    lines.append(f"  • {s.get('label', '')}: {s.get('outcome', 'done')}")
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            logger.debug(f"away shift details: unreadable session record ({e})")
    return "\n".join(lines)




class AwayShift(_SchedulingMixin, _BudgetMixin, _StatusMixin, _SessionMixin, _DedupMixin, _StepsMixin):
    """Background worker running memory upkeep, open loops, and opportunity research when Alex is away."""

    def __init__(self, db: DatabaseManager, memory: Any = None, llm: Any = None, proactive: Any = None,
                 open_loops: Any = None, connectors: Any = None, search_fn: Callable | None = None,
                 clock: Callable[[], float] = time.time, now_fn: Callable[[], datetime.datetime] = _now,
                 page_date_fn: Callable[[str], str] | None = None):
        self.db = db
        self.memory = memory
        self.llm = llm
        self.proactive = proactive
        self.open_loops = open_loops
        self.connectors = connectors
        self.search_fn = search_fn
        self.page_date_fn = page_date_fn            # url -> the date the page says it was published ("" if none)
        self.clock = clock
        self.now_fn = now_fn
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="AwayShift")
        self._active_future: concurrent.futures.Future | None = None
        self._lock = threading.Lock()
        self._current_step = ""
        self._step_label = ""
        self._session_start_time: float | None = None
        self._found_today: int = 0
        set_current_away_shift(self)
