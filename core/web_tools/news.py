"""News (web_tools §7). Split out of core/web_tools.py 2026-11-03.

get_news(): headlines, outlets and dates (the links are Google's redirects, so they're left out).
"""
import urllib.parse
import xml.etree.ElementTree as ET


def _wt():
    """core.web_tools, looked up at call time. Tests patch names on that module (w._get, w._geocode,
    w._assert_public_host, ...), so every call to a former web_tools function goes through it here;
    a direct import-time binding would silently ignore the patch."""
    try:
        from core import web_tools
        return web_tools
    except ImportError:  # running from core/ directly
        import web_tools  # type: ignore[no-redef]
        return web_tools


def get_news(query: str, limit: int = 8) -> str:
    body, _ = _wt()._get("https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}))
    items = ET.fromstring(body).findall("./channel/item")[:max(1, min(int(limit or 8), 15))]
    if not items:
        return f"No recent news for '{query}'."
    lines = [f"Recent news for '{query}' (source: Google News):"]
    for it in items:
        src = it.find("source")
        # Google News links are long redirect tokens: headline, outlet and date carry the information
        lines.append(f"- {it.findtext('title', '').strip()} ({src.text if src is not None else 'unknown source'}, "
                     f"{it.findtext('pubDate', '')[:16]})")
    return "\n".join(lines)
