"""Constants for the proactive agent.

Split out of core/proactive.py §1 (setup) and the §2 module constants on
2026-11-03; core/proactive/__init__.py re-exports these names, so `proactive.DEFAULTS`,
`proactive.ROUTINE_MAIL`, `proactive.STATE_RANKED_UPTO` and the rest still work.
"""
import datetime
import logging
import re

# A child of the daemon's logger, so its lines reach logs/worker.log (as "Proactive" they went nowhere on the Mini;
# found 2026-10-13 checking the first open-loop run)
logger = logging.getLogger("AlwaysOnAgent.Proactive")

PROACTIVE_EVERY_MINUTES = 30
# Timing follows Alex, not the clock (2026-10-27, his words: no regular sleep pattern at all, "these quiet hours are
# not important to me"; 35% of his messages were 22:00-08:00 and the 08:30 brief got 2 replies in 9). Quiet hours are
# off unless he sets some; the brief comes when he's around after BRIEF_EVERY ("auto"), or at a time he sets; what
# isn't urgent waits in the outbox until he's around (the 2026-10-27 background audit, B4, B5).
DEFAULTS = {"enabled": "1", "brief_time": "auto", "quiet_hours": "off", "daily_cap": "10",
            "questions_per_day": "4", "location": "", "brief_to": "today",
            "open_loops": "1", "curiosity": "1", "research": "1", "patterns": "1"}
STATE_INTERESTS = "proactive_interests"
STATE_EXCLUSIONS = "proactive_exclusions"
SWITCH_KEYS = ("open_loops", "curiosity", "research", "patterns")
QUESTION_GAP_HOURS = 3
# Routing from the candidate pool (docs/PROACTIVE_RESEARCH.md 1b, 1n)
LULL = datetime.timedelta(minutes=10)            # nothing from the pool within 10 min of the last message either way
ACTIVE_WITHIN = datetime.timedelta(hours=1)      # "whenever" items go when he's been around this recently
SILENCE_STEP = datetime.timedelta(hours=6)       # the threshold drops THRESHOLD_STEP per this much quiet, to the floor
THRESHOLD_STEP, THRESHOLD_FLOOR = 0.05, 0.35
NOW_PER_CYCLE = 2                                # time-sensitive notices per cycle (as before)
STATE_RANKED_UPTO = "proactive_ranked_upto"      # the newest candidate id the ranker has seen
# Silence check-in (docs/PROACTIVE_RESEARCH.md 1k; checklist 1.7)
SILENCE_CHECKIN_INITIAL = datetime.timedelta(hours=6)    # reaches out after 6 hours without a message from Alex
SILENCE_CHECKIN_BACKOFF_1 = datetime.timedelta(hours=12) # if ignored, waits 12 hours before next check-in
SILENCE_CHECKIN_BACKOFF_2 = datetime.timedelta(hours=24) # if still ignored, waits 24 hours
STATE_SILENCE_CHECKIN_AT = "proactive_silence_checkin_at"
STATE_SILENCE_LEVEL = "proactive_silence_level"
# Presence-based delivery (AUDIT_BACKGROUND B4, B5)
BRIEF_EVERY = datetime.timedelta(hours=18)        # "auto" brief: the first time he's around this long after the last
BRIEF_STAGE_MAX_AGE = datetime.timedelta(hours=4)  # a brief written ahead while he's away is used if this fresh
STATE_BRIEF_STAGED = "proactive_brief_staged"
OUTBOX_SPACING = datetime.timedelta(minutes=45)    # held messages go one at a time, this far apart (no stampede)
OUTBOX_KEEP = datetime.timedelta(days=4)           # a held message older than this is dropped, not sent stale
OFFLINE_NOTICE_AFTER = datetime.timedelta(hours=1)  # a gap in the heartbeat this long is worth telling him
# How often the cycle writes a curious question of its own (_question_gen_due)
QUESTION_GEN_EVERY = datetime.timedelta(hours=6)
QUESTION_GEN_AFTER_DROP = datetime.timedelta(hours=24)
QUESTION_GEN_NEW_MESSAGES = 5
QUESTION_GEN_NEW_FACTS = 3
STATE_QUESTION_GEN = "proactive_question_gen"
KINDS = ("brief", "notice", "suggestion", "question", "review", "memory")    # memory: the weekly check (core/memory_review.py)
# 2026-10-04: Brave's sign-up codes and password resets (the owner adding search keys) became a notice and a
# brief line "requiring your review"
ROUTINE_MAIL = ("Leave out routine account mail (verification or sign-in codes, password resets, confirm-your-email "
                "and welcome messages): the owner almost always triggered it themselves. Leave out daily digests, "
                "lottery numbers, statements and promotions too (USPS Informed Delivery, Illinois Lottery, free "
                "samples: in briefs every day, 2026-10-27 to 11-02); mention mail a person wrote, or that needs action.")


# Guardrails (docs/PROACTIVE_RESEARCH.md 1h; P8)
GUARDRAIL_ENGAGEMENT = (
    "we miss you",
    "haven't seen you",
    "don't leave",
    "come back to the app",
    "where did you go",
    "you've been away",
)
GUARDRAIL_SENSITIVE = (
    "health",
    "doctor",
    "medical",
    "hospital",
    "illness",
    "salary",
    "debt",
    "bank account",
    "family",
    "parents",
)
# What the push says: never the message itself
PUSH_TEXT = {
    "brief": ("☀️ Your brief", "Tap to read it in the chat."),    # not "morning": it comes when he's around
    "notice": ("🔔 Something you'll want to know", "Tap to see it in the chat."),
    "suggestion": ("💡 A suggestion", "Tap to see it in the chat."),
    "question": ("💬 A question for you", "Tap to answer in the chat."),
    "review": ("📊 Your weekly review is ready", "Tap to read it."),
    "memory": ("🧠 Want me to remember these?", "Tap to check this week's memories."),
}
MONTHS = r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(t|tember)?|oct(ober)?|nov(ember)?|dec(ember)?"
DATED = re.compile(rf"\b({MONTHS})\.? \d{{1,2}}\b|\b\d{{1,2}}/\d{{1,2}}(/\d{{2,4}})?\b|\btomorrow\b|\bnext (week|month)\b", re.IGNORECASE)
