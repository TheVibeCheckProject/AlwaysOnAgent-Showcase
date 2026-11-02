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
"""

import concurrent.futures
import html
import http.client
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

try:
    from core.config import get_config
except ImportError:
    from config import get_config

from core.observation_sanitizer import clean_web_text, collapse_whitespace, strip_tracking_params

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AlwaysOnAgent/1.0 (personal assistant)"
TIMEOUT = 15
MAX_PAGE_BYTES = 2_000_000

US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
    "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan", "mn": "minnesota",
    "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina",
    "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee", "tx": "texas",
    "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}

WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "light freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "light freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "light snow showers", 86: "snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


# ---------------------------------------------------------------------------- safe HTTP
def _assert_public_host(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("only public http(s) addresses can be fetched")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror:
        raise ValueError(f"can't resolve {parts.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise ValueError("that address is on this PC or the local network, which is off limits")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_public_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _assert_peer_public(sock: socket.socket) -> None:
    """
    Checks the address actually connected to. The name is looked up again when connecting, so a
    host that answers with a public address for the check and this PC for the connection (DNS
    rebinding) is caught here, before any request is sent.
    """
    ip = ipaddress.ip_address(sock.getpeername()[0].split("%")[0])
    if not ip.is_global:
        sock.close()
        raise ValueError("that address is on this PC or the local network, which is off limits")


class _PeerCheckedHTTP(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        _assert_peer_public(self.sock)


class _PeerCheckedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        _assert_peer_public(self.sock)


class _CheckedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PeerCheckedHTTP, req)


class _CheckedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PeerCheckedHTTPS, req, context=self._context)


_opener = urllib.request.build_opener(_SafeRedirects)
# For addresses the model chose (web pages): every connection, redirects included, must be public
_checked_opener = urllib.request.build_opener(_SafeRedirects, _CheckedHTTPHandler, _CheckedHTTPSHandler)


def _get(url: str, data: Optional[bytes] = None, check_host: bool = False, headers: Optional[Dict[str, str]] = None) -> Tuple[bytes, str]:
    if check_host:
        _assert_public_host(url)
    req_headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers)
    with (_checked_opener if check_host else _opener).open(req, timeout=TIMEOUT) as resp:
        return resp.read(MAX_PAGE_BYTES), resp.headers.get_content_type() or ""


def _get_json(url: str, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None) -> Any:
    if headers:
        try:
            body, _ = _get(url, data=data, headers=headers)
        except TypeError:
            body, _ = _get(url, data=data)
    else:
        body, _ = _get(url, data=data)
    return json.loads(body.decode("utf-8", "replace"))


def _clean(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _trim_to_sentence(text: str, max_chars: int) -> str:
    """Trims text up to max_chars without cutting mid-sentence, falling back to word boundary."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Find last sentence-ending punctuation followed by whitespace or end
    matches = list(re.finditer(r"[.!?](\s+|$)", cut))
    if matches:
        last_end = matches[-1].end()
        if last_end >= int(max_chars * 0.55):
            return text[:last_end].strip()
    # Fallback to word boundary
    last_space = cut.rfind(" ")
    if last_space > int(max_chars * 0.55):
        return text[:last_space].strip() + "…"
    return cut.strip() + "…"


# ---------------------------------------------------------------------------- weather
def _geocode(location: str) -> Dict[str, Any]:
    """'Springfield, Illinois' / 'Springfield, IL' / 'Paris, France' / 'Springfield Illinois' -> best matching place."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        raise ValueError("no location given")
    name, hint = parts[0], " ".join(parts[1:]).lower()
    hint = US_STATES.get(hint, hint)
    results = _get_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": name, "count": 25, "language": "en", "format": "json"})).get("results") or []
    if not results and not hint and " " in name:
        # No comma: try splitting "Springfield Illinois" into place + region, longest place name first
        words = name.split()
        for i in range(len(words) - 1, 0, -1):
            try:
                return _geocode(" ".join(words[:i]) + ", " + " ".join(words[i:]))
            except ValueError:
                continue
    if not results:
        raise ValueError(f"couldn't find a place called '{location}'")
    if not hint:
        return results[0]
    fields = lambda r: [(r.get(k) or "").lower() for k in ("admin1", "country", "country_code")]
    for r in results:                                 # exact region/country first ("virginia" != "west virginia")
        if hint in fields(r):
            return r
    for r in results:
        if any(hint in f for f in fields(r) if f):
            return r
    raise ValueError(f"found '{name}' but none in '{parts[1]}'. Places found: " +
                     "; ".join(f"{r['name']}, {r.get('admin1', '')}, {r.get('country', '')}" for r in results[:6]))


def _hours_old(stamp: Optional[str]) -> float:
    import datetime as _dt
    try:
        t = _dt.datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
        return (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() / 3600
    except ValueError:
        return float("inf")


def _measured_now(lat: float, lon: float) -> Optional[str]:
    """The latest reading from the nearest US weather station (National Weather Service, free, no key).
    Open-Meteo's "current" is a forecast model's estimate and ran 5 °F off the owner's weather app on a clear
    evening (2026-10-09: 74 vs 69); a station measures it. None outside the US or when the service doesn't answer."""
    try:
        hdr = {"Accept": "application/geo+json"}
        pt = _get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", headers=hdr)
        stations = _get_json(pt["properties"]["observationStations"], headers=hdr)["features"]
        for st in stations[:3]:
            o = _get_json(st["id"] + "/observations/latest", headers=hdr)["properties"]
            c = (o.get("temperature") or {}).get("value")
            if c is None or _hours_old(o.get("timestamp")) > 1.5:     # a stale "latest" is the afternoon's reading
                continue
            name = st.get("properties", {}).get("name") or st["id"].rsplit("/", 1)[-1]
            when = (o.get("timestamp") or "")[11:16]
            return f"{c * 9 / 5 + 32:.0f}°F measured at {name}" + (f" ({when} UTC)" if when else "")
    except Exception:
        return None
    return None


def get_weather(location: str, days: int = 3) -> str:
    place = _geocode(location)
    us = place.get("country_code") == "US"
    q = {
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": max(1, min(int(days or 3), 7)),
        "temperature_unit": "fahrenheit" if us else "celsius", "wind_speed_unit": "mph" if us else "kmh",
        "precipitation_unit": "inch" if us else "mm",
    }
    w = _get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q))
    t, sp = ("°F", "mph") if us else ("°C", "km/h")
    c = w["current"]
    where = ", ".join(x for x in (place["name"], place.get("admin1"), place.get("country")) if x)
    # A missing reading is shown as n/a, never as 0 (that would be a made-up temperature)
    n = lambda v, unit="": "n/a" if v is None else f"{v:.0f}{unit}"
    lines = [f"Weather for {where} (source: Open-Meteo, local time {c['time'].replace('T', ' ')}):",
             f"RIGHT NOW (the current temperature; the days below are forecasts): {WMO_CODES.get(c.get('weather_code'), 'unknown conditions')}, {n(c.get('temperature_2m'), t)} "
             f"(feels like {n(c.get('apparent_temperature'), t)}), humidity {n(c.get('relative_humidity_2m'), '%')}, "
             f"wind {n(c.get('wind_speed_10m'))} {sp}."]
    if us and (m := _measured_now(place["latitude"], place["longitude"])):
        lines.insert(1, f"RIGHT NOW, MEASURED (use this one for the temperature now): {m}, National Weather Service. "
                        "The line below is Open-Meteo's model estimate.")
    d = w["daily"]
    for i, day in enumerate(d["time"]):
        lines.append(f"Forecast {day}: {WMO_CODES.get(d['weather_code'][i], '?')}, high {n(d['temperature_2m_max'][i], t)}, "
                     f"low {n(d['temperature_2m_min'][i], t)}, chance of precipitation "
                     f"{n(d['precipitation_probability_max'][i], '%')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------- search & news
def _tavily(query: str, limit: int, search_depth: str = "basic", raw: bool = False,
            recent: bool = False) -> Tuple[Optional[str], List[Dict[str, str]]]:
    key = get_config("TAVILY_API_KEY") or get_config("TAVILY-API_KEY")
    if not key:
        return None, []
    payload = json.dumps({
        "api_key": key,
        "query": query,
        "search_depth": search_depth,
        "include_answer": True,
        "max_results": limit,
        # The page's text as Tavily read it: sites that refuse fetch_webpage (some records sites answer 429) still have it
        **({"include_raw_content": True} if raw else {}),
        **({"time_range": "week"} if recent else {}),
    }).encode("utf-8")
    try:
        j = _get_json("https://api.tavily.com/search", data=payload, headers={"Content-Type": "application/json"})
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
    except Exception:
        return None, []


def _serper(query: str, limit: int, recent: bool = False) -> Tuple[Optional[str], List[Dict[str, str]]]:
    key = get_config("SERPER_API_KEY") or get_config("SERPER-API_KEY")
    if not key:
        return None, []
    payload = json.dumps({
        "q": query,
        "num": limit,
        **({"tbs": "qdr:w"} if recent else {}),          # Google's "past week"
    }).encode("utf-8")
    try:
        j = _get_json("https://google.serper.dev/search", data=payload, headers={"X-API-KEY": key, "Content-Type": "application/json"})
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
    except Exception:
        return None, []


def _brave(query: str, limit: int, recent: bool = False) -> Tuple[Optional[str], List[Dict[str, str]]]:
    key = get_config("BRAVE_SEARCH_API_KEY") or get_config("BRAVE_API_KEY")
    if not key:
        return None, []
    params = urllib.parse.urlencode({"q": query, "count": limit, **({"freshness": "pw"} if recent else {})})
    try:
        j = _get_json(f"https://api.search.brave.com/res/v1/web/search?{params}",
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
    except Exception:
        return None, []


def _ddg_lite(query: str, limit: int, recent: bool = False) -> List[Dict[str, str]]:
    form = {"q": query, **({"df": "w"} if recent else {})}         # df=w: the past week
    body, _ = _get("https://lite.duckduckgo.com/lite/", data=urllib.parse.urlencode(form).encode())
    page = body.decode("utf-8", "replace")
    if "anomaly" in page.lower() and "result-link" not in page:
        raise RuntimeError("DuckDuckGo refused the request")
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*class=[\'"]result-link[\'"][^>]*>(.*?)</a>', page, re.S)
    snippets = re.findall(r"<td class=['\"]result-snippet['\"]>(.*?)</td>", page, re.S)
    out = []
    for i, (href, title) in enumerate(links):
        if "duckduckgo.com/y.js" in href:           # sponsored
            continue
        out.append({"title": _clean(title), "url": html.unescape(href), "snippet": _clean(snippets[i]) if i < len(snippets) else ""})
        if len(out) >= limit:
            break
    return out


def _wikipedia(query: str, limit: int) -> List[Dict[str, str]]:
    j = _get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": limit}))
    return [{"title": r["title"], "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(r["title"].replace(" ", "_")),
             "snippet": _clean(r.get("snippet", ""))} for r in j.get("query", {}).get("search", [])]


TOP_PAGE_CHARS = 1500     # of the top search result's page, sent with the results


def search_results(query: str, limit: int = 6, raw: bool = False,
                   recent: bool = False) -> Tuple[str, Optional[str], List[Dict[str, str]]]:
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
    except Exception:
        results = []
    if not results and not recent:                 # an encyclopedia has no "this week"
        source, results = "Wikipedia", _wikipedia(query, limit)
    return source, answer, results


_DATE_NAMES = r"(?:article:published_time|og:published_time|datePublished|date|pubdate|publish-date|parsely-pub-date|sailthru\.date|dc\.date)"
PUBLISHED_META = re.compile(
    r"(?:property|name|itemprop)=[\"']" + _DATE_NAMES + r"[\"'][^>]*content=[\"']([^\"']+)"
    r"|content=[\"']([^\"']+)[\"'][^>]*(?:property|name|itemprop)=[\"']" + _DATE_NAMES + r"[\"']"
    r'|"datePublished"\s*:\s*"([^"]+)"'
    r"|<time[^>]+datetime=[\"']([^\"']+)", re.I)


def page_published(url: str) -> str:
    """When a public page says it was published (its meta tags, JSON-LD or first <time>), as written; "" if it
    doesn't say. Used to keep old articles from being told as news (2026-10-29: a May 28 article about a Claude release
    was sent as fresh news on Oct 29)."""
    body, _ = _get(url, check_host=True)
    m = PUBLISHED_META.search(body[:400_000].decode("utf-8", "replace"))
    return next((g for g in m.groups() if g), "") if m else ""


def web_search(query: str, limit: int = 6) -> str:
    limit = max(1, min(int(limit or 6), 10))
    source = "DuckDuckGo"
    answer = None
    results = []

    # 1. Tavily AI Search (Highest quality AI-native search with clean content)
    ans, res = _tavily(query, limit)
    if res:
        source, answer, results = "Tavily", ans, res

    # 2. Serper (Direct Google Search SERP)
    if not results:
        ans, res = _serper(query, limit)
        if res:
            source, answer, results = "Google", ans, res

    # 3. Brave Search API
    if not results:
        ans, res = _brave(query, limit)
        if res:
            source, answer, results = "Brave", ans, res

    # 4. DuckDuckGo Lite (Free zero-key)
    if not results:
        try:
            results = _ddg_lite(query, limit)
            source = "DuckDuckGo"
        except Exception:
            results = []

    # 5. Wikipedia (Fallback)
    if not results:
        source, results = "Wikipedia", _wikipedia(query, limit)

    if not results:
        return f"No results for '{query}'."

    # The top page's text comes along: chat used to spend a whole extra model request (of 20 free a day on the
    # Flash models) on fetch_webpage after most searches (2026-10-05)
    page = ""
    for r in [r for r in results if (r.get("url") or "").startswith("http")][:2]:
        try:
            page = fetch_webpage(r["url"], max_chars=TOP_PAGE_CHARS)
            break
        except Exception:
            continue

    lines = [f"Search results for '{query}' (source: {source}):"]
    if answer:
        lines.append(f"Direct answer: {answer.strip()}\n")
    for i, r in enumerate(results, 1):
        snippet = _trim_to_sentence(r["snippet"], 300 if page else 450)
        lines.append(f"{i}. {r['title']}\n   {strip_tracking_params(r['url'])}\n   {snippet}")
    if page:
        lines.append(f"\nText of the top result (first {TOP_PAGE_CHARS:,} characters; fetch_webpage reads more of "
                     f"it or another result):\n{page}")
    return "\n".join(lines)


def web_research(query: str, limit: int = 3) -> str:
    limit = max(1, min(int(limit or 3), 5))
    source = "DuckDuckGo"
    answer = None
    results = []

    # 1. Tavily (uses advanced depth for deep research)
    ans, res = _tavily(query, limit, search_depth="advanced")
    if res:
        source, answer, results = "Tavily", ans, res

    # 2. Serper (Google)
    if not results:
        ans, res = _serper(query, limit)
        if res:
            source, answer, results = "Google", ans, res

    # 3. Brave
    if not results:
        ans, res = _brave(query, limit)
        if res:
            source, answer, results = "Brave", ans, res

    # 4. DuckDuckGo
    if not results:
        try:
            results = _ddg_lite(query, limit)
            source = "DuckDuckGo"
        except Exception:
            results = []

    # 5. Wikipedia
    if not results:
        source, results = "Wikipedia", _wikipedia(query, limit)

    if not results:
        return f"No research results for '{query}'."

    target_results = results[:limit]
    pages_text: Dict[str, str] = {}

    def _fetch_safe(url: str) -> Tuple[str, str]:
        try:
            return url, fetch_webpage(url, max_chars=4000)
        except Exception:
            return url, ""

    urls = [r["url"] for r in target_results if r.get("url") and r["url"].startswith("http")]
    if urls:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(urls), 3)) as executor:
            future_to_url = {executor.submit(_fetch_safe, u): u for u in urls}
            for fut in concurrent.futures.as_completed(future_to_url):
                try:
                    u, page_body = fut.result()
                    if page_body:
                        pages_text[u] = page_body
                except Exception:
                    pass

    # Extract candidate passages from pages or fallback to snippet / raw_content
    candidate_passages: List[Tuple[str, str, str]] = []  # (text, title, url)
    for r in target_results:
        u = r.get("url", "")
        title = r.get("title", "")
        body = pages_text.get(u) or r.get("raw_content") or r.get("snippet", "")
        paragraphs = [p.strip() for p in re.split(r"\n{2,}|\n(?=[A-Z0-9#*-])", body) if p.strip()]
        for p in paragraphs:
            if len(p) >= 70 and not re.match(r"^(Page:|Search results|Home|Menu|Copyright)", p, re.I):
                candidate_passages.append((p, title, u))

    if not candidate_passages:
        for r in target_results:
            if r.get("snippet"):
                candidate_passages.append((r["snippet"], r.get("title", ""), r.get("url", "")))

    # Semantic Reranking with local embedder (BGE-small in core/embedder.py)
    ranked: List[Tuple[str, str, str]] = []
    embedder_scored = False
    try:
        from core.embedder import get_embedder
        emb = get_embedder()
        if emb and emb.ensure_loaded() and candidate_passages:
            texts = [cp[0] for cp in candidate_passages]
            q_vec = emb.embed([query], query=True)
            p_vecs = emb.embed(texts, query=False)
            if q_vec is not None and p_vecs is not None and len(p_vecs) > 0:
                scores = (q_vec @ p_vecs.T)[0]
                scored = list(zip(scores, candidate_passages))
                scored.sort(key=lambda x: x[0], reverse=True)
                ranked = [cp for _, cp in scored]
                embedder_scored = True
    except Exception:
        pass

    if not embedder_scored:
        terms = [re.escape(w.lower()) for w in re.findall(r"\w+", query) if len(w) > 2]
        def _score(p_text: str) -> int:
            p_low = p_text.lower()
            return sum(len(re.findall(t, p_low)) for t in terms)
        ranked = sorted(candidate_passages, key=lambda cp: _score(cp[0]), reverse=True)

    # De-duplicate passages: at most 2 per source
    selected_passages: List[Tuple[str, str, str]] = []
    seen_urls: Dict[str, int] = {}
    for p_text, p_title, p_url in ranked:
        if seen_urls.get(p_url, 0) >= 2:
            continue
        selected_passages.append((p_text, p_title, p_url))
        seen_urls[p_url] = seen_urls.get(p_url, 0) + 1
        if len(selected_passages) >= 4:
            break

    lines = [f"Research dossier for '{query}' (source: {source}):"]
    if answer:
        lines.append(f"Summary / Direct Answer:\n{answer.strip()}\n")

    lines.append("Key Evidence & Cited Findings:")
    char_count = sum(len(l) for l in lines)
    for i, (p_text, p_title, p_url) in enumerate(selected_passages, 1):
        clean_u = strip_tracking_params(p_url)
        # Allow healthy paragraphs up to ~950 chars, trimmed at clean sentence boundary
        p_slice = _trim_to_sentence(p_text, 950)
        item = f"[{i}] {p_title} ({clean_u})\n{p_slice}\n"
        if char_count + len(item) > 4200 and i > 1:
            break
        lines.append(item)
        char_count += len(item)

    return collapse_whitespace("\n".join(lines))


def get_news(query: str, limit: int = 8) -> str:
    body, _ = _get("https://news.google.com/rss/search?" + urllib.parse.urlencode(
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


# ---------------------------------------------------------------------------- web pages
def fetch_webpage(url: str, max_chars: int = 3000) -> str:
    url = (url or "").strip()
    if re.match(r"^([a-z][a-z0-9+.-]*://|(file|data|javascript|mailto|ftp):)", url, re.I) and not re.match(r"^https?://", url, re.I):
        raise ValueError("only public http(s) web pages can be fetched")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    clean_url = strip_tracking_params(url)
    body, ctype = _get(url, check_host=True)
    text = body.decode("utf-8", "replace")
    if "html" in ctype or "<html" in text[:500].lower():
        title = _clean((re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I) or [None, ""])[1])
        text = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", text)
        text = "\n".join(l for l in (_clean(l) for l in re.sub(r"<[^>]+>", " ", text).split("\n")) if l)
        text = f"{title}\n\n{text}" if title else text
        text = clean_web_text(text)
    elif not ctype.startswith("text/") and "json" not in ctype:
        return f"{clean_url} is not a readable text page ({ctype})."
    else:
        text = clean_web_text(text)
    limit = max(500, min(int(max_chars or 3000), 6000))
    return f"Page: {clean_url}\n" + (text[:limit] + f"\n…[page continues, {len(text) - limit} more characters]" if len(text) > limit else text)
