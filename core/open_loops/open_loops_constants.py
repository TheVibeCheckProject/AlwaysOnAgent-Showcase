"""Setup constants for the open-loops check (core/open_loops.py §1).

Split out of core/open_loops.py on 2026-11-03; core/open_loops re-exports the public names for backwards compatibility.
Moved into core/open_loops/ on 2026-11-04.
"""

import datetime
import logging

# A child of the daemon's logger, so its lines reach logs/worker.log (as "OpenLoops" they went nowhere on the Mini;
# found 2026-10-13 checking the first open-loop run)
logger = logging.getLogger("AlwaysOnAgent.OpenLoops")

CHECK_EVERY = datetime.timedelta(hours=20)
ASK_AGAIN_DAYS = 14
DEFAULT_WAIT_DAYS = 7            # check_after when the fact gives no date
TAG_BATCH = 40
MAX_CHECKS = 12                  # loops checked in one run
LOOP_CATEGORIES = ("plan", "fact", "project")      # profiles, people, preferences and instructions don't resolve
STATE_LAST = "open_loops_last"
SOURCE_NAMES = {"F": "a newer memory", "M": "the conversation", "E": "an email", "W": "a web search",
                "R": "a web page"}
RESEARCH_PAGES, PAGE_TEXT = 2, 60000       # pages read when the search didn't return their text, and how much
PAGE_CHARS = 2500                          # of a page goes to the AI (the parts around the matter's words)
MAX_QUERIES = 2
CADENCE_NORMAL = datetime.timedelta(hours=48)
CADENCE_NEAR = datetime.timedelta(hours=24)
NEAR_WINDOW_DAYS = 3
# A loop whose research keeps finding nothing backs off (x2 per empty run) to at most weekly. 2026-10-27: the federal
# case's service of process was researched every 1-2 days from Oct 17 with no news (16 AI calls, ~50K tokens a week);
# a mention in chat, a new email or a newer fact still triggers a look at once (_cheap_flagged)
CADENCE_MAX = datetime.timedelta(days=7)
