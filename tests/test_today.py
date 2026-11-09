"""Today (core/today.py): an edition built from what the agent already made, with page previews, and Alex's reactions."""
import datetime
import io
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.candidates import CandidatePool
from core.database import DatabaseManager
from core.proactive import Proactive
from core.today import RUNNING_MAX, Today, page_preview

NOW = datetime.datetime(2026, 10, 28, 9, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))


def _jpeg(width):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, width // 2), (30, 120, 60)).save(buf, "JPEG")
    return buf.getvalue()


PAGES = {
    "https://trails.example.com/bobcat?utm_source=x": (
        (b'<html><head><meta property="og:title" content="Bobcat spotted near Fox River">'
        b'<meta property="og:site_name" content="Illinois Outdoors">'
        b'<meta property="og:image" content="/img/bobcat.jpg"></head></html>'), "text/html"),
    "https://trails.example.com/img/bobcat.jpg": (_jpeg(1200), "image/jpeg"),
    "https://news.example.org/small": ((b'<title>Tiny &amp; picture</title><meta property="og:image" '
                                       b'content="https://news.example.org/s.jpg">'), "text/html"),
    "https://news.example.org/s.jpg": (_jpeg(200), "image/jpeg"),
    "https://i.ytimg.com/vi/abc123/hqdefault.jpg": (_jpeg(480), "image/jpeg"),
}


@pytest.fixture
def world(tmp_path):
    db = DatabaseManager(tmp_path / "s.db")
    pool = CandidatePool(db, now=lambda: NOW - datetime.timedelta(hours=2))
    Proactive(db)                                           # makes proactive_messages
    clock = {"now": NOW}
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url not in PAGES:
            raise OSError("unreachable")
        return PAGES[url]

    today = Today(db, tmp_path / "img", now=lambda: clock["now"], fetch=fetch,
                  weather=lambda loc: {"temp_f": 62, "summary": "Clear", "place": loc, "is_day": True} if loc else None,
                  location=lambda: "Springfield, IL")
    return db, pool, today, clock, fetched, tmp_path


def test_an_edition_is_built_from_what_the_agent_already_made(world):
    db, pool, today, clock, _fetched, _tmp = world
    pool.add("interest", "notice", "A bobcat was seen on the Fox River trail cam.", why="outdoors",
             url="https://trails.example.com/bobcat?utm_source=x")
    pool.add("interest", "notice", "styropyro posted a new video: “Big laser”", topic="styropyro: Big laser",
             ref="youtube:abc123", url="https://www.youtube.com/watch?v=abc123")
    pool.add("interest", "notice", "Small picture news.", url="https://news.example.org/small")
    rejected = pool.add("interest", "notice", "A heavy court story.", url="https://news.example.org/court")
    pool.drop(rejected, "ranker: heavy legal story")
    repeat = pool.add("interest", "notice", "Same topic, new link.", url="https://news.example.org/gone")
    pool.drop(repeat, "repeat of 'Illinois trails'")
    pool.add("curiosity", "question", "Which trail have you liked most this fall?", why="you hike")
    with db.get_connection() as conn:
        conn.execute("INSERT INTO proactive_messages (kind, text, topic, created_at) VALUES ('notice', "
                     "'Your permit application was approved.', 'open-loops', ?)", ((NOW - datetime.timedelta(hours=1)).isoformat(),))
    for title in ("Post 1 pin to Pinterest", "Weekly growth review", "Post 1 pin to Pinterest"):
        tid = db.create_task(title=title, prompt="x")
        db.update_task(tid, {"phase": "completed", "completed_at": (NOW - datetime.timedelta(hours=3)).isoformat()})

    ed = today.edition()
    head, items = ed["edition"], ed["items"]
    assert head["date"] == "2026-10-28" and head["count"] == len(items)
    assert ed["header"]["weather"]["temp_f"] == 62 and ed["header"]["location"] == "Springfield, IL"
    cards = [(i["card"], i["title"]) for i in items]
    # Best score first, then newest (none are scored here)
    assert cards[:4] == [("story", ""), ("story", "Tiny & picture"), ("video", "Big laser"),
                         ("story", "Bobcat spotted near Fox River")]   # "": the unreachable page, a text card
    assert [c for c, _ in cards[4:]] == ["settled", "done", "question"]
    gone, tiny, video, bobcat = items[:4]
    assert bobcat["site"] == "Illinois Outdoors" and bobcat["image_url"] == f"/api/today/items/{bobcat['id']}/image"
    assert today.image_path(bobcat["id"]).is_file()
    assert video["site"] == "styropyro" and video["image_url"]
    assert tiny["image_url"] is None                                          # under 400 px: a designed text card
    assert gone["site"] == "news.example.org"
    assert not any("court" in i["link"] for i in items)                       # the ranker's rejects stay out
    assert items[5]["title"] == "3 finished"
    assert sorted(items[5]["body"].split("\n")) == ["Post 1 pin to Pinterest ×2", "Weekly growth review"]

    # The same edition until EDITION_EVERY has passed, and then only when something is new
    clock["now"] = NOW + datetime.timedelta(hours=2)
    assert today.edition()["edition"]["id"] == head["id"]
    clock["now"] = NOW + datetime.timedelta(hours=7)
    assert today.edition()["edition"]["id"] == head["id"]
    pool.now = lambda: NOW + datetime.timedelta(hours=6)
    pool.add("interest", "notice", "The bobcat again, other link.", url="http://trails.example.com/bobcat")
    pool.add("interest", "notice", "A new trail opened.", url="https://trails.example.com/new")
    second = today.edition()
    assert second["edition"]["id"] != head["id"] and second["older"][0]["id"] == head["id"]
    assert [i["link"] for i in today.edition(second["edition"]["id"])["items"]] == \
        ["https://trails.example.com/new"]                                             # a link is shown once
    assert today.edition(head["id"])["items"][3]["id"] == bobcat["id"]                  # older editions stay
    # Today itself is the running list (Oct 29): the new card first, the earlier edition's still there below it
    links = [i["link"] for i in second["items"] if i["link"]]
    assert links[0] == "https://trails.example.com/new" and bobcat["link"] in links
    assert len(links) == len(set(links)) and len(second["items"]) <= RUNNING_MAX
    assert [i["card"] for i in second["items"]].count("done") <= 1                      # one "finished" card


def test_trivia_and_on_this_day_dont_crowd_the_running_list(world):
    # 2026-11-03: 14 of Today's 20 cards were trivia, "on this day" and NASA, a new one each day. Trivia and on this
    # day now go to the app's decks: off the cap, the newest DECK_MAX of each after the feed
    from core.today import DECK_MAX, in_deck
    db, _pool, today, _clock, _fetched, _tmp = world
    with db.get_connection() as conn:
        ed = conn.execute("INSERT INTO today_editions (built_at, header_json) VALUES (?, '{}')",
                          (NOW.isoformat(),)).lastrowid
        pos = 0
        for n in range(25):
            for card, title, ref in (("story", f"News {n}", f"candidate:{n}"),
                                     ("trivia", f"Question {n}", f"discover:trivia:{n}"),
                                     ("story", f"On this day in {1900 + n}", f"discover:onthisday:{n}")):
                conn.execute("INSERT INTO today_items (edition, position, card, title, body, link, site, why, source_ref, "
                             "created_at, state) VALUES (?, ?, ?, ?, '', '', '', '', ?, ?, 'new')",
                             (ed, pos, card, title, ref, NOW.isoformat()))
                pos += 1
    page = today.edition()
    items = page["items"]
    feed = [i for i in items if not in_deck(i)]
    assert len(feed) == RUNNING_MAX and page["edition"]["count"] == RUNNING_MAX     # the feed keeps its 20
    assert [i["title"] for i in feed][:2] == ["News 0", "News 1"]
    assert sum(i["card"] == "trivia" for i in items) == DECK_MAX
    assert sum(i["source_ref"].startswith("discover:onthisday") for i in items) == DECK_MAX
    assert items[:RUNNING_MAX] == feed                                              # decks come after the feed
    # A recipe that hides trivia still hides it
    from core.today import RECIPE_STATE
    db.set_state(RECIPE_STATE, __import__("json").dumps({"text": "", "rules": {"show": {"trivia": False}}}))
    assert not any(i["card"] == "trivia" for i in today.edition()["items"])


def test_reacting_to_cards(world):
    db, pool, today, _clock, _fetched, _tmp = world
    q = pool.add("curiosity", "question", "Which trail have you liked most this fall?")
    item = today.edition()["items"][0]
    assert today.react(item["id"], "save")["state"] == "saved"
    assert today.react(item["id"], "seen")["state"] == "saved"                # seeing never undoes a save
    assert [s["id"] for s in today.saved()] == [item["id"]]
    assert today.react(item["id"], "answered")["state"] == "answered"
    with db.get_connection() as conn:
        row = conn.execute("SELECT status, outcome FROM proactive_candidates WHERE id = ?", (q,)).fetchone()
    assert (row["status"], row["outcome"]) == ("sent", "answered")
    today.react(item["id"], "dismiss")
    assert today.edition()["items"] == []                                      # a dismissed card is gone
    with pytest.raises(ValueError):
        today.react(item["id"], "explode")
    with pytest.raises(KeyError):
        today.react(9999, "save")


def test_page_preview_reads_open_graph_then_title():
    p = page_preview('<meta content="https://x.org/a.png" property="og:image"><title> A  page </title>',
                     "https://www.x.org/post")
    assert p == {"title": "A page", "site": "x.org", "image": "https://x.org/a.png"}

    # A generic page name isn't a headline: the card shows the finding's line instead
    db = DatabaseManager(Path(tempfile.mkdtemp()) / "g.db")
    t = Today(db, Path(tempfile.mkdtemp()), fetch=lambda url: (b"<title>Newsroom</title>", "text/html"))
    card = {"card": "story", "link": "https://www.anthropic.com/news", "body": "Claude got faster."}
    t._preview_into(card)
    assert card["title"] == "" and card["site"] == "anthropic.com"


def test_the_today_routes(tmp_path, monkeypatch):
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from interfaces.hud import today_api
    from tests._hud_client import LOCAL_ADDR, client_from
    original = hud_state.db
    hud_state.set_db(DatabaseManager(tmp_path / "api.db"))
    monkeypatch.setattr(today_api, "TODAY_IMAGES_DIR", tmp_path / "img")
    monkeypatch.setattr(today_api, "_today", None)
    try:
        client = client_from(hud_app, addr=LOCAL_ADDR)
        empty = client.get("/api/today").json()
        assert empty["edition"] is None and empty["items"] == [] and empty["header"]["brief"] is None
        assert empty["recipe"]["text"] == "" and empty["recipe"]["rules"]["show"]["done"] is True
        CandidatePool(hud_state.db).add("curiosity", "question", "What are you building this week?")
        data = client.get("/api/today").json()
        item = data["items"][0]
        assert item["card"] == "question" and data["edition"]["count"] == 1
        assert client.post(f"/api/today/items/{item['id']}/react", json={"action": "save"}).json()["item"]["state"] == "saved"
        assert client.post(f"/api/today/items/{item['id']}/react", json={"action": "nope"}).status_code == 400
        assert client.post("/api/today/items/999/react", json={"action": "save"}).status_code == 404
        assert client.get(f"/api/today/items/{item['id']}/image").status_code == 404
        assert client.get("/api/today/saved").json()["items"][0]["id"] == item["id"]

        # The recipe: saved through one AI call (here a fake), and hiding questions hides this card at once
        monkeypatch.setattr(today_api, "llm_asker", lambda llm: lambda system, user: {
            "more": ["golf"], "show": {"question": False}, "location": "Springfield, IL"})
        saved = client.put("/api/today/recipe", json={"text": "More golf, no questions. Weather for Springfield"}).json()
        assert saved["rules"]["more"] == ["golf"] and saved["rules"]["show"]["question"] is False
        assert client.get("/api/today").json()["items"] == []
        from core.proactive import Proactive
        assert Proactive(hud_state.db).settings()["location"] == "Springfield, IL"
        monkeypatch.setattr(today_api, "llm_asker", lambda llm: lambda system, user: None)
        assert client.put("/api/today/recipe", json={"text": "anything"}).status_code == 503
        assert client.get("/api/today/recipe").json()["rules"]["more"] == ["golf"]     # a failed edit changes nothing
    finally:
        hud_state.set_db(original)


def test_the_recipe_steers_today_and_the_away_shift(tmp_path, monkeypatch):
    from core import feeds
    from core.today import apply_rules, recipe, recipe_summary, save_recipe
    db = DatabaseManager(tmp_path / "r.db")
    towns = []
    answer = {"more": ["golf", "Golf", "Illinois outdoors"], "less": ["AI news"], "never": ["crypto"],
              "channels": ["https://www.youtube.com/@veritasium", "Smarter Every Day"], "max_cards": 40,
              "show": {"done": False}, "location": "Springfield, IL"}
    # YouTube's own answer for each name (no network in tests): the real channel, whatever the owner called it
    real = {"@veritasium": {"handle": "@veritasium", "id": "UC" + "v" * 22, "title": "Veritasium"},
            "Smarter Every Day": {"handle": "@smartereveryday", "id": "UC" + "s" * 22, "title": "SmarterEveryDay"}}
    looked_up = []
    find = lambda name: looked_up.append(name) or real.get(name)
    r = save_recipe(db, "More golf and Illinois outdoors, less AI news, no crypto. Follow Veritasium and Smarter "
                        "Every Day. Hide finished tasks. Weather for Springfield.", lambda s, u: answer, towns.append,
                    find=find)
    rules = r["rules"]
    assert rules["more"] == ["golf", "Illinois outdoors"] and rules["max_cards"] == 15      # deduped, capped 3-15
    assert rules["channels"] == ["@veritasium", "Smarter Every Day"] and towns == ["Springfield, IL"]   # their words
    assert feeds.channels(db) == ["@veritasium", "@smartereveryday"]                                 # real channels
    assert "never: crypto" in recipe_summary(r) and "hiding: done cards" in recipe_summary(r)
    assert "SmarterEveryDay (@smartereveryday)" in recipe_summary(r)

    # Saved again: what was found isn't looked up again; a name YouTube doesn't know is said, not followed
    looked_up.clear()
    answer["channels"].append("No Such Channel Anywhere")
    r = save_recipe(db, "same, plus No Such Channel Anywhere", lambda s, u: answer, find=find)
    assert looked_up == ["No Such Channel Anywhere"] and r["channels_missing"] == ["No Such Channel Anywhere"]
    assert "couldn't find on YouTube: No Such Channel Anywhere" in recipe_summary(r)
    assert feeds.channels(db) == ["@veritasium", "@smartereveryday"]

    cards = [{"card": "story", "title": "AI news roundup", "body": ""}, {"card": "story", "title": "Bitcoin and crypto"},
             {"card": "story", "title": "Golf at Maple Ridge"}, {"card": "done", "title": "3 finished", "body": ""},
             {"card": "settled", "title": "Settled", "body": "x"}]
    assert [c["title"] for c in apply_rules(cards, rules)] == ["Golf at Maple Ridge", "AI news roundup", "Settled"]
    assert [c["title"] for c in apply_rules(cards, rules, reorder=False)] == ["AI news roundup", "Golf at Maple Ridge",
                                                                              "Settled"]

    # Dropping a channel from the recipe unfollows it; one followed some other way stays
    feeds.follow(db, "@styropyro")
    save_recipe(db, "Follow Veritasium", lambda s, u: {"channels": ["@veritasium"]}, find=find)
    assert feeds.channels(db) == ["@veritasium", "@styropyro"]

    # The away shift searches what the recipe wants more of; never-topics are excluded everywhere
    from core.proactive import Proactive
    save_recipe(db, "more golf, never crypto", lambda s, u: {"more": ["golf"], "never": ["crypto"]})
    assert Proactive(db).is_excluded("Crypto prices jump") and not Proactive(db).is_excluded("golf")
    assert recipe(db)["text"] == "more golf, never crypto"


def test_the_chat_can_change_today(tmp_path):
    """2026-10-28: "Use Springfield, IL for my weather in the Today screen." got a weather report and nothing saved."""
    from core.assistant_tools import AssistantTools
    from core.proactive import Proactive
    db = DatabaseManager(tmp_path / "c.db")
    for said in ("Use Springfield, IL for my weather in the Today screen.", "show more golf on my Today",
                 "what's in my Today recipe"):
        assert "today" in AssistantTools(db, None, tmp_path, user_text=said).pick_groups(), said
    for said in ("what's the weather today", "I'm tired today", "today is going well"):
        assert "today" not in AssistantTools(db, None, tmp_path, user_text=said).pick_groups(), said

    class Memory:
        llm = None                      # no AI here: only the town is set
    tools = AssistantTools(db, Memory(), tmp_path)
    out = tools.call("today_settings", {"location": "Springfield, IL"})
    assert "Weather town: Springfield, IL" in out and Proactive(db).settings()["location"] == "Springfield, IL"
    assert tools.call("today_settings", {"add": "more golf"}).startswith("Error:")      # the AI didn't answer


def test_the_header_carries_the_briefs_own_facts(tmp_path):
    """Today's MAIL and UP NEXT tiles come from the facts the brief was written from, not from its wording (2026-10-28:
    read from the wording, a missing line showed made-up senders)."""
    import json as _json
    db = DatabaseManager(tmp_path / "b.db")
    Proactive(db)
    data = {"up_next": [{"time": "08:00", "what": "Twice Daily TikTok"}, {"time": "12:00", "what": "Pinterest pins"}],
            "mail": [{"from": "Cash App", "subject": "You paid $20 to Anthropic"}]}
    with db.get_connection() as conn:
        conn.execute("INSERT INTO proactive_messages (kind, text, topic, created_at, data_json) VALUES ('brief', "
                     "'Good night', 'today', ?, ?)", ("2026-10-28T03:00:00-05:00", _json.dumps(data)))
    tz = datetime.timezone(datetime.timedelta(hours=-5))
    head = lambda h, d=28: Today(db, tmp_path, now=lambda: datetime.datetime(2026, 10, d, h, 0, tzinfo=tz)).header()["brief"]
    assert [u["what"] for u in head(3)["up_next"]] == ["Twice Daily TikTok", "Pinterest pins"]
    assert [u["what"] for u in head(9)["up_next"]] == ["Pinterest pins"]                 # 08:00 has passed
    assert head(9)["mail"] == data["mail"] and head(9)["text"] == "Good night"
    assert head(13)["up_next"] == []
    assert Today(db, tmp_path, now=lambda: datetime.datetime(2026, 10, 28, 3, tzinfo=tz)).header()["brief"]["mail"]


def test_build_now_reaches_back_and_skips_what_was_shown(world):
    _db, pool, today, _clock, _fetched, _tmp = world
    pool.add("interest", "notice", "A new trail opened.", url="https://trails.example.com/new")
    first = today.edition()["edition"]["id"]
    assert today.build_now(NOW - datetime.timedelta(days=7)) is None                 # everything was shown already
    pool.add("interest", "notice", "Another trail.", url="https://trails.example.com/another")
    ed = today.build_now(NOW - datetime.timedelta(days=7))
    assert ed != first and [i["link"] for i in today.edition(ed)["items"]] == ["https://trails.example.com/another"]


def test_the_weather_carries_what_the_sky_is_drawn_from(monkeypatch):
    """2026-10-28: the app draws a real sun (by sunrise/sunset), clouds (cover, wind) and rain or snow from these."""
    from core import web_tools
    from core.today import current_weather
    asked = []
    monkeypatch.setattr(web_tools, "_geocode", lambda loc: {"latitude": 35.8, "longitude": -90.7, "name": "Springfield"})
    monkeypatch.setattr(web_tools, "_get_json", lambda url: asked.append(url) or {
        "current": {"temperature_2m": 57.4, "weather_code": 61, "is_day": 1, "cloud_cover": 88, "rain": 0.4,
                    "showers": 0.1, "snowfall": 0, "wind_speed_10m": 9.26, "wind_direction_10m": 230},
        "daily": {"sunrise": [1793190087], "sunset": [1793229081]}})
    w = current_weather("Springfield, IL")
    assert w == {"temp_f": 57, "summary": "Light rain", "place": "Springfield", "is_day": True, "code": 61,
                 "cloud_cover": 88, "rain_mm": 0.5, "snow_cm": 0.0, "wind_mph": 9.3, "wind_dir": 230,
                 "sunrise": 1793190087, "sunset": 1793229081}
    assert "timeformat=unixtime" in asked[0] and "cloud_cover" in asked[0]


def test_something_interesting_and_trivia_come_once_a_day(world, monkeypatch):
    """Oct 29: "Something random and interesting" in the recipe found nothing (recipe topics are news searches), and
    Alex loves trivia. core/discover.py adds Wikipedia's article of the day, an "on this day" event, NASA's picture
    and an Open Trivia DB question, once a day each; right/wrong answers make the month's score."""
    import json as _json
    import urllib.parse as up

    from core import discover
    db, _pool, today, clock, _fetched, _tmp = world
    monkeypatch.setattr(discover, "ENABLED", True)                              # off in other tests
    q = {"response_code": 0, "results": [{"question": up.quote("Which planet is largest?"),
                                           "correct_answer": up.quote("Jupiter"),
                                           "incorrect_answers": [up.quote("Mars"), up.quote("Venus"), up.quote("Earth")],
                                           "category": up.quote("Science"), "difficulty": up.quote("easy")}]}
    wiki = {"tfa": {"titles": {"normalized": "Chimacum Creek"}, "extract": "A creek in Washington.",
                    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Chimacum_Creek"}}},
            "onthisday": [{"year": 1956, "text": "Something happened.", "pages": [{"content_urls": {"desktop": {
                "page": "https://en.wikipedia.org/wiki/1956"}}}]}]}
    # NASA's own pages since 2026-11-03 (tests/test_discover.py has the details)
    entry = "https://science.nasa.gov/image-article/apod-2026-october-29-a-nebula/"
    archive = f'<a href="{entry}">Oct 29</a>'
    page = ('<meta property="og:image" content="https://assets.science.nasa.gov/n.jpg" /><h1>A Nebula</h1>'
            '<p class="media-detail-hero__description"><strong>Explanation: </strong>Gas and dust.</p>')
    calls = []

    def fetch(url):
        calls.append(url)
        if "opentdb" in url:
            return _json.dumps(q).encode(), "application/json"
        if "wikimedia" in url:
            return _json.dumps(wiki).encode(), "application/json"
        if url == discover.APOD_ARCHIVE:
            return archive.encode(), "text/html"
        if url == entry:
            return page.encode(), "text/html"
        raise OSError("not in this test")
    now = datetime.datetime(2026, 10, 29, 9, 0).astimezone()
    cards = discover.daily_cards(fetch, now, set(), interesting=True, want_trivia=True)
    assert [c["site"] for c in cards] == ["Trivia", "Wikipedia · on this day", "NASA · picture of the day"]   # no article of the day
    body = _json.loads(cards[0]["body"])
    assert body["answer"] == "Jupiter" and sorted(body["choices"]) == ["Earth", "Jupiter", "Mars", "Venus"]
    refs = {c["source_ref"] for c in cards}
    assert discover.daily_cards(fetch, now, refs, interesting=True, want_trivia=True) == []        # once a day
    assert [c["card"] for c in discover.daily_cards(fetch, now, set(), interesting=False, want_trivia=True)] == ["trivia"]
    assert discover.wants_interesting("Something random and interesting", []) and not discover.wants_interesting("golf", ["golf"])

    # A trivia card answered right and one wrong: the header counts this month's
    with db.get_connection() as conn:
        ids = [conn.execute("INSERT INTO today_items (edition, position, card, title, body, created_at, state) "
                            "VALUES (1, 0, 'trivia', 'Q', '{}', ?, 'new')", (clock["now"].isoformat(),)).lastrowid
               for _ in range(2)]
    today.react(ids[0], "right")
    today.react(ids[1], "wrong")
    assert today.header()["trivia"] == {"right": 1, "answered": 2}
