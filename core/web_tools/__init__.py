"""
Live information from the internet for the chat assistant (all free, no API keys)
================================================================================
- Weather:    Open-Meteo (geocoding + forecast)
- Web search: DuckDuckGo Lite, falling back to Wikipedia search
- News:       Google News RSS
- Web pages:  fetch a public page and return its readable text

Every function returns plain text for the model and raises on failure (the caller turns errors
into text). Page fetching refuses anything that isn't a public internet address, including after
redirects, so the assistant can never be pointed at this PC, the HUD, or the local network.

MAP OF THIS PACKAGE
-------------------
Packaged 2026-11-04: the sections that were split out of core/web_tools.py on 2026-11-03 into
core/web_*.py now live here, one file per section; this module re-exports them, so
`from core.web_tools import ...` and patching names on this module (w._get, w._geocode, ...) keep
working exactly as before. The old flat files (core/web_fetch.py, ...) were thin shims that
re-exported from here; they were removed 2026-11-04.

  §1   Setup                         imports, the search providers' keys, timeouts
  §2   Safe fetching                 core/web_tools/fetch.py
  §3   Text helpers                  core/web_tools/text.py
  §4   Weather                       core/web_tools/weather.py
  §5   Search providers              core/web_tools/providers.py
  §6   Web search and research       core/web_tools/search.py
  §7   News                          core/web_tools/news.py
  §8   Web pages                     core/web_tools/pages.py
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §1  SETUP
#     Imports and settings: which search providers are used (keys from .env), timeouts and size caps.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
try:
    from core.config import get_config
except ImportError:
    from config import get_config  # type: ignore[no-redef]

from core.observation_sanitizer import clean_web_text, collapse_whitespace, strip_tracking_params

from .fetch import (
    MAX_PAGE_BYTES,
    TIMEOUT,
    USER_AGENT,
    _assert_peer_public,
    _assert_public_host,
    _CheckedHTTPHandler,
    _CheckedHTTPSHandler,
    _get,
    _get_json,
    _PeerCheckedHTTP,
    _PeerCheckedHTTPS,
    _SafeRedirects,
)
from .news import get_news
from .pages import extract_page, fetch_webpage
from .providers import (
    _DATE_NAMES,
    PUBLISHED_META,
    TOP_PAGE_CHARS,
    _brave,
    _ddg_lite,
    _serper,
    _tavily,
    _wikipedia,
    page_published,
    search_results,
)
from .search import web_research, web_search
from .text import _clean, _trim_to_sentence
from .weather import US_STATES, WMO_CODES, _geocode, _hours_old, _measured_now, get_weather

__all__ = [
    "MAX_PAGE_BYTES",
    "PUBLISHED_META",
    "TIMEOUT",
    "TOP_PAGE_CHARS",
    "USER_AGENT",
    "US_STATES",
    "WMO_CODES",
    "_CheckedHTTPHandler",
    "_CheckedHTTPSHandler",
    "_PeerCheckedHTTP",
    "_PeerCheckedHTTPS",
    "_SafeRedirects",
    "_assert_peer_public",
    "_assert_public_host",
    "_brave",
    "_clean",
    "_ddg_lite",
    "_geocode",
    "_get",
    "_get_json",
    "_hours_old",
    "_measured_now",
    "_serper",
    "_tavily",
    "_trim_to_sentence",
    "_wikipedia",
    "clean_web_text",
    "collapse_whitespace",
    "extract_page",
    "fetch_webpage",
    "get_config",
    "get_news",
    "get_weather",
    "page_published",
    "search_results",
    "strip_tracking_params",
    "web_research",
    "web_search",
]
