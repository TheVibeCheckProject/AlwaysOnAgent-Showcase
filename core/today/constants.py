"""Today §1: setup constants — edition timing, list sizes, picture limits, the decks rule.

Moved into core/today/constants.py on 2026-11-04 (was core/today_constants.py, split out of
core/today.py §1 on 2026-11-03); core/today_constants.py is now a re-export shim.
"""
import datetime
import threading
from typing import Any

EDITION_EVERY = datetime.timedelta(hours=6)
FIRST_LOOKBACK = datetime.timedelta(days=3)       # what the very first edition may show
MAX_CARDS = 10
RUNNING_MAX = 20                                  # Today's running list: this many of the newest cards (Alex, Oct 29)
DECK_MAX = 14                                     # trivia and "on this day" cards each, for the app's decks (Nov 3)


def in_deck(item: dict[str, Any]) -> bool:
    """A card for the app's decks rather than the feed: trivia, and Wikipedia's "on this day"."""
    return item.get("card") == "trivia" or str(item.get("source_ref") or "").startswith("discover:onthisday")


MAX_STORIES = 6                                   # stories and videos together, before the compact cards
MIN_IMAGE_WIDTH = 400                             # smaller previews look broken full-bleed: a text card instead
IMAGE_MAX_WIDTH = 1080
MAX_IMAGE_PIXELS = 25_000_000                     # a preview image larger than this isn't decoded (Pillow allows 179M)
BUILD_FETCH_SECONDS = 20                          # an edition's page and image downloads, all together, at most
FEED_RECHECK = datetime.timedelta(hours=1)        # a refresh reads followed channels again if their last read is older
FEED_SECONDS = 15                                 # no channel is started after this long into a refresh
FETCH_WORKERS = 6
_BUILD_LOCK = threading.Lock()                    # one edition built at a time (the app opening Today twice at once)
KEEP_IMAGES = datetime.timedelta(days=14)         # unsaved cards' images are deleted after this
WEATHER_KEEP = datetime.timedelta(minutes=30)     # the header's weather is read again after this
BRIEF_SHOWN_FOR = datetime.timedelta(hours=16)    # the day's brief stays in the header this long


# right / wrong: the owner's answer to a trivia card (the month's score is in the header)
ACTIONS = {"right": "right", "wrong": "wrong", "seen": "seen", "save": "saved", "unsave": "seen", "less": "less", "more": "more",
           "dismiss": "dismissed", "answered": "answered"}
