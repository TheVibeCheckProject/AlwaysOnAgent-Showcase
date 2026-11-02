"""
Something interesting, and a trivia question, once a day for Today (2026-10-29)
==============================================================================
Alex's recipe asked for "Something random and interesting" and got nothing: recipe topics become news searches, and
"random and interesting" isn't news. And: "maybe even have a little trivia question for me. I love some trivia."

So, from free sources with no key and no AI call (an AI writing trivia could state a confident wrong answer, like the
Oct 29 "Claude Opus 4.8" notice):
  - Wikipedia's featured article of the day, and one "on this day" event (api.wikimedia.org's feed);
  - NASA's Astronomy Picture of the Day (api.nasa.gov; DEMO_KEY unless NASA_API_KEY is set);
  - one multiple-choice trivia question from Open Trivia DB (opentdb.com: written and checked by people).
Every card's source_ref names the source and the date, so each comes once a day at most.
"""
import datetime
import json
import logging
import random
import re
import urllib.parse
from typing import Any, Callable, Dict, List, Tuple

logger = logging.getLogger("AlwaysOnAgent.Discover")

WIKI_FEED = "https://api.wikimedia.org/feed/v1/wikipedia/en/featured/{:%Y/%m/%d}"
APOD = "https://api.nasa.gov/planetary/apod?api_key={}"
TRIVIA = "https://opentdb.com/api.php?amount=1&type=multiple&encode=url3986"
# Recipe words that turn on the "something interesting" cards (trivia is on unless the recipe hides it)
INTERESTING = re.compile(r"\b(random|interesting|surprise|curio|fascinat|fun facts?|discover)", re.I)

Fetch = Callable[[str], Tuple[bytes, str]]
FEATURED_ARTICLE = False          # Wikipedia's article of the day (off: the owner didn't find it interesting)
ENABLED = True                    # tests turn this off (tests/conftest.py): no real fetches in tests


def wants_interesting(recipe_text: str, more: List[str]) -> bool:
    return bool(INTERESTING.search(" ".join([recipe_text or ""] + list(more or []))))


def _json(fetch: Fetch, url: str) -> Any:
    body, _ = fetch(url)
    return json.loads(body.decode("utf-8", "replace"))


def wikipedia(fetch: Fetch, day: datetime.date) -> List[Dict[str, Any]]:
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


def apod(fetch: Fetch, day: datetime.date, key: str = "DEMO_KEY") -> List[Dict[str, Any]]:
    """NASA's Astronomy Picture of the Day (a picture only: a video day gives no card)."""
    a = _json(fetch, APOD.format(urllib.parse.quote(key or "DEMO_KEY")))
    if a.get("media_type") != "image" or not a.get("url"):
        return []
    return [{"card": "story", "title": a.get("title") or "Astronomy Picture of the Day",
             "body": (a.get("explanation") or "")[:500], "link": "https://apod.nasa.gov/apod/astropix.html",
             "site": "NASA · picture of the day", "why": "Something interesting: NASA's astronomy picture of the day",
             "image_url": a.get("hdurl") or a["url"], "source_ref": f"discover:apod:{day.isoformat()}"}]


def trivia(fetch: Fetch, day: datetime.date) -> List[Dict[str, Any]]:
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
                nasa_key: str = "") -> List[Dict[str, Any]]:
    """Today's cards not shown yet. A source that fails is skipped (logged) and tried again at the next build."""
    if not ENABLED:
        return []
    day = now.date()
    sources: List[Tuple[str, Callable[[], List[Dict[str, Any]]]]] = []
    if want_trivia:
        sources.append(("trivia", lambda: trivia(fetch, day)))
    if interesting:
        sources.append(("wikipedia", lambda: wikipedia(fetch, day)))
        sources.append(("apod", lambda: apod(fetch, day, nasa_key)))
    cards: List[Dict[str, Any]] = []
    for name, get in sources:
        if any(r.startswith(f"discover:{name}:{day.isoformat()}") for r in shown_refs) or \
                (name == "wikipedia" and f"discover:onthisday:{day.isoformat()}" in shown_refs):
            continue
        try:
            cards += [c for c in get() if c["source_ref"] not in shown_refs]
        except Exception as e:
            logger.info(f"Discover: {name} unavailable ({type(e).__name__}: {e})")
    return cards
