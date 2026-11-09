"""Split out of core/away_shift.py §1 on 2026-11-03; core/away_shift/__init__.py re-exports these names.
Setup constants: timings, the daily search budget, state keys.
"""
import datetime

AWAY_TRIGGER_SECONDS = 2 * 3600      # 2 hours without an open app stream
SESSION_GAP_SECONDS = 2 * 3600       # at least 2 hours between session starts
DAILY_SEARCH_CAP = 15                # max total web searches per calendar day across background work
MAX_INTEREST_SEARCHES = 3            # per session
POOL_LOW_WATERMARK = 6               # refresh pool candidates when fewer than 6 pending
# A generator whose last batch was all dropped waits before trying again, unless there's new material (Alex wrote
# GEN_NEW_MESSAGES more messages, or a new fact). Until 2026-10-27 dropped candidates didn't count as pending, so
# every session (2 h apart) made the same subjects again: 246 of 266 dropped in two weeks, the sinkhole cave 39 times
# (the 2026-10-27 background audit, B2).
GEN_WAIT_AFTER_EMPTY = (datetime.timedelta(hours=6), datetime.timedelta(hours=24))
GEN_NEW_MESSAGES = 5
GEN_NEW_FACTS = 3                    # new facts (ids) that count as new material
FULL_UPKEEP_EVERY = datetime.timedelta(days=7)
STATE_LAST_FULL_UPKEEP = "away_shift_last_full_upkeep"
STATE_GEN_PREFIX = "away_shift_gen_"
COVERED_DAYS_ASKED, COVERED_DAYS_DROPPED = 30, 7

STATE_LAST_SESSION = "away_shift_last_session"
STATE_SEARCHES_PREFIX = "away_shift_searches_"
STATE_LAST_PATTERNS = "away_shift_last_patterns"
STATE_LAST_SUMMARY = "away_shift_last_summary"
STATE_LAST_RECORD = "away_shift_last_record"
