"""Web pages (web_tools §8). Split out of core/web_tools.py 2026-11-03.

fetch_webpage(): a page's readable text (capped for the chat, longer for a watch); extract_page()
for sites that refuse a direct read.
"""
import json
import re

try:
    from .text import _clean
except ImportError:  # not imported as part of the core.web_tools package
    from core.web_text import _clean  # type: ignore[no-redef]

from core.observation_sanitizer import clean_web_text, strip_tracking_params


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


def extract_page(url: str) -> str:
    """A page's text as Tavily's extract service read it, for sites that refuse fetch_webpage (2026-11-03: Justia's
    docket answers 403 to it; extract returned the same 24,105 characters twice in a row). Free plan credits: only a
    fallback. Raises when there's no key or Tavily couldn't read it."""
    key = _wt().get_config("TAVILY_API_KEY") or _wt().get_config("TAVILY-API_KEY")
    if not key:
        raise RuntimeError("the page refused a direct read and there's no Tavily key for the fallback")
    j = _wt()._get_json("https://api.tavily.com/extract", data=json.dumps({"api_key": key, "urls": [url]}).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
    results = (j or {}).get("results") or [] if isinstance(j, dict) else []
    text = (results[0].get("raw_content") or "") if results else ""
    if not text.strip():
        raise RuntimeError("the page refused a direct read, and Tavily couldn't read it either")
    return f"Page: {strip_tracking_params(url)}\n" + clean_web_text(text)


def fetch_webpage(url: str, max_chars: int = 3000, cap: int = 6000) -> str:
    """cap: the most characters returned (6000 for the chat, whose prompt it joins; a page watch reads more)."""
    url = (url or "").strip()
    if re.match(r"^([a-z][a-z0-9+.-]*://|(file|data|javascript|mailto|ftp):)", url, re.IGNORECASE) and not re.match(r"^https?://", url, re.IGNORECASE):
        raise ValueError("only public http(s) web pages can be fetched")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    clean_url = strip_tracking_params(url)
    body, ctype = _wt()._get(url, check_host=True)
    text = body.decode("utf-8", "replace")
    if "html" in ctype or "<html" in text[:500].lower():
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.DOTALL | re.IGNORECASE)
        title = _clean(m.group(1)) if m else ""
        text = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", text)
        text = "\n".join(l for l in (_clean(l) for l in re.sub(r"<[^>]+>", " ", text).split("\n")) if l)
        text = f"{title}\n\n{text}" if title else text
        text = clean_web_text(text)
    elif not ctype.startswith("text/") and "json" not in ctype:
        return f"{clean_url} is not a readable text page ({ctype})."
    else:
        text = clean_web_text(text)
    limit = max(500, min(int(max_chars or 3000), int(cap or 6000)))
    return f"Page: {clean_url}\n" + (text[:limit] + f"\n…[page continues, {len(text) - limit} more characters]" if len(text) > limit else text)
