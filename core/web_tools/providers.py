"""Search providers (web_tools §5). Split out of core/web_tools.py 2026-11-03.

Each provider in turn until one answers; search_results() gives the raw results (with page text
and dates) to code that reads them itself (open loops, watches, the away shift).
"""
import html
import json
import logging
import re
import urllib.parse

try:
    from .text import _clean
except ImportError:  # not imported as part of the core.web_tools package
    from core.web_text import _clean  # type: ignore[no-redef]

logger = logging.getLogger("AlwaysOnAgent.WebTools")

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


def _tavily(query: str, limit: int, search_depth: str = "basic", raw: bool = False,
            recent: bool = False) -> tuple[str | None, list[dict[str, str]]]:
    key = _wt().get_config("TAVILY_API_KEY") or _wt().get_config("TAVILY-API_KEY")
    if not key:
        return None, []
    payload = json.dumps({
        "api_key": key,
        "query": query,
        "search_depth": search_depth,
        "include_answer": True,
        "max_results": limit,
        # The page's text as Tavily read it: sites that refuse fetch_webpage (PacerMonitor answers 429) still have it
        **({"include_raw_content": True} if raw else {}),
        **({"time_range": "week"} if recent else {}),
    }).encode("utf-8")
    try:
        j = _wt()._get_json("https://api.tavily.com/search", data=payload, headers={"Content-Type": "application/json"})
        if not isinstance(j, dict):
            return None, []
        answer = j.get("answer") or None
        results = []
        for r in j.get("results", []):
            results.append({
                "title": _clean(r.get("title", "")),
                "url": html.unescape(r.get("url", "")),
                "snippet": _clean(r.get("content", "")),
                "raw_content": r.get("raw_content", "") or "",
                "published": r.get("published_date") or "",
            })
        return answer, results
    except Exception as e:
        logger.debug(f"Tavily search failed: {e}")
        return None, []


def _serper(query: str, limit: int, recent: bool = False) -> tuple[str | None, list[dict[str, str]]]:
    key = _wt().get_config("SERPER_API_KEY") or _wt().get_config("SERPER-API_KEY")
    if not key:
        return None, []
    payload = json.dumps({
        "q": query,
        "num": limit,
        **({"tbs": "qdr:w"} if recent else {}),          # Google's "past week"
    }).encode("utf-8")
    try:
        j = _wt()._get_json("https://google.serper.dev/search", data=payload, headers={"X-API-KEY": key, "Content-Type": "application/json"})
        if not isinstance(j, dict):
            return None, []
        answer = None
        ab = j.get("answerBox")
        if isinstance(ab, dict):
            answer = ab.get("answer") or ab.get("snippet") or ab.get("title")
        results = []
        for r in j.get("organic", []):
            results.append({
                "title": _clean(r.get("title", "")),
                "url": html.unescape(r.get("link", "")),
                "snippet": _clean(r.get("snippet", "")),
                "raw_content": "",
                "published": r.get("date") or "",
            })
        return answer, results
    except Exception as e:
        logger.debug(f"Serper search failed: {e}")
        return None, []


def _brave(query: str, limit: int, recent: bool = False) -> tuple[str | None, list[dict[str, str]]]:
    key = _wt().get_config("BRAVE_SEARCH_API_KEY") or _wt().get_config("BRAVE_API_KEY")
    if not key:
        return None, []
    params = urllib.parse.urlencode({"q": query, "count": limit, **({"freshness": "pw"} if recent else {})})
    try:
        j = _wt()._get_json(f"https://api.search.brave.com/res/v1/web/search?{params}",
                      headers={"X-Subscription-Token": key, "Accept": "application/json"})
        if not isinstance(j, dict):
            return None, []
        results = []
        for r in j.get("web", {}).get("results", []):
            results.append({
                "title": _clean(r.get("title", "")),
                "url": html.unescape(r.get("url", "")),
                "snippet": _clean(r.get("description", "")),
                "raw_content": "",
                "published": r.get("page_age") or r.get("age") or "",
            })
        return None, results
    except Exception as e:
        logger.debug(f"Brave search failed: {e}")
        return None, []


def _ddg_lite(query: str, limit: int, recent: bool = False) -> list[dict[str, str]]:
    form = {"q": query, **({"df": "w"} if recent else {})}         # df=w: the past week
    body, _ = _wt()._get("https://lite.duckduckgo.com/lite/", data=urllib.parse.urlencode(form).encode())
    page = body.decode("utf-8", "replace")
    if "anomaly" in page.lower() and "result-link" not in page:
        raise RuntimeError("DuckDuckGo refused the request")
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*class=[\'"]result-link[\'"][^>]*>(.*?)</a>', page, re.DOTALL)
    snippets = re.findall(r"<td class=['\"]result-snippet['\"]>(.*?)</td>", page, re.DOTALL)
    out = []
    for i, (href, title) in enumerate(links):
        if "duckduckgo.com/y.js" in href:           # sponsored
            continue
        out.append({"title": _clean(title), "url": html.unescape(href), "snippet": _clean(snippets[i]) if i < len(snippets) else ""})
        if len(out) >= limit:
            break
    return out


def _wikipedia(query: str, limit: int) -> list[dict[str, str]]:
    j = _wt()._get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": limit}))
    return [{"title": r["title"], "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(r["title"].replace(" ", "_")),
             "snippet": _clean(r.get("snippet", ""))} for r in j.get("query", {}).get("search", [])]


TOP_PAGE_CHARS = 1500     # of the top search result's page, sent with the results


def search_results(query: str, limit: int = 6, raw: bool = False,
                   recent: bool = False) -> tuple[str, str | None, list[dict[str, str]]]:
    """(source, direct answer or None, [{"title", "url", "snippet", "raw_content"?, "published"?}]) from the same
    providers as web_search, for code that reads the results itself (the open-loop check, core/open_loops.py). raw:
    Tavily also returns each page's text. recent: the past week only (each provider's own filter; no Wikipedia), and
    "published" is the date a provider gives, as it gives it ("2026-10-27", "Oct 27, 2026", "3 days ago")."""
    limit = max(1, min(int(limit or 6), 10))
    source = "DuckDuckGo"
    answer = None
    results = []
    for name, provider in (("Tavily", lambda q, n: _tavily(q, n, raw=raw, recent=recent)),
                           ("Google", lambda q, n: _serper(q, n, recent=recent)),
                           ("Brave", lambda q, n: _brave(q, n, recent=recent))):
        ans, res = provider(query, limit)
        if res:
            return name, ans, res
    try:
        results = _ddg_lite(query, limit, recent=recent)
    except Exception as e:
        logger.debug(f"DuckDuckGo Lite search failed: {e}")
        results = []
    if not results and not recent:                 # an encyclopedia has no "this week"
        source, results = "Wikipedia", _wikipedia(query, limit)
    return source, answer, results


_DATE_NAMES = r"(?:article:published_time|og:published_time|datePublished|date|pubdate|publish-date|parsely-pub-date|sailthru\.date|dc\.date)"
PUBLISHED_META = re.compile(
    r"(?:property|name|itemprop)=[\"']" + _DATE_NAMES + r"[\"'][^>]*content=[\"']([^\"']+)"
    r"|content=[\"']([^\"']+)[\"'][^>]*(?:property|name|itemprop)=[\"']" + _DATE_NAMES + r"[\"']"
    r'|"datePublished"\s*:\s*"([^"]+)"'
    r"|<time[^>]+datetime=[\"']([^\"']+)", re.IGNORECASE)


def page_published(url: str) -> str:
    """When a public page says it was published (its meta tags, JSON-LD or first <time>), as written; "" if it
    doesn't say. Used to keep old articles from being told as news (2026-10-29: a May 28 article about a Claude release
    was sent as fresh news on Oct 29)."""
    body, _ = _wt()._get(url, check_host=True)
    m = PUBLISHED_META.search(body[:400_000].decode("utf-8", "replace"))
    return next((g for g in m.groups() if g), "") if m else ""
