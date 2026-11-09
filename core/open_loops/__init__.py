"""
Open loops: memories that are waiting on something (2026-10-13, the owner's ask)
=================================================================================
"It has a memory saved that says Alex is waiting on a decision for his permit. I have since got this decision." The
memory only changed a fact when the owner happened to say it changed; nothing went back to see whether "waiting for X"
or "once X is approved" had settled. Five such facts piled up about one permit, and the profile built from them said
he was still waiting. And then: "I think the background should be able to recognize that this is something that
would be public information. So it should be able to search for that."

An open loop is an active fact that will resolve: a decision, ruling, reply or delivery being waited on, an
application pending, "once X happens". Once a day (outside quiet hours) the agent:
1. tags facts it hasn't looked at yet (one AI call for the lot): open or not, what's awaited, when it's worth checking
   (check_after), words that would be in an email about it, and, when its outcome is public and researchable, a web
   search for it (names and numbers from other memories included). Private matters get no search: the query leaves
   the house. Nothing here knows about particular sites: whatever source has the answer turns up in the results;
2. closes loops whose fact was replaced, faded or deleted some other way (chat, the weekly check, the Memory screen);
3. gathers evidence for each open loop: newer facts, the owner's messages and the agent's events since the fact was
   saved, mail (subjects and snippets), and the research: search results and the text of the top pages (the
   agent's own free web search, core/web_tools.py). When none of it changed since the last check (and the owner hasn't written), no AI call is made. An AI
   reads it; the code keeps an answer only when its quote is found in the item it names, and a second AI on other
   providers agrees. Then:
   - settled: the fact is replaced (history kept; pointed at a newer fact that already says it, rather than a copy)
     and the owner is told in one line;
   - news (something happened but the evidence doesn't say how it came out, e.g. a city permit portal listing
     "Decision on Application for a Building Permit" on Oct 5 while the decision itself is behind the portal's
     sign-in): the owner is told what was found, with the link, and asked how it went. Each piece is told once;
4. when nothing settles a loop and check_after has passed, asks the owner once ("did that come through?"), at most
   one question a run and again only after ASK_AGAIN_DAYS. After a question is answered the check runs at the next
   cycle rather than the next day.
Mail and web pages are other people's words: evidence to read, never instructions.

How a pass goes: the proactive cycle calls run() (§8). It tags facts not looked at yet (§5), closes loops
settled elsewhere, then for a few open loops gathers evidence (§7: newer facts, your messages, mail, and web
research when it's due, §6, §3). An AI reads it; an answer counts only with a quote found in its source and
a second AI agreeing. Settled: the fact is updated and you're told. Still open past its date: you're asked once.

MAP OF THIS FILE
----------------
Each section starts with a banner like "# §2  ...". Search for the § number to jump straight to it.
Sections were split out of core/open_loops.py on 2026-11-03, one mixin/helper module per section, and moved
into this package on 2026-11-04; this package keeps the OpenLoops shell (§4: __init__, _conn, due) and
re-exports every name, so `from core.open_loops import ...` keeps working exactly as before.

  §1   Setup                         core/open_loops/open_loops_constants.py (timings, limits, logger)
  §2   Small helpers                 core/open_loops/open_loops_helpers.py (dates, clipping, prompts)
  §3   Web research                  core/open_loops/open_loops_research.py
  §4   OpenLoops: setup and timing   here: __init__, _conn, due
  §5   Finding loops                 core/open_loops/open_loops_finding.py (_FindingMixin)
  §6   When to research              core/open_loops/open_loops_timing.py (_TimingMixin)
  §7   Checking a loop               core/open_loops/open_loops_checking.py (_CheckingMixin)
  §8   A pass                        core/open_loops/open_loops_pass.py (_PassMixin)
"""

import datetime
import threading
from collections.abc import Callable

try:
    from core.database import get_iso_now
except ImportError:
    from database import get_iso_now  # type: ignore[no-redef]

# Re-exports: every name that lived at module level in the original file, so
# `from core.open_loops import ...` keeps working. (get_iso_now is kept for
# the same reason: it was a module attribute of the original.)
from .open_loops_checking import _CheckingMixin
from .open_loops_constants import (
    ASK_AGAIN_DAYS,
    CADENCE_MAX,
    CADENCE_NEAR,
    CADENCE_NORMAL,
    CHECK_EVERY,
    DEFAULT_WAIT_DAYS,
    LOOP_CATEGORIES,
    MAX_CHECKS,
    MAX_QUERIES,
    NEAR_WINDOW_DAYS,
    PAGE_CHARS,
    PAGE_TEXT,
    RESEARCH_PAGES,
    SOURCE_NAMES,
    STATE_LAST,
    TAG_BATCH,
    logger,
)
from .open_loops_finding import _FindingMixin
from .open_loops_helpers import (
    CHECK_PROMPT,
    TAG_PROMPT,
    VERIFY_SETTLED,
    _around,
    _clip,
    _json,
    _norm,
    _one_line,
    _parse_day,
    _parse_iso,
    _queries,
)
from .open_loops_pass import _PassMixin
from .open_loops_research import _default_research, _excerpt
from .open_loops_timing import _TimingMixin

__all__ = [
    "ASK_AGAIN_DAYS",
    "CADENCE_MAX",
    "CADENCE_NEAR",
    "CADENCE_NORMAL",
    "CHECK_EVERY",
    "CHECK_PROMPT",
    "DEFAULT_WAIT_DAYS",
    "LOOP_CATEGORIES",
    "MAX_CHECKS",
    "MAX_QUERIES",
    "NEAR_WINDOW_DAYS",
    "PAGE_CHARS",
    "PAGE_TEXT",
    "RESEARCH_PAGES",
    "SOURCE_NAMES",
    "STATE_LAST",
    "TAG_BATCH",
    "TAG_PROMPT",
    "VERIFY_SETTLED",
    "OpenLoops",
    "_around",
    "_clip",
    "_default_research",
    "_excerpt",
    "_json",
    "_norm",
    "_one_line",
    "_parse_day",
    "_parse_iso",
    "_queries",
    "get_iso_now",
    "logger",
]


class OpenLoops(_FindingMixin, _TimingMixin, _CheckingMixin, _PassMixin):
    def __init__(self, db, memory, proactive=None, connectors_mod=None,
                 research: Callable[[str], list[dict[str, str]]] | None = None):
        self.db, self.memory, self.proactive = db, memory, proactive
        if connectors_mod is None:            # False: no mail (reading the loops needs none)
            try:
                from core import connectors as connectors_mod
            except ImportError:
                connectors_mod = None
        self.connectors = connectors_mod or None
        self.research = research or _default_research
        # One run at a time: the away shift and the proactive cycle run on their own threads and both call run(); two
        # runs over the same loops could settle one fact twice or tell the owner twice (2026-10-31 review, defect 7)
        self._run_lock = threading.Lock()
        with self.db.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS mem_open_loops (
                    fact_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL,          -- open | closed | none (looked at: not a loop)
                    waiting_on TEXT,
                    check_after TEXT,
                    mail TEXT,
                    created_at TEXT NOT NULL,
                    checked_at TEXT,
                    asked_at TEXT,
                    closed_by INTEGER,             -- the fact that settled it
                    note TEXT,
                    research TEXT,
                    evidence_key TEXT,
                    news TEXT,
                    researched_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_mem_open_loops ON mem_open_loops (status);
            """)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(mem_open_loops)")}
            for col in ("research", "evidence_key", "news", "researched_at"):     # research: the query; news: told already; researched_at: last search
                if col not in cols:
                    conn.execute(f"ALTER TABLE mem_open_loops ADD COLUMN {col} TEXT")
            # Research runs in a row that found nothing: each doubles the wait, up to weekly (2026-10-27, B7.4)
            if "quiet_runs" not in cols:
                conn.execute("ALTER TABLE mem_open_loops ADD COLUMN quiet_runs INTEGER DEFAULT 0")

    def _conn(self):
        return self.db.get_connection()

    # ------------------------------------------------------------------------------------------- when
    def due(self, now: datetime.datetime) -> bool:
        """Once every CHECK_EVERY, and sooner when the owner has written since a loop question was asked."""
        last = _parse_iso(self.db.get_state(STATE_LAST, ""))
        if last is None or now - last >= CHECK_EVERY:
            return True
        asked = self._conn().execute(
            "SELECT MAX(asked_at) FROM mem_open_loops WHERE status = 'open' AND asked_at IS NOT NULL").fetchone()[0]
        if not asked:
            return False
        return self._conn().execute("SELECT 1 FROM mem_messages WHERE role = 'user' AND created_at > ? LIMIT 1",
                                    (max(asked, last.isoformat()),)).fetchone() is not None
