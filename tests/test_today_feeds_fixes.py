"""Today, YouTube feeds and interest findings: 2026-10-28 audit 0.4, 0.5, 0.6, 0.8, 0.10, 0.11."""
import datetime
import io
import json
import threading
import time

from core import feeds
from core import today as today_mod
from core.assistant_tools import AssistantTools
from core.database import DatabaseManager
from tests.test_background_audit import _Pool
from tests.test_today import NOW, PAGES, world  # noqa: F401  (world is a fixture)


# ---------------------------------------------------------------------------------------------------- 0.4 the build
def test_two_requests_at_once_build_one_edition(world, monkeypatch):
    db, pool, today, _clock, _fetched, _ = world
    pool.add("interest", "notice", "Bobcat seen near the river.", topic="bobcat",
             url="https://trails.example.com/bobcat?utm_source=x")
    slow = today.fetch

    def slow_fetch(url):
        time.sleep(0.3)                                    # both requests are inside the build at the same time
        return slow(url)
    today.fetch = slow_fetch
    built = []
    threads = [threading.Thread(target=lambda: built.append(today.build_if_due())) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([b for b in built if b]) == 1 and built.count(None) == 1    # the second waited, then found it built
    assert db.get_connection().execute("SELECT COUNT(*) FROM today_editions").fetchone()[0] == 1


def test_a_slow_page_doesnt_hold_the_edition_up(world, monkeypatch):
    _db, pool, today, _clock, _fetched, _ = world
    pool.add("interest", "notice", "Bobcat seen near the river.", topic="bobcat",
             url="https://trails.example.com/bobcat?utm_source=x")
    monkeypatch.setattr(today_mod, "BUILD_FETCH_SECONDS", 0.3)
    today.fetch = lambda url: time.sleep(2) or PAGES[url]  # a page that hangs
    started = time.time()
    ed = today.build_if_due()
    assert ed and time.time() - started < 1.5
    item = today.edition()["items"][0]
    assert item["card"] == "story" and item["image_url"] is None and item["site"] == "trails.example.com"


# ---------------------------------------------------------------------------------------------------- 0.11 images
def test_a_huge_preview_image_isnt_decoded(world):
    from PIL import Image
    _db, _pool, today, _clock, _fetched, _tmp_path = world
    today.images_dir.mkdir(parents=True, exist_ok=True)
    assert today._keep_image("https://trails.example.com/img/bobcat.jpg")   # a normal preview is kept...
    buf = io.BytesIO()
    Image.new("RGB", (6000, 5000), (0, 0, 0)).save(buf, "PNG")        # 30M pixels, a small file
    today.fetch = lambda url: (buf.getvalue(), "image/png")
    assert today._keep_image("https://example.com/bomb.png") is None   # ...this one isn't even decoded


# ---------------------------------------------------------------------------------------------------- 0.8 the recipe
def test_adding_to_a_full_recipe_says_so_and_changes_nothing(tmp_path):
    db = DatabaseManager(tmp_path / "s.db")
    long_text = "More Illinois outdoors and fishing reports. " * 34                    # ~1,500 characters
    db.set_state(today_mod.RECIPE_STATE, json.dumps({"text": long_text.strip(), "rules": {}, "at": ""}))
    tools = AssistantTools(db, None, tmp_path, user_text="no crypto on my Today screen")
    out = tools.today_settings(add="no crypto")
    assert "Nothing was changed" in out and "rewrite it shorter" in out
    assert today_mod.recipe(db)["text"] == long_text.strip()


# ---------------------------------------------------------------------------------------------------- 0.6 feeds
def test_switching_between_feed_and_page_doesnt_retell_old_videos(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "s.db")
    now = datetime.datetime(2026, 10, 28, 12, tzinfo=datetime.UTC)
    ago = lambda days: (now - datetime.timedelta(days=days)).isoformat()
    feed = [{"id": f"V{i}", "title": f"video {i}", "url": "", "published": ago(i * 10 + 40), "author": "a"}
            for i in range(2)]                             # the latest few, all past FIRST_READ_MAX_AGE
    page = feed + [{"id": f"OLD{i}", "title": "old", "url": "", "published": ago(200 + i), "author": "a"}
                   for i in range(3)]                      # the page lists more, all of them old
    reads = iter([feed, page, feed, page])
    monkeypatch.setattr(feeds, "_channel_videos", lambda cid, now, fetch: next(reads))
    monkeypatch.setattr(feeds, "resolve", lambda db, handle, fetch=None: "UCx")
    feeds.follow(db, "@someone")
    for hours in (0, 7, 14, 21):
        assert feeds.new_uploads(db, now + datetime.timedelta(hours=hours), force=True) == []


# ---------------------------------------------------------------------------------------------------- 0.5 / 0.10 findings
def _shift(tmp_path, answers):
    from core.away_shift import AwayShift
    db = DatabaseManager(tmp_path / "s.db")

    class LLM:
        def chat(self, messages, **kw):
            if messages[0]["content"].startswith("You are a personal assistant reviewing"):
                return json.dumps({"queries": [{"topic": "Hiking season", "query": "state park trail openings 2026"}]})
            return next(answers)
    shift = AwayShift(db, llm=LLM(), search_fn=lambda q, limit=4: [
        {"title": "Trail reopening", "url": "https://parks.example.gov/trails", "snippet": "opens Nov 14"}])
    shift.memory = type("M", (), {"_fact_rows": lambda self: [
        {"content": "Alex hikes trails with his friend", "category": "preference"}]})()
    return shift


def test_a_findings_link_must_be_one_of_the_results(tmp_path):
    shift = _shift(tmp_path, iter([json.dumps({"news": "Trails reopen Nov 14.", "subject": "trails",
                                               "url": "https://made-up.example/trails-2026"})]))
    pool = _Pool()
    assert shift._step_interests(datetime.datetime.now().astimezone(), pool) == 1
    assert pool.added[0][2]["url"] == "https://parks.example.gov/trails"           # not the link the AI made up


def test_results_arent_marked_seen_when_the_ai_didnt_answer(tmp_path):
    answers = iter([None, json.dumps({"news": "Trails reopen Nov 14.", "subject": "trails",
                                      "url": "https://parks.example.gov/trails"})])
    shift = _shift(tmp_path, answers)
    cur = datetime.datetime.now().astimezone()
    assert shift._step_interests(cur, _Pool()) == 0                     # the AI call failed: nothing learned...
    assert shift._step_interests(cur, _Pool()) == 1                     # ...so the same results are looked at again


# ---------------------------------------------------------------------------------------------------- channels by name
def _youtube(now, uploads_days_ago=(3, 12)):
    """A fake YouTube: @Barstoolsports doesn't exist, the search finds the real channel, its feed 404s and its
    /videos page lists uploads from these many days ago."""
    import urllib.error

    from tests.test_background_audit import _lockup
    cid = "UCifWD4FBa4eaKK7HLF0PlTA"
    search = {"contents": [{"channelRenderer": {"channelId": cid, "title": {"simpleText": "Barstool Sports"},
                                                "navigationEndpoint": {"browseEndpoint": {
                                                    "canonicalBaseUrl": "/@BarstoolSportsTV"}}}}]}
    page = {"contents": {"tabs": [{"items": [_lockup(f"V{d}", f"video {d}", f"{d}d ago") for d in uploads_days_ago]}]}}
    reads = []

    def fetch(url):
        reads.append(url)
        if url == feeds.FEED.format(cid) or url.endswith("/@Barstoolsports"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "/results?" in url:
            return f"<script>var ytInitialData = {json.dumps(search)};</script>"
        if url == feeds.VIDEOS_PAGE.format(cid):
            return f"<script>var ytInitialData = {json.dumps(page)};</script>"
        raise AssertionError(f"unexpected fetch {url}")
    return fetch, reads, cid


def test_a_channel_is_found_by_name_when_the_handle_doesnt_exist(tmp_path):
    """2026-10-28: the recipe's AI made "Barstool sports" into @Barstoolsports, a 404, and it was followed as given."""
    db = DatabaseManager(tmp_path / "s.db")
    fetch, _reads, cid = _youtube(datetime.datetime.now().astimezone())
    found = feeds.follow_by_name(db, "@Barstoolsports", fetch)
    assert found == {"handle": "@BarstoolSportsTV", "id": cid, "title": "Barstool Sports"}
    assert feeds.channels(db) == ["@BarstoolSportsTV"] and feeds.title_of(db, "@BarstoolSportsTV") == "Barstool Sports"
    assert feeds.follow_by_name(db, "Barstool sports", fetch)["handle"] == "@BarstoolSportsTV"   # a plain name too
    assert feeds.channels(db) == ["@BarstoolSportsTV"]
    feeds.unfollow(db, "Barstool Sports")                                  # by its name
    assert feeds.channels(db) == []


def test_a_channel_just_followed_shows_its_latest_uploads_of_the_week(tmp_path):
    """Its first read told only uploads from the last 2 days, so a channel that posted 3 days ago showed nothing."""
    db = DatabaseManager(tmp_path / "s.db")
    now = datetime.datetime.now().astimezone()
    fetch, _, _ = _youtube(now, uploads_days_ago=(1, 3, 5, 12))
    feeds.follow_by_name(db, "Barstool sports", fetch)
    assert feeds.unread_channels(db)
    first = feeds.new_uploads(db, now, fetch)
    assert [v["id"] for v in first] == ["V1", "V3"]                      # the newest FIRST_READ_SHOW, not V12
    assert not feeds.unread_channels(db)
    assert feeds.new_uploads(db, now, fetch, force=True) == []           # V5 isn't news later on


def test_a_refresh_reads_a_channel_just_followed(world):
    db, _pool, today, clock, _fetched, _ = world
    fetch, _reads, _ = _youtube(clock["now"], uploads_days_ago=(1, 3))
    feeds.follow_by_name(db, "Barstool sports", fetch)
    today.feed_fetch = fetch
    clock["now"] = datetime.datetime.now().astimezone()                  # the fake uploads are dated from now
    assert today.build_if_due(force=True)
    cards = today.edition()["items"]
    assert [c["card"] for c in cards].count("video") == 2
    assert cards[0]["site"] == "Barstool Sports"                         # the channel's own name on the card


def test_once_the_feed_fails_channels_are_read_from_their_page(tmp_path):
    """2026-10-28: YouTube's feeds 404ed for every channel; each read tried the feed first and logged a failure."""
    db = DatabaseManager(tmp_path / "s.db")
    now = datetime.datetime.now().astimezone()
    fetch, reads, cid = _youtube(now, uploads_days_ago=(1,))
    feeds.follow_by_name(db, "Barstool sports", fetch)
    feeds.new_uploads(db, now, fetch)
    reads.clear()
    feeds.new_uploads(db, now, fetch, force=True)
    assert reads == [feeds.VIDEOS_PAGE.format(cid)]                     # the feed isn't tried again for a while


def test_the_data_api_is_read_first_when_there_is_a_key(tmp_path, monkeypatch):
    """With YOUTUBE_API_KEY the channel's uploads come from YouTube's Data API; if it fails, the feed and page still do."""
    db = DatabaseManager(tmp_path / "s.db")
    now = datetime.datetime.now().astimezone()
    fetch, reads, cid = _youtube(now, uploads_days_ago=(1,))
    feeds.follow_by_name(db, "Barstool sports", fetch)
    monkeypatch.setattr(feeds, "_api_key", lambda: "k")
    asked = []
    published = (now - datetime.timedelta(hours=5)).astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    answer = {"items": [
        {"snippet": {"title": "Pizza review", "videoOwnerChannelTitle": "Barstool Sports"},
         "contentDetails": {"videoId": "API1", "videoPublishedAt": published}},
        {"snippet": {"title": "Premieres Friday"}, "contentDetails": {"videoId": "SOON"}}]}      # not out yet
    monkeypatch.setattr(feeds, "_api_get", lambda url: asked.append(url) or json.dumps(answer))
    reads.clear()
    got = feeds.new_uploads(db, now, fetch)
    assert [v["id"] for v in got] == ["API1"] and got[0]["author"] == "Barstool Sports"
    assert asked == [feeds.API.format(cid[2:])] and reads == []          # nothing read from youtube.com

    def broken(url):
        raise OSError("quota")
    monkeypatch.setattr(feeds, "_api_get", broken)
    db2 = DatabaseManager(tmp_path / "t.db")
    feeds.follow_by_name(db2, "Barstool sports", fetch)
    assert [v["id"] for v in feeds.new_uploads(db2, now, fetch)] == ["V1"]   # the API failed: the page answered


# ---------------------------------------------------------------------------------------------------- stale news
def test_an_old_article_is_never_told_as_news(tmp_path):
    """2026-10-29: a May 28 article ("Claude Opus 4.8") was found on Oct 29, judged fresh by an AI that wasn't told the
    date, and sent as a notice and a Today card. The date is now checked in code, and the AI is told today's date."""
    from core.away_shift import FINDING_MAX_AGE, AwayShift
    db = DatabaseManager(tmp_path / "s.db")
    cur = datetime.datetime(2026, 10, 29, 3, 16, tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))
    old = "https://www.actuia.com/en/news/claude-opus-48-anthropic-emphasizes-a-more-honest-model-facing-its-own-errors"
    pages = {old: "2026-05-28T20:40:02+00:00", "https://news.example/fresh": "2026-10-27T09:00:00+00:00",
             "https://blog.example/undated": ""}
    results = {"old": [{"title": "Claude Opus 4.8", "url": old, "snippet": "Anthropic released Claude Opus 4.8"}],
               "fresh": [{"title": "Update", "url": "https://news.example/fresh", "snippet": "new"}],
               "dated": [{"title": "Old by provider", "url": "https://x.example/a", "snippet": "s",
                          "published": "May 28, 2026"}],
               "undated": [{"title": "Update", "url": "https://blog.example/undated", "snippet": "new"}]}
    asked, searched = [], []

    class LLM:
        def chat(self, messages, **kw):
            if messages[0]["content"].startswith("You are a personal assistant reviewing"):
                return json.dumps({"queries": [{"topic": "Claude model updates", "query": f"{which} claude news"}]})
            asked.append(messages[-1]["content"])
            return json.dumps({"news": "Something happened.", "subject": "Claude", "url": results[which][0]["url"]})
    shift = AwayShift(db, llm=LLM(), search_fn=lambda q, limit=4: searched.append(q) or results[q.split()[0]],
                      page_date_fn=lambda url: pages.get(url, ""))
    shift.memory = type("M", (), {"_fact_rows": lambda self: [
        {"content": "Alex follows Claude model releases", "category": "preference"}]})()
    for which, told in (("old", 0), ("fresh", 1), ("dated", 0), ("undated", 1)):
        assert shift._step_interests(cur, _Pool()) == told, which
    assert "Today: 2026-10-29" in asked[0]                               # the AI is told the date...
    assert '"published": "2026-05-28"' in asked[2]                        # ...and each result's, when the provider gives it
    assert FINDING_MAX_AGE <= datetime.timedelta(days=14)


def test_the_honesty_guard_is_in_the_prompt_only_when_switched_on(tmp_path, monkeypatch):
    """Step 2a of the research (anti-sycophancy prompt): off in production until scripts/eval_sycophancy.py shows it
    helps; SYCOPHANCY_GUARD=on in .env, or guard=True from the eval, turns it on."""
    from core import memory as mem_mod
    mem = mem_mod.MemoryManager(DatabaseManager(tmp_path / "g.db"))
    text = lambda msgs: "\n".join(m["content"] for m in msgs)
    monkeypatch.setattr(mem_mod, "guard_on", lambda: False)
    off = text(mem.build_context("Are you sure about that?"))
    assert "Disagreeing and changing your mind" not in off and mem_mod.GUARD_REMINDER.strip() not in off
    on = text(mem.build_context("Are you sure about that?", guard=True))
    assert "Disagreeing and changing your mind" in on and mem_mod.GUARD_REMINDER.strip() in on
    monkeypatch.setattr(mem_mod, "guard_on", lambda: True)
    assert "Disagreeing and changing your mind" in text(mem.build_context("hi"))      # the .env switch


def test_the_dispute_note_comes_only_when_the_owner_pushes_back(tmp_path, monkeypatch):
    """Oct 29: disputed about its own claim, the chat caved and invented a cause; a note before the reply, only when the
    owner's message disputes, behind SYCOPHANCY_DISPUTE (off until scripts/eval_sycophancy.py shows it helps)."""
    from core import memory as mem_mod
    mem = mem_mod.MemoryManager(DatabaseManager(tmp_path / "d.db"))
    text = lambda msgs: "\n".join(m["content"] for m in msgs)
    note = mem_mod.DISPUTE_NOTE[:40]
    assert note in text(mem.build_context("Wait what? That's wrong, it's still closed.", dispute=True))
    assert note not in text(mem.build_context("What's the weather today?", dispute=True))         # not a dispute
    assert note not in text(mem.build_context("Wait what? That's wrong.", dispute=False))           # switched off
    monkeypatch.setattr(mem_mod, "dispute_on", lambda: False)
    assert note not in text(mem.build_context("Are you sure?"))                                    # off by default
