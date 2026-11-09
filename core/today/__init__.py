"""
Today: DeskAI's second screen, an edition of things for Alex (step 2 of docs/FEED_AND_DESIGN_PLAN_2026-10-27.md
in the DeskAI repo; Alex named it "Today")
==================================================================================================================
Not an endless feed: an *edition* of a few cards, put together when Alex opens Today after being away (at most every
EDITION_EVERY, and only when something is new), then "You're caught up". Never on a clock.

Where the cards come from (all already made by the agent; Today makes no AI call):
- story: interest findings with a link (core/away_shift/). The page's own title, site name and preview image
  (og:image) are read once and the image is kept here; no image (or one under MIN_IMAGE_WIDTH) gives a text card.
- video: new uploads on followed YouTube channels (core/feeds.py), with YouTube's thumbnail.
- settled: what the open-loops check found out (core/open_loops/'s "open-loops" notices).
- done: background tasks finished since the last edition.
- question: the best curious question still waiting (core/candidates/), answered from the card.
The chat sends far fewer of these than the agent thinks of (2026-10-28: 16 of ~260 in two weeks). Today shows the
best of the rest without interrupting the chat. Findings dropped only as a same-topic repeat still count; ones the
ranker rejected (heavy, generic, off-topic) don't.

Stored here: today_editions (when each was built, its header) and today_items (one row per card, with what Alex did
with it: seen, saved, less/more, dismissed, answered). Pages and images are other people's content: shown, never
followed as instructions.

How it works: the app asks for Today (GET /api/today) -> Today.edition() (§5) builds a new edition first if one
is due (§6: gathered from the candidate pool, followed channels, settled loops, finished tasks, the daily extras
from core/discover.py; pictures fetched in §7), then returns the running list with the header (date, weather
§3, the day's brief) and the recipe (§4). Trivia and "on this day" go to the app's decks (in_deck, §1).

MAP OF THIS FILE
----------------
Each section lives in its own file inside this package now (same § numbers); search for the § number to jump
straight to it.

Split 2026-11-03, packaged 2026-11-04: each section now lives in its own file in core/today/ (same § numbers).
This file keeps the class shell, __init__ and the re-exports, so `from core.today import ...` still works.
The old flat files (core/today_<name>.py) and core/today.py were thin re-export shims; they were
removed 2026-11-04. (A package shadows a same-named module file, so `import core.today` always
loaded this file, not the shim.)

  §1   Setup                         core/today/constants.py — timing, sizes, the decks rule (in_deck)
  §2   Links and page previews       core/today/links.py — one link; a page's title, site, preview image
  §3   The weather                   core/today/weather.py — the weather now at your town, for the header
  §4   The recipe                    core/today/recipe.py — your words into rules; applying them; channels
  §5   Today: reading it             core/today/reading.py — the edition, header, saved cards, reactions
  §6   Building an edition           core/today/build.py — when one is due; gathering cards; storing it
  §7   Pictures                      core/today/pictures.py — card images: fetched, checked, shrunk, pruned
"""


import datetime
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("AlwaysOnAgent.Today")


try:
    from .build import _BuildingMixin
    from .constants import (
        _BUILD_LOCK,
        ACTIONS,
        BRIEF_SHOWN_FOR,
        BUILD_FETCH_SECONDS,
        DECK_MAX,
        EDITION_EVERY,
        FEED_RECHECK,
        FEED_SECONDS,
        FETCH_WORKERS,
        FIRST_LOOKBACK,
        IMAGE_MAX_WIDTH,
        KEEP_IMAGES,
        MAX_CARDS,
        MAX_IMAGE_PIXELS,
        MAX_STORIES,
        MIN_IMAGE_WIDTH,
        RUNNING_MAX,
        WEATHER_KEEP,
        in_deck,
    )
    from .links import _now, _parse, _web_get, link_key, page_preview
    from .pictures import _PicturesMixin
    from .reading import _ReadingMixin
    from .recipe import (
        CARD_KINDS,
        EMPTY_RULES,
        MAX_TOPICS,
        RECIPE_MAX_CHARS,
        RECIPE_PROMPT,
        RECIPE_STATE,
        _matches,
        apply_rules,
        compile_recipe,
        llm_asker,
        normalize_rules,
        recipe,
        recipe_summary,
        save_recipe,
    )
    from .weather import WEATHER_WORDS, current_weather
except ImportError:
    from core.today.build import _BuildingMixin
    from core.today.constants import (
        _BUILD_LOCK,
        ACTIONS,
        BRIEF_SHOWN_FOR,
        BUILD_FETCH_SECONDS,
        DECK_MAX,
        EDITION_EVERY,
        FEED_RECHECK,
        FEED_SECONDS,
        FETCH_WORKERS,
        FIRST_LOOKBACK,
        IMAGE_MAX_WIDTH,
        KEEP_IMAGES,
        MAX_CARDS,
        MAX_IMAGE_PIXELS,
        MAX_STORIES,
        MIN_IMAGE_WIDTH,
        RUNNING_MAX,
        WEATHER_KEEP,
        in_deck,
    )
    from core.today.links import _now, _parse, _web_get, link_key, page_preview
    from core.today.pictures import _PicturesMixin
    from core.today.reading import _ReadingMixin
    from core.today.recipe import (
        CARD_KINDS,
        EMPTY_RULES,
        MAX_TOPICS,
        RECIPE_MAX_CHARS,
        RECIPE_PROMPT,
        RECIPE_STATE,
        _matches,
        apply_rules,
        compile_recipe,
        llm_asker,
        normalize_rules,
        recipe,
        recipe_summary,
        save_recipe,
    )
    from core.today.weather import WEATHER_WORDS, current_weather


__all__ = [
    "ACTIONS",
    "BRIEF_SHOWN_FOR",
    "BUILD_FETCH_SECONDS",
    "CARD_KINDS",
    "DECK_MAX",
    "EDITION_EVERY",
    "EMPTY_RULES",
    "FEED_RECHECK",
    "FEED_SECONDS",
    "FETCH_WORKERS",
    "FIRST_LOOKBACK",
    "IMAGE_MAX_WIDTH",
    "KEEP_IMAGES",
    "MAX_CARDS",
    "MAX_IMAGE_PIXELS",
    "MAX_STORIES",
    "MAX_TOPICS",
    "MIN_IMAGE_WIDTH",
    "RECIPE_MAX_CHARS",
    "RECIPE_PROMPT",
    "RECIPE_STATE",
    "RUNNING_MAX",
    "WEATHER_KEEP",
    "WEATHER_WORDS",
    "_BUILD_LOCK",
    "Today",
    "_matches",
    "_now",
    "_parse",
    "_web_get",
    "apply_rules",
    "compile_recipe",
    "current_weather",
    "in_deck",
    "link_key",
    "llm_asker",
    "normalize_rules",
    "page_preview",
    "recipe",
    "recipe_summary",
    "save_recipe",
]


class Today(_ReadingMixin, _BuildingMixin, _PicturesMixin):
    def __init__(self, db, images_dir: Path, now: Callable[[], datetime.datetime] = _now,
                 fetch: Callable[[str], tuple[bytes, str]] = _web_get,
                 weather: Callable[[str], dict[str, Any] | None] = current_weather,
                 location: Callable[[], str] = lambda: "",
                 feed_fetch: Callable[[str], str] | None = None):
        self.db, self.images_dir, self.now = db, Path(images_dir), now
        self.fetch, self.weather, self.location = fetch, weather, location
        if feed_fetch is None:
            from core.feeds import _get_text as feed_fetch
        self.feed_fetch = feed_fetch                  # YouTube pages and feeds (core/feeds.py), as text
        self._weather_cache: tuple[str, datetime.datetime, Any] | None = None
        with db.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS today_editions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    built_at TEXT NOT NULL,
                    header_json TEXT
                );
                CREATE TABLE IF NOT EXISTS today_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    edition INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    card TEXT NOT NULL,             -- story | video | settled | done | question
                    title TEXT,
                    body TEXT,
                    link TEXT,
                    link_key TEXT,
                    site TEXT,
                    image TEXT,                     -- file name in images_dir, or NULL
                    why TEXT,
                    source_ref TEXT,                -- "candidate:12", "message:40", "tasks:3,4"
                    created_at TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'new'   -- new | seen | saved | less | more | dismissed | answered
                );
                CREATE INDEX IF NOT EXISTS idx_today_items ON today_items (edition, position);
            """)
