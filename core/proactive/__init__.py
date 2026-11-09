"""
Proactiveness: the agent reaches out first
==========================================
Every PROACTIVE_EVERY_MINUTES the daemon runs a cycle (live agent only). Outside quiet hours and under the
day's cap it may send: the morning brief (once a day), notices and suggestions (an AI call picks what's worth
it from cheap signals: new important email, renewals, schedules due soon, dated facts, things the owner said
they'd do), memories waiting on something checked for how they came out (core/open_loops/: looked up on the
web when the outcome is public, settled ones updated and told, news and stale ones asked about), and curious
questions: one specific thing the owner said or cares about, wondered about, sometimes looked up first (never while
the last question is unanswered).

Each message is stored in proactive_messages, added to the conversation (so a reply has context) and announced
with a push that carries NO content (pushes pass through Google); DeskAI fetches the text from GET /api/inbox.
Design: docs/PROACTIVE.md.

How a cycle goes: the daemon calls run_cycle() (§6) every PROACTIVE_EVERY_MINUTES. It may write the brief (§9),
add notices from cheap signals (§8, §10) and a curious question (§11) to the candidate pool (core/candidates/),
then sends the best candidate if it's a good moment (§7). Every message goes through deliver() (§5).

MAP OF THIS FILE
----------------
Each section starts with a banner like "# §2  ...". Search for the § number to jump straight to it.

  §1   Setup                          imports, timings, the brief's routine-mail rule, guardrail word lists
                                       (constants: core/proactive/proactive_constants.py)
  §2   Small helpers                  the brief's greeting from the clock, time and date parsing
                                       (core/proactive/proactive_helpers.py)
  §3   Settings and your switches     proactive settings, interests, exclusions, on/off per kind, quiet hours
                                       (core/proactive/proactive_settings.py)
  §4   The inbox and feedback         what DeskAI's inbox lists; your 👍 / 👎 / "less like this"
                                       (core/proactive/proactive_inbox.py)
  §5   Delivering                     sending now or holding until you're around; the outbox; "I was off"
                                       (core/proactive/proactive_delivery.py)
  §6   The cycle                      run_cycle(): one pass of everything, every 30 minutes
                                       (core/proactive/proactive_cycle.py)
  §7   The candidate pool and timing  scoring, the send threshold, a good moment, the silence check-in
                                       (core/proactive/proactive_pool.py)
  §8   Signals and context            cheap inputs: new important mail, renewals; your profile and recent words
                                       (core/proactive/proactive_signals.py)
  §9   The brief                      when it's due, writing it ahead, posting it to Today, what it says
                                       (core/proactive/proactive_brief.py)
  §10  Notices                        the AI picks notices from the signals; guardrails; grounding
                                       (core/proactive/proactive_notices.py)
  §11  Curious questions              when it may ask, writing one, looking it up first, not repeating itself
                                       (core/proactive/proactive_questions.py)

Split 2026-11-03: §§1-2 helpers/constants and §§3-11 methods moved to the
files above; this file keeps the class (with __init__) and re-exports every
public name, so `from core.proactive import Proactive, greeting, ...` still works.

Packaged 2026-11-04: the sections live in core/proactive/ (one file per
section, same basenames); this __init__.py is the shell. The old core/proactive.py
was a shim — Python loaded this package in preference to that file, so the
shim never ran; it was deleted 2026-11-04."""


import datetime
import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.events import Presence
    from core.open_loops import OpenLoops

try:
    from core.candidates import SOURCES_THAT_MAY_REPEAT, CandidatePool, Scorer, Similarity, discussed, novelty
    from core.database import get_iso_now
    from core.llm_usage import UsageLog, usage_purpose
except ImportError:
    from candidates import SOURCES_THAT_MAY_REPEAT, CandidatePool, Scorer, Similarity, discussed, novelty  # type: ignore[no-redef]
    from database import get_iso_now  # type: ignore[no-redef]
    from llm_usage import UsageLog, usage_purpose  # type: ignore[no-redef]


try:
    from .proactive_brief import _BriefMixin
    from .proactive_constants import (
        ACTIVE_WITHIN,
        BRIEF_EVERY,
        BRIEF_STAGE_MAX_AGE,
        DATED,
        DEFAULTS,
        GUARDRAIL_ENGAGEMENT,
        GUARDRAIL_SENSITIVE,
        KINDS,
        LULL,
        MONTHS,
        NOW_PER_CYCLE,
        OFFLINE_NOTICE_AFTER,
        OUTBOX_KEEP,
        OUTBOX_SPACING,
        PROACTIVE_EVERY_MINUTES,
        PUSH_TEXT,
        QUESTION_GAP_HOURS,
        QUESTION_GEN_AFTER_DROP,
        QUESTION_GEN_EVERY,
        QUESTION_GEN_NEW_FACTS,
        QUESTION_GEN_NEW_MESSAGES,
        ROUTINE_MAIL,
        SILENCE_CHECKIN_BACKOFF_1,
        SILENCE_CHECKIN_BACKOFF_2,
        SILENCE_CHECKIN_INITIAL,
        SILENCE_STEP,
        STATE_BRIEF_STAGED,
        STATE_EXCLUSIONS,
        STATE_INTERESTS,
        STATE_QUESTION_GEN,
        STATE_RANKED_UPTO,
        STATE_SILENCE_CHECKIN_AT,
        STATE_SILENCE_LEVEL,
        SWITCH_KEYS,
        THRESHOLD_FLOOR,
        THRESHOLD_STEP,
        logger,
    )
    from .proactive_cycle import _CycleMixin
    from .proactive_delivery import _DeliveryMixin
    from .proactive_helpers import _hhmm, _now, _parse_day, _parse_iso, greeting
    from .proactive_inbox import _InboxMixin
    from .proactive_notices import _NoticesMixin
    from .proactive_pool import _PoolMixin
    from .proactive_questions import _QuestionsMixin
    from .proactive_settings import _SettingsMixin
    from .proactive_signals import _SignalsMixin
except ImportError:
    from core.proactive.proactive_brief import _BriefMixin
    from core.proactive.proactive_constants import (
        ACTIVE_WITHIN,
        BRIEF_EVERY,
        BRIEF_STAGE_MAX_AGE,
        DATED,
        DEFAULTS,
        GUARDRAIL_ENGAGEMENT,
        GUARDRAIL_SENSITIVE,
        KINDS,
        LULL,
        MONTHS,
        NOW_PER_CYCLE,
        OFFLINE_NOTICE_AFTER,
        OUTBOX_KEEP,
        OUTBOX_SPACING,
        PROACTIVE_EVERY_MINUTES,
        PUSH_TEXT,
        QUESTION_GAP_HOURS,
        QUESTION_GEN_AFTER_DROP,
        QUESTION_GEN_EVERY,
        QUESTION_GEN_NEW_FACTS,
        QUESTION_GEN_NEW_MESSAGES,
        ROUTINE_MAIL,
        SILENCE_CHECKIN_BACKOFF_1,
        SILENCE_CHECKIN_BACKOFF_2,
        SILENCE_CHECKIN_INITIAL,
        SILENCE_STEP,
        STATE_BRIEF_STAGED,
        STATE_EXCLUSIONS,
        STATE_INTERESTS,
        STATE_QUESTION_GEN,
        STATE_RANKED_UPTO,
        STATE_SILENCE_CHECKIN_AT,
        STATE_SILENCE_LEVEL,
        SWITCH_KEYS,
        THRESHOLD_FLOOR,
        THRESHOLD_STEP,
        logger,
    )
    from core.proactive.proactive_cycle import _CycleMixin
    from core.proactive.proactive_delivery import _DeliveryMixin
    from core.proactive.proactive_helpers import _hhmm, _now, _parse_day, _parse_iso, greeting
    from core.proactive.proactive_inbox import _InboxMixin
    from core.proactive.proactive_notices import _NoticesMixin
    from core.proactive.proactive_pool import _PoolMixin
    from core.proactive.proactive_questions import _QuestionsMixin
    from core.proactive.proactive_settings import _SettingsMixin
    from core.proactive.proactive_signals import _SignalsMixin


__all__ = [
    "ACTIVE_WITHIN",
    "BRIEF_EVERY",
    "BRIEF_STAGE_MAX_AGE",
    "DATED",
    "DEFAULTS",
    "GUARDRAIL_ENGAGEMENT",
    "GUARDRAIL_SENSITIVE",
    "KINDS",
    "LULL",
    "MONTHS",
    "NOW_PER_CYCLE",
    "OFFLINE_NOTICE_AFTER",
    "OUTBOX_KEEP",
    "OUTBOX_SPACING",
    "PROACTIVE_EVERY_MINUTES",
    "PUSH_TEXT",
    "QUESTION_GAP_HOURS",
    "QUESTION_GEN_AFTER_DROP",
    "QUESTION_GEN_EVERY",
    "QUESTION_GEN_NEW_FACTS",
    "QUESTION_GEN_NEW_MESSAGES",
    "ROUTINE_MAIL",
    "SILENCE_CHECKIN_BACKOFF_1",
    "SILENCE_CHECKIN_BACKOFF_2",
    "SILENCE_CHECKIN_INITIAL",
    "SILENCE_STEP",
    "SOURCES_THAT_MAY_REPEAT",
    "STATE_BRIEF_STAGED",
    "STATE_EXCLUSIONS",
    "STATE_INTERESTS",
    "STATE_QUESTION_GEN",
    "STATE_RANKED_UPTO",
    "STATE_SILENCE_CHECKIN_AT",
    "STATE_SILENCE_LEVEL",
    "SWITCH_KEYS",
    "THRESHOLD_FLOOR",
    "THRESHOLD_STEP",
    "CandidatePool",
    "Proactive",
    "Scorer",
    "Similarity",
    "UsageLog",
    "_BriefMixin",
    "_CycleMixin",
    "_DeliveryMixin",
    "_InboxMixin",
    "_NoticesMixin",
    "_PoolMixin",
    "_QuestionsMixin",
    "_SettingsMixin",
    "_SignalsMixin",
    "_hhmm",
    "_now",
    "_parse_day",
    "_parse_iso",
    "discussed",
    "get_iso_now",
    "greeting",
    "logger",
    "novelty",
    "usage_purpose",
]


class Proactive(_SettingsMixin, _InboxMixin, _DeliveryMixin, _CycleMixin, _PoolMixin, _SignalsMixin, _BriefMixin, _NoticesMixin, _QuestionsMixin):
    def __init__(self, db, memory=None, llm=None, send_push: Callable[..., int] | None = None,
                 connectors_mod: Any = None, now: Callable[[], datetime.datetime] = _now):
        self.db, self.memory, self.llm = db, memory, llm
        self.send_push = send_push
        self.connectors = connectors_mod
        self.now = now
        self.open_loops: OpenLoops | None = None          # core/open_loops.OpenLoops, set by the daemon: a step of every cycle
        self.research = None            # web search for curiosity's look-ups (tests set a fake); None: web_tools
        self.presence: Presence | None = None            # core/events.PRESENCE, set by the daemon; None: only his messages tell
        with db.get_connection() as conn:
            # Messages held until Alex is around (deliver(); released one at a time by the cycle)
            conn.execute("""CREATE TABLE IF NOT EXISTS proactive_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, text TEXT NOT NULL, topic TEXT,
                canvas_json TEXT, why TEXT, subject TEXT, candidate_id INTEGER, created_at TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS proactive_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, text TEXT NOT NULL,
                topic TEXT, created_at TEXT NOT NULL)""")
            # Added for the weekly review: a message can carry a canvas draft card ({"id", "title", "kind", "version"})
            cols = {r[1] for r in conn.execute("PRAGMA table_info(proactive_messages)")}
            if "canvas_json" not in cols:
                conn.execute("ALTER TABLE proactive_messages ADD COLUMN canvas_json TEXT")
            # What a message is about, in a few words: what novelty compares (core/candidates/, 2026-10-13)
            if "subject" not in cols:
                conn.execute("ALTER TABLE proactive_messages ADD COLUMN subject TEXT")
            # Why it was sent (shown under the message in DeskAI, Phase 2) and the pool row it came from (1.6)
            if "why" not in cols:
                conn.execute("ALTER TABLE proactive_messages ADD COLUMN why TEXT")
            if "candidate_id" not in cols:
                conn.execute("ALTER TABLE proactive_messages ADD COLUMN candidate_id INTEGER")
            if "feedback" not in cols:
                conn.execute("ALTER TABLE proactive_messages ADD COLUMN feedback TEXT")
            if "feedback_at" not in cols:
                conn.execute("ALTER TABLE proactive_messages ADD COLUMN feedback_at TEXT")
            # A brief's own facts for Today's tiles (up next, mail), kept apart from the AI's wording (2026-10-28)
            if "data_json" not in cols:
                conn.execute("ALTER TABLE proactive_messages ADD COLUMN data_json TEXT")
        self.pool = CandidatePool(db, now=now)
        self._scorer: Scorer | None = None
        self._brief_data: dict[str, Any] = {"up_next": [], "mail": []}     # the last brief's facts (_brief)
        self._sim: Similarity | None = None
