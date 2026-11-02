"""Followed YouTube channels, through their free RSS feeds: new uploads without a web search or an AI call.

2026-10-27 (the 2026-10-27 background audit B3): the away shift's "interests" step web-searched Styropyro every
few hours and "found" the same Oct 15 video 12 times, each dropped as a repeat, using up the day's 15 searches. Every
channel has a feed at youtube.com/feeds/videos.xml?channel_id=UC… listing its latest ~15 uploads; it answered 200 from
the laptop with a browser-style User-Agent. A channel is followed by its handle (@styropyro); the UC… id is read once
from the channel page and kept.

2026-10-28: read through YouTube's Data API first when YOUTUBE_API_KEY is set (free, 1 unit of 10,000 a day per
channel read, no billing), then the feed, then the channel's /videos page: the feed has had outages since Dec 2025.

Stored in system_state: CHANNELS (handles or ids Alex follows), IDS (handle -> channel id), SEEN (channel id -> the
video ids already told), LAST (when the feeds were last read).
"""
import datetime
import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("AlwaysOnAgent.Feeds")

STATE_CHANNELS = "proactive_youtube_channels"
STATE_IDS = "youtube_channel_ids"
STATE_SEEN = "youtube_seen_videos"
STATE_LAST = "youtube_last_check"
# None until Alex follows one. @styropyro was followed by default from one remark on Oct 4 ("likes DIY YouTube
# channels such as Styropyro"), and it kept coming back; Alex, 2026-10-28: "I said I liked his stuff one time".
DEFAULT_CHANNELS: List[str] = []
CHECK_EVERY = datetime.timedelta(hours=6)
SEEN_KEEP = 200                                  # video ids remembered per channel
NEW_UPLOAD_MAX_AGE = datetime.timedelta(days=7)  # older than this is never told as new (checks every 6 h; off for days)
# A channel just followed: its newest uploads of this long. A week missed channels that post every week or two (Oct 29:
# Grant Horvat, followed Oct 28, last posted Oct 19, so nothing showed)
FIRST_READ_MAX_AGE = datetime.timedelta(days=30)
FIRST_READ_SHOW = 2                              # a channel's first read tells its newest uploads of the past week
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
FEED_RETRY = datetime.timedelta(hours=1)         # a source (the API, the feed) that failed is skipped this long
_down_since: Dict[str, float] = {}               # source -> time.monotonic() of its last failure
API = ("https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&maxResults=15"
       "&playlistId=UULF{}")                     # UULF + the id without "UC": the channel's uploads, no Shorts
CHANNEL_ID = re.compile(r'youtube\.com/channel/(UC[\w-]{22})"|"externalId":"(UC[\w-]{22})"')
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def _get_text(url: str) -> str:
    try:
        from core.web_tools import _get
    except ImportError:
        from web_tools import _get
    body, _ = _get(url)
    return body.decode("utf-8", errors="replace")


def _api_key() -> str:
    try:
        from core.config import get_config
    except ImportError:
        from config import get_config
    return get_config("YOUTUBE_API_KEY")


def _api_get(url: str) -> str:
    """The Data API, with the key in a header (never in a URL that an error or a log line could show)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"X-goog-api-key": _api_key()})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_api(body: str) -> List[Dict[str, Any]]:
    """The Data API's playlistItems answer, in parse()'s shape. Scheduled and private items (no publish time) are left
    out."""
    out = []
    for i in json.loads(body).get("items") or []:
        sn, cd = i.get("snippet") or {}, i.get("contentDetails") or {}
        vid, published = cd.get("videoId") or "", cd.get("videoPublishedAt") or ""
        if vid and published:
            out.append({"id": vid, "title": sn.get("title") or "", "url": f"https://www.youtube.com/watch?v={vid}",
                        "published": published.replace("Z", "+00:00"),
                        "author": sn.get("videoOwnerChannelTitle") or sn.get("channelTitle") or ""})
    return out


def _load(db, key: str, default: Any) -> Any:
    try:
        v = json.loads(db.get_state(key, "") or "null")
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


def channels(db) -> List[str]:
    return _load(db, STATE_CHANNELS, list(DEFAULT_CHANNELS))


def _clean(handle: str) -> str:
    h = (handle or "").strip()
    m = re.search(r"youtube\.com/(@[\w.-]+|channel/UC[\w-]{22})", h)
    if m:
        h = m.group(1).replace("channel/", "")
    if not h.startswith(("@", "UC")):
        h = "@" + h.lstrip("@")
    return h


def follow(db, handle: str) -> List[str]:
    h, cur = _clean(handle), channels(db)
    if h.lower() not in [c.lower() for c in cur]:
        cur.append(h)
        db.set_state(STATE_CHANNELS, json.dumps(cur))
    return cur


def unfollow(db, handle: str) -> List[str]:
    """By handle, or by the channel's name ("unfollow Barstool Sports" for @BarstoolSportsTV)."""
    h, name = _clean(handle).lower(), " ".join((handle or "").split()).lower()
    titles = {k.lower(): (v or "").lower() for k, v in _load(db, STATE_TITLES, {}).items()}
    cur = [c for c in channels(db) if c.lower() != h and titles.get(c.lower()) != name]
    db.set_state(STATE_CHANNELS, json.dumps(cur))
    return cur


SEARCH = "https://www.youtube.com/results?search_query={}&sp=EgIQAg%3D%3D"     # sp: channels only
STATE_TITLES = "youtube_channel_titles"          # handle -> the channel's own name, as YouTube shows it


def search_channel(name: str, fetch: Callable[[str], str] = _get_text) -> Optional[Dict[str, str]]:
    """The channel YouTube's own search ranks first for a name: {"handle", "id", "title"}, or None."""
    from urllib.parse import quote_plus
    m = INITIAL_DATA.search(fetch(SEARCH.format(quote_plus(name))))
    if not m:
        return None
    found: List[Dict[str, Any]] = []

    def walk(o):
        if isinstance(o, dict):
            if "channelRenderer" in o:
                found.append(o["channelRenderer"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads(m.group(1)))
    for r in found:
        handle = ((r.get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("canonicalBaseUrl", "")
        cid = r.get("channelId") or ""
        if re.fullmatch(r"UC[\w-]{22}", cid):
            return {"handle": handle.lstrip("/") if handle.startswith("/@") else cid, "id": cid,
                    "title": ((r.get("title") or {}).get("simpleText") or "").strip()}
    return None


def find_channel(name: str, fetch: Callable[[str], str] = _get_text) -> Optional[Dict[str, str]]:
    """The real channel the owner means: a handle or link that exists, else YouTube's search for the name. The recipe's
    AI turned "Barstool sports" and "grant horvat" into handles that don't exist (@Barstoolsports, @granthorvatgolf:
    404) and they were followed as given (2026-10-28); a name is a search, the way the owner would find it."""
    n = " ".join((name or "").split())
    if not n:
        return None
    if re.fullmatch(r"UC[\w-]{22}", n):
        return {"handle": n, "id": n, "title": n}
    if n.startswith("@") or "youtube.com/" in n or " " not in n:
        handle = _clean(n)
        try:
            page = fetch(f"https://www.youtube.com/{handle}")
            m = CHANNEL_ID.search(page)
            if m:
                t = re.search(r'<meta property="og:title" content="([^"]+)"', page)
                return {"handle": handle, "id": m.group(1) or m.group(2), "title": t.group(1) if t else handle}
        except Exception as e:
            logger.info(f"feeds: {handle} isn't a channel page ({type(e).__name__}); searching for it by name")
    try:
        return search_channel(re.sub(r"^@|youtube\.com/", "", n).replace("_", " "), fetch)
    except Exception as e:
        logger.warning(f"feeds: couldn't search YouTube for {n!r}: {type(e).__name__}: {e}")
        return None


def follow_by_name(db, name: str, fetch: Callable[[str], str] = _get_text) -> Optional[Dict[str, str]]:
    """Finds the channel the owner named and follows it; its id and title are kept. None if it couldn't be found."""
    found = find_channel(name, fetch)
    if not found:
        return None
    follow(db, found["handle"])
    ids, titles = _load(db, STATE_IDS, {}), _load(db, STATE_TITLES, {})
    ids[found["handle"]], titles[found["handle"]] = found["id"], found["title"]
    db.set_state(STATE_IDS, json.dumps(ids))
    db.set_state(STATE_TITLES, json.dumps(titles))
    return found


def title_of(db, handle: str) -> str:
    return _load(db, STATE_TITLES, {}).get(handle) or handle


def to_pool(db, pool: Any, now: datetime.datetime, fetch: Callable[[str], str] = _get_text, **read) -> int:
    """New uploads from followed channels, added to the candidate pool (Today shows them as video cards). Used by the
    away shift and by a refresh of Today. **read: new_uploads' force / every / deadline."""
    added = 0
    for v in new_uploads(db, now, fetch, **read):
        when = f" ({v['when']:%b %d})" if v.get("when") else ""
        pool.add("interest", "notice", f"{v['author']} posted a new video: “{v['title']}”{when} {v['url']}".strip(),
                 topic=f"{v['author']}: {v['title']}"[:100], why=f"New on {v['author']}, a channel you follow",
                 ref=f"youtube:{v['id']}", urgency="whenever", url=v["url"])
        added += 1
    return added


def resolve(db, handle: str, fetch: Callable[[str], str] = _get_text) -> Optional[str]:
    """The channel's UC… id: itself, the one kept from before, or read once from the channel page."""
    if handle.startswith("UC"):
        return handle
    ids = _load(db, STATE_IDS, {})
    if handle in ids:
        return ids[handle]
    m = CHANNEL_ID.search(fetch(f"https://www.youtube.com/{handle}"))
    if not m:
        return None
    ids[handle] = m.group(1) or m.group(2)
    db.set_state(STATE_IDS, json.dumps(ids))
    return ids[handle]


def parse(xml: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml)
    author = root.findtext("a:title", default="", namespaces=NS)
    out = []
    for e in root.findall("a:entry", NS):
        link = e.find("a:link", NS)
        out.append({"id": e.findtext("yt:videoId", default="", namespaces=NS),
                    "title": e.findtext("a:title", default="", namespaces=NS),
                    "url": link.get("href") if link is not None else "",
                    "published": e.findtext("a:published", default="", namespaces=NS),
                    "author": e.findtext("a:author/a:name", default=author, namespaces=NS) or author})
    return out


VIDEOS_PAGE = "https://www.youtube.com/channel/{}/videos"
INITIAL_DATA = re.compile(r"var ytInitialData = (\{.*?\});</script>", re.S)
AGO = re.compile(r"(\d+)\s*(s|sec|second|m|min|minute|h|hr|hour|d|day|w|wk|week|mo|month|y|yr|year)s?\b", re.I)
AGO_UNIT = {"s": 1 / 60, "sec": 1 / 60, "second": 1 / 60, "m": 1, "min": 1, "minute": 1, "h": 60, "hr": 60,
            "hour": 60, "d": 1440, "day": 1440, "w": 10080, "wk": 10080, "week": 10080, "mo": 43200,
            "month": 43200, "y": 525600, "yr": 525600, "year": 525600}


def parse_videos_page(html: str, now: datetime.datetime) -> List[Dict[str, Any]]:
    """The uploads listed on a channel's /videos page, newest first, in parse()'s shape. The fallback when the feed
    fails: YouTube's feeds answered 404 for every channel from 2026-10-27 (and before, Dec 2025), while the page
    still loads. The page says only how long ago ("8mo ago"), so "published" is approximate."""
    m = INITIAL_DATA.search(html)
    if not m:
        return []
    found: List[Dict[str, Any]] = []

    def walk(o):
        if isinstance(o, dict):
            if "lockupViewModel" in o:
                found.append(o["lockupViewModel"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(m.group(1)))
    out, ids = [], set()
    for lv in found:
        vid = lv.get("contentId") or ""
        if lv.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO" or not vid or vid in ids:
            continue
        ids.add(vid)
        meta = (lv.get("metadata") or {}).get("lockupMetadataViewModel") or {}
        parts = [p.get("text", {}).get("content", "")
                 for r in (meta.get("metadata") or {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                 for p in r.get("metadataParts", [])]
        ago = next((AGO.search(p) for p in parts if "ago" in p.lower() and AGO.search(p)), None)
        published = ""
        if ago:
            minutes = int(ago.group(1)) * AGO_UNIT[ago.group(2).lower()]
            published = (now - datetime.timedelta(minutes=minutes)).isoformat()
        out.append({"id": vid, "title": (meta.get("title") or {}).get("content", ""),
                    "url": f"https://www.youtube.com/watch?v={vid}", "published": published, "author": ""})
    return out


def _channel_videos(cid: str, now: datetime.datetime, fetch: Callable[[str], str],
                    api_get: Optional[Callable[[str], str]] = None) -> List[Dict[str, Any]]:
    """The channel's latest uploads: the Data API (with a key), else its feed, else its /videos page. A source that
    fails is skipped for FEED_RETRY (one log line, not one per channel). YouTube's feeds have had outages of hours to a
    day or two, 404 or 500 for every channel, since Dec 2025 (no replacement announced)."""
    import time as _time
    sources = [("feed", lambda: parse(fetch(FEED.format(cid))))]
    if _api_key():
        sources.insert(0, ("Data API", lambda: parse_api((api_get or _api_get)(API.format(cid[2:])))))
    for name, read in sources:
        if name in _down_since and _time.monotonic() - _down_since[name] < FEED_RETRY.total_seconds():
            continue
        try:
            videos = read()
        except Exception as e:
            if name not in _down_since:
                logger.info(f"feeds: YouTube's {name} failed ({type(e).__name__}); skipped for the next "
                            f"{FEED_RETRY.total_seconds() / 3600:g} h")
            _down_since[name] = _time.monotonic()
            continue
        if _down_since.pop(name, None) is not None:
            logger.info(f"feeds: YouTube's {name} answers again")
        return videos
    videos = parse_videos_page(fetch(VIDEOS_PAGE.format(cid)), now)
    if not videos:
        raise ValueError(f"no videos on {cid}'s page")
    return videos


def unread_channels(db) -> bool:
    """True if a followed channel hasn't been read yet (just followed): worth reading now, not at the next check."""
    ids, seen = _load(db, STATE_IDS, {}), _load(db, STATE_SEEN, {})
    return any((h if h.startswith("UC") else ids.get(h)) not in seen for h in channels(db))


def new_uploads(db, now: datetime.datetime, fetch: Callable[[str], str] = _get_text,
                force: bool = False, every: datetime.timedelta = CHECK_EVERY,
                deadline: Optional[float] = None) -> List[Dict[str, Any]]:
    """Uploads not told before, from every followed channel, at most every `every`. Never raises: a channel that
    can't be read is skipped (and logged) until the next check. deadline (time.time()): no channel is started after
    it (the rest wait for the next check)."""
    import time as _time
    last = db.get_state(STATE_LAST, "")
    try:
        if not force and last and now - datetime.datetime.fromisoformat(last) < every:
            return []
    except ValueError:
        pass
    db.set_state(STATE_LAST, now.isoformat())
    seen = _load(db, STATE_SEEN, {})
    fresh: List[Dict[str, Any]] = []
    for handle in channels(db):
        if deadline is not None and _time.time() > deadline:
            break
        try:
            cid = resolve(db, handle, fetch)
            if not cid:
                logger.warning(f"feeds: no channel id found for {handle}")
                continue
            author = title_of(db, handle) if title_of(db, handle) != handle else handle.lstrip("@")
            videos = [{**v, "author": v["author"] or author} for v in _channel_videos(cid, now, fetch)]
        except Exception as e:
            logger.warning(f"feeds: couldn't read {handle}: {type(e).__name__}: {e}")
            continue
        first = cid not in seen
        told = set(seen.get(cid, []))
        shown_first = 0
        for v in videos:
            if not v["id"] or v["id"] in told:
                continue
            try:
                when = datetime.datetime.fromisoformat(v["published"])
            except ValueError:
                when = None
            if when is not None and now - when > (FIRST_READ_MAX_AGE if first else NEW_UPLOAD_MAX_AGE):
                continue          # listed only because the page shows more than the feed: not a new upload (0.6)
            if first:
                # A channel just followed shows its latest uploads (FIRST_READ_MAX_AGE), not nothing until it posts
                # again (2026-10-28: Today stayed empty after following three channels); never its whole back catalog
                if when is None or shown_first >= FIRST_READ_SHOW:
                    continue
                shown_first += 1
            fresh.append({**v, "when": when})
        # Everything told so far, newest first, not just what this read listed: the feed lists ~15 uploads but the
        # /videos page ~30, so switching between them made videos 16-30 "new" again (2026-10-28 audit 0.6)
        seen[cid] = list(dict.fromkeys([v["id"] for v in videos if v["id"]] + seen.get(cid, [])))[:SEEN_KEEP]
    db.set_state(STATE_SEEN, json.dumps(seen))
    return fresh
