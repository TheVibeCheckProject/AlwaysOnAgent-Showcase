"""Pages and searches (core/watches.py §3)
========================================
page_lines() keeps the lines with real words; digits are blanked when comparing; _fetch_page()
reads a page directly, or through Tavily when the site refuses it; _search() runs a web search.

Split out of core/watches.py on 2026-11-04; moved into core/watches/ on 2026-11-04.
core/watches/__init__.py re-exports the public names for backwards compatibility.
"""
import datetime
import re
import sys
from typing import Any


def _w():
    """core.watches, looked up at call time so a test patching a name on it takes effect."""
    return sys.modules["core.watches"]


def _parse(ts: Any) -> datetime.datetime | None:
    try:
        d = datetime.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.astimezone()


def page_lines(text: str) -> list[str]:
    """A page's text as lines, without the fetcher's own header/trailer and lines with no real words."""
    out = []
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if not line or line.startswith(("Page: ", "…[page continues")):
            continue
        if len(re.sub(r"[\d\W_]+", "", line)) < 4:
            continue
        out.append(line)
    return out[:_w().PAGE_LINES]


def _blank_digits(line: str) -> str:
    return re.sub(r"\d+", "#", line)


def _fetch_page(url: str) -> str:
    """The page's text: a direct read, or, when the site refuses it (401/403/429), Tavily's copy (web_tools.
    extract_page: free credits, so only then)."""
    from core.web_tools import extract_page, fetch_webpage
    try:
        return fetch_webpage(url, max_chars=_w().PAGE_CHARS, cap=_w().PAGE_CHARS)
    except Exception as e:
        if not _w().REFUSED.search(str(e)):
            raise
        _w().logger.info(f"{url} refused a direct read ({e}); reading it through Tavily")
        return extract_page(url)


def _search(query: str) -> list[dict[str, str]]:
    from core.web_tools import search_results
    return [{"title": r.get("title") or r.get("url") or "", "url": r.get("url") or ""}
            for r in search_results(query, limit=8, recent=True)[2] if r.get("url")]
