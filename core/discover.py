"""
Something interesting, and a trivia question, once a day for Today (2026-10-29)
==============================================================================
Alex's recipe asked for "Something random and interesting" and got nothing: recipe topics become news searches, and
"random and interesting" isn't news. And: "maybe even have a little trivia question for me. I love some trivia."

So, from free sources with no key and no AI call (an AI writing trivia could state a confident wrong answer, like the
Oct 29 "Claude Opus 4.8" notice):
  - Wikipedia's featured article of the day, and one "on this day" event (api.wikimedia.org's feed);
  - NASA's Astronomy Picture of the Day (its own pages on science.nasa.gov since 2026-11-03; no key);
  - one multiple-choice trivia question from Open Trivia DB (opentdb.com: written and checked by people).
Every card's source_ref names the source and the date, so each comes once a day at most.
"""
import datetime
import json
import logging
import random
import re
import urllib.parse
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("AlwaysOnAgent.Discover")

WIKI_FEED = "https://api.wikimedia.org/feed/v1/wikipedia/en/featured/{:%Y/%m/%d}"
APOD_ARCHIVE = "https://science.nasa.gov/apod/archive/"      # newest entries first (APOD moved here, 2026)
TRIVIA = "https://opentdb.com/api.php?amount=1&type=multiple&encode=url3986"
# Recipe words that turn on the "something interesting" cards (trivia is on unless the recipe hides it)
INTERESTING = re.compile(r"\b(random|interesting|surprise|curio|fascinat|fun facts?|discover)", re.IGNORECASE)

Fetch = Callable[[str], tuple[bytes, str]]
FEATURED_ARTICLE = False          # Wikipedia's article of the day (off: the owner didn't find it interesting)
ENABLED = True                    # tests turn this off (tests/conftest.py): no real fetches in tests


def wants_interesting(recipe_text: str, more: list[str]) -> bool:
    return bool(INTERESTING.search(" ".join([recipe_text or ""] + list(more or []))))


def _json(fetch: Fetch, url: str) -> Any:
    body, _ = fetch(url)
    return json.loads(body.decode("utf-8", "replace"))


def wikipedia(fetch: Fetch, day: datetime.date) -> list[dict[str, Any]]:
    """One event from "on this day". (The featured article was dropped on Oct 29, Alex: "the interesting thing from
    wikipedia isn't really interesting to me. I like the on this day thing.")"""
    feed = _json(fetch, WIKI_FEED.format(day))
    cards = []
    tfa = feed.get("tfa") or {}
    if FEATURED_ARTICLE and tfa.get("extract"):
        title = (tfa.get("titles") or {}).get("normalized") or tfa.get("normalizedtitle") or tfa.get("title") or ""
        cards.append({
            "card": "story", "title": title, "body": tfa["extract"][:500],
            "link": ((tfa.get("content_urls") or {}).get("desktop") or {}).get("page") or "",
            "site": "Wikipedia · featured article", "why": "Something interesting: Wikipedia's article of the day",
            "image_url": (tfa.get("originalimage") or tfa.get("thumbnail") or {}).get("source"),
            "source_ref": f"discover:wikipedia:{day.isoformat()}"})
    events = [e for e in feed.get("onthisday") or [] if e.get("text") and e.get("year")]
    if events:
        e = random.Random(day.toordinal()).choice(events[:12])
        page = (e.get("pages") or [{}])[0]
        cards.append({
            "card": "story", "title": f"On this day in {e['year']}", "body": e["text"][:400],
            "link": ((page.get("content_urls") or {}).get("desktop") or {}).get("page") or "",
            "site": "Wikipedia · on this day", "why": "Something interesting: on this day in history",
            "source_ref": f"discover:onthisday:{day.isoformat()}"})
    return cards


MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"), 1)}


def _html(fetch: Fetch, url: str) -> str:
    body, _ = fetch(url)
    return body.decode("utf-8", "replace")


def _meta(html: str, prop: str) -> str:
    p = re.escape(prop)
    m = (re.search(r'(?is)<meta[^>]+(?:property|name)="' + p + r'"[^>]+content="([^"]*)"', html)
         or re.search(r'(?is)<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="' + p + '"', html))
    return html_unescape(m.group(1)).strip() if m else ""


def html_unescape(text: str) -> str:
    import html as _h
    return _h.unescape(text or "")


def entry_date(slug: str) -> datetime.date | None:
    """The date in an entry's address: NASA writes it both ways ("apod-2026-november-3-...", "apod-29-october-2026-...")."""
    s = slug.lower()
    months = "|".join(MONTHS)
    m = (re.search(r"(\d{4})-(" + months + r")-(\d{1,2})\b", s)
         or re.search(r"(\d{1,2})-(" + months + r")-(\d{4})\b", s))
    if not m:
        return None
    a, month, b = m.groups()
    year, dd = (int(a), int(b)) if len(a) == 4 else (int(b), int(a))
    try:
        return datetime.date(year, MONTHS[month], dd)
    except ValueError:
        return None


def apod(fetch: Fetch, day: datetime.date, key: str = "") -> list[dict[str, Any]]:
    """NASA's Astronomy Picture of the Day, from its own pages. 2026-11-03: APOD moved to science.nasa.gov; the
    api.nasa.gov feed (on the shared DEMO_KEY) answered HTTP 500 on most days and, on the rest, "NASA Science" as the
    title and no usable picture, so every card since Oct 29 was a picture card without its picture. Now: the archive's
    newest entry, only if it's dated today (no picture twice), and from its page the heading, the explanation and the
    picture (og:image, 1280 px). `key` is no longer needed."""
    archive = _html(fetch, APOD_ARCHIVE)
    links = list(dict.fromkeys(re.findall(r'href="(https://science\.nasa\.gov/image-article/apod[^"#?]+)"', archive)))
    entry = next((u for u in links if entry_date(u) == day), None)
    if entry is None:
        return []                                  # not posted yet today: tried again at the next build
    page = _html(fetch, entry)
    image = _meta(page, "og:image")
    if not image:
        return []                                  # a video day: no picture, no card
    def text(fragment: str) -> str:
        return " ".join(re.sub(r"<[^>]+>", " ", html_unescape(fragment)).split())

    h1 = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page)
    title = text(h1.group(1)) if h1 else ""
    if not title:                                  # "APOD: 2026 November 3 - Comet ... - NASA Science"
        title = re.sub(r"^APOD:\s*[^-]+-\s*|\s*-\s*NASA Science$", "", _meta(page, "og:title")).strip()
    expl = re.search(r'(?is)<p[^>]*media-detail-hero__description[^>]*>(.*?)</p>', page)
    body = text(expl.group(1)) if expl else ""
    body = re.sub(r"^Explanation:\s*", "", body)
    return [{"card": "story", "title": title or "Astronomy Picture of the Day", "body": body[:500], "link": entry,
             "site": "NASA · picture of the day", "why": "Something interesting: NASA's astronomy picture of the day",
             "image_url": image, "source_ref": f"discover:apod:{day.isoformat()}"}]


def trivia(fetch: Fetch, day: datetime.date) -> list[dict[str, Any]]:
    """One multiple-choice question. The card's body is JSON: choices (shuffled), answer, category, difficulty."""
    j = _json(fetch, TRIVIA)
    if j.get("response_code") != 0 or not j.get("results"):
        return []
    q = j["results"][0]
    un = urllib.parse.unquote
    answer = un(q["correct_answer"])
    choices = [answer] + [un(x) for x in q.get("incorrect_answers") or []]
    random.Random(day.toordinal()).shuffle(choices)
    return [{"card": "trivia", "title": un(q["question"]),
             "body": json.dumps({"choices": choices, "answer": answer, "category": un(q.get("category") or ""),
                                 "difficulty": un(q.get("difficulty") or "")}),
             "site": "Trivia", "why": "Today's trivia (Open Trivia DB)",
             "source_ref": f"discover:trivia:{day.isoformat()}"}]


def daily_cards(fetch: Fetch, now: datetime.datetime, shown_refs: set, interesting: bool, want_trivia: bool,
                nasa_key: str = "") -> list[dict[str, Any]]:
    """Today's cards not shown yet. A source that fails is skipped (logged) and tried again at the next build."""
    if not ENABLED:
        return []
    day = now.date()
    sources: list[tuple[str, Callable[[], list[dict[str, Any]]]]] = []
    if want_trivia:
        sources.append(("trivia", lambda: trivia(fetch, day)))
    if interesting:
        sources.append(("wikipedia", lambda: wikipedia(fetch, day)))
        sources.append(("apod", lambda: apod(fetch, day, nasa_key)))
    cards: list[dict[str, Any]] = []
    for name, get in sources:
        if any(r.startswith(f"discover:{name}:{day.isoformat()}") for r in shown_refs) or \
                (name == "wikipedia" and f"discover:onthisday:{day.isoformat()}" in shown_refs):
            continue
        try:
            cards += [c for c in get() if c["source_ref"] not in shown_refs]
        except Exception as e:
            logger.info(f"Discover: {name} unavailable ({type(e).__name__}: {e})")
    return cards
