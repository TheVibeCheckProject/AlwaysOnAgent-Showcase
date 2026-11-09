"""Fixes from the 2026-10-27 background audit that aren't covered by a module's own tests."""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import events
from core.candidates import CandidatePool
from core.database import DatabaseManager
from core.memory import MemoryManager


def test_opening_the_app_wakes_the_proactive_cycle():
    """B5: the brief and held messages arrive as Alex shows up, not up to 30 minutes later."""
    p = events.Presence(clock=lambda: 1000.0)
    events.OWNER_ARRIVED.clear()
    p.stream_opened()
    assert events.OWNER_ARRIVED.is_set() and events.DAEMON_WAKE.is_set()
    events.OWNER_ARRIVED.clear()
    p.stream_opened()                     # a second stream while one is open: not a new arrival
    assert not events.OWNER_ARRIVED.is_set()
    p.stream_closed(), p.stream_closed()
    events.DAEMON_WAKE.clear()


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <title>styropyro</title>
 <entry><yt:videoId>new1</yt:videoId><title>i am not a risk taker</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=new1"/><author><name>styropyro</name></author>
  <published>{recent}</published></entry>
 <entry><yt:videoId>old1</yt:videoId><title>Have you ever seen a cyan laser?</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=old1"/><author><name>styropyro</name></author>
  <published>2026-03-17T21:30:04+00:00</published></entry>
</feed>"""


def test_followed_channels_tell_each_new_video_once_without_searching(tmp_path):
    """B3: the same Oct 15 video was web-searched and dropped 12 times. The feed tells it once, no search, no AI."""
    from core import feeds
    db = DatabaseManager(tmp_path / "s.db")
    now = datetime.datetime(2026, 10, 16, 12, tzinfo=datetime.UTC)
    pages = {"https://www.youtube.com/@styropyro": '<link rel="canonical" href="https://www.youtube.com/channel/'
                                                  'UCJYJgj7rzsn0vdR7fkgjuIA">',
             feeds.FEED.format("UCJYJgj7rzsn0vdR7fkgjuIA"): FEED.format(recent="2026-10-15T18:40:48+00:00")}
    fetched = []
    fetch = lambda url: fetched.append(url) or pages[url]
    assert feeds.channels(db) == [] and feeds.new_uploads(db, now, fetch) == []     # nothing followed by default
    feeds.follow(db, "@styropyro")
    first = feeds.new_uploads(db, now, fetch, force=True)
    assert [v["title"] for v in first] == ["i am not a risk taker"]       # the March one is old: not told
    assert feeds.new_uploads(db, now + datetime.timedelta(hours=1), fetch) == []          # CHECK_EVERY
    assert feeds.new_uploads(db, now + datetime.timedelta(hours=7), fetch) == []          # seen
    assert fetched.count("https://www.youtube.com/@styropyro") == 1                       # the id is kept
    assert feeds.follow(db, "https://www.youtube.com/@veritasium") == ["@styropyro", "@veritasium"]
    assert feeds.unfollow(db, "@Styropyro") == ["@veritasium"]


class _Pool:
    def __init__(self):
        self.added = []

    def add(self, source, kind, text, **kw):
        self.added.append((source, text, kw))
        return len(self.added)


def test_interests_skip_the_owners_case_and_links_already_seen(tmp_path):
    """B3: a private matter (his car loan) was searched as an "interest" several times a day; a finding is new by its link."""
    from core.away_shift import AwayShift
    db = DatabaseManager(tmp_path / "s.db")
    asked = []

    class LLM:
        def chat(self, messages, **kw):
            system = messages[0]["content"]
            asked.append(system[:30])
            if system.startswith("You are a personal assistant reviewing"):
                return json.dumps({"queries": [{"topic": "Car loan", "query": "Alex car loan refinance"},
                                               {"topic": "Hiking season", "query": "state park trail openings 2026"}]})
            return json.dumps({"news": "Trails reopen Nov 14.", "url": "https://parks.example.gov/trails", "subject": "trails"})

    searched = []
    shift = AwayShift(db, llm=LLM(), search_fn=lambda q, limit=4: searched.append(q) or [
        {"title": "Trail reopening", "url": "https://parks.example.gov/trails?utm_source=x", "snippet": "opens Nov 14"}])
    shift.memory = type("M", (), {"_fact_rows": lambda self: [
        {"content": "Alex hikes trails with his friend", "category": "preference"},
        {"content": "Alex is paying off a car loan", "category": "project"}]})()
    pool, cur = _Pool(), datetime.datetime.now().astimezone()
    assert shift._step_interests(cur, pool) == 1
    assert searched == ["state park trail openings 2026"] and pool.added[0][2]["ref"] == "link:parks.example.gov/trails"
    evals = sum(a.startswith("You are the owner's personal") for a in asked)
    assert shift._step_interests(cur, _Pool()) == 0                     # same link: no new candidate...
    assert sum(a.startswith("You are the owner's personal") for a in asked) == evals    # ...and no AI call


def test_a_generator_whose_batch_was_all_dropped_waits(tmp_path):
    """B2: dropped candidates didn't count as pending, so every session (2 h apart) made the same ones again."""
    from core.away_shift import AwayShift
    db = DatabaseManager(tmp_path / "s.db")
    MemoryManager(db, llm=None, embedder=None)
    shift, pool = AwayShift(db), CandidatePool(db)
    t0 = datetime.datetime.now().astimezone()
    assert shift._generator_ready("curiosity", "curiosity", t0)
    shift._mark_generated("curiosity", "curiosity", t0)
    cid = pool.add("curiosity", "question", "The sinkhole cave?", topic="Exploring the sinkhole cave")
    pool.drop(cid, "repeat of 'Exploring the sinkhole cave'")
    assert not shift._generator_ready("curiosity", "curiosity", t0 + datetime.timedelta(hours=2))
    assert shift._generator_ready("curiosity", "curiosity", t0 + datetime.timedelta(hours=7))
    with db.get_connection() as c:          # new material (5 messages from Alex): ready again at once
        for i in range(5):
            c.execute("INSERT INTO mem_messages (conv, role, content, created_at, tokens) VALUES "
                      "('main', 'user', 'x', ?, 1)", (t0.isoformat(),))
    assert shift._generator_ready("curiosity", "curiosity", t0 + datetime.timedelta(hours=2))


def test_one_new_fact_doesnt_reopen_a_generator_but_a_few_do(tmp_path):
    """2026-10-28 audit 0.7: a single new fact (extracted after most chats) re-opened the throttle every active day."""
    from core.away_shift import GEN_NEW_FACTS, AwayShift
    db = DatabaseManager(tmp_path / "s.db")
    MemoryManager(db, llm=None, embedder=None)
    shift, pool = AwayShift(db), CandidatePool(db)
    t0 = datetime.datetime.now().astimezone()
    shift._mark_generated("curiosity", "curiosity", t0)
    pool.drop(pool.add("curiosity", "question", "The cave?", topic="the cave"), "repeat")
    later = t0 + datetime.timedelta(hours=2)

    def add_facts(n):
        with db.get_connection() as c:
            for _ in range(n):
                c.execute("INSERT INTO mem_facts (content, category, importance, created_at, updated_at) "
                          "VALUES ('Alex fact', 'fact', 3, ?, ?)", (t0.isoformat(), t0.isoformat()))
    add_facts(1)
    assert not shift._generator_ready("curiosity", "curiosity", later)
    add_facts(GEN_NEW_FACTS - 1)
    assert shift._generator_ready("curiosity", "curiosity", later)


def test_the_chat_can_show_a_computer_runs_screenshots(tmp_path, monkeypatch):
    """2026-10-27: asked for task-233's screenshots, the chat named folders that didn't exist; it couldn't see them."""
    from core import computer_use
    from core.assistant_tools import AssistantTools
    run = tmp_path / "data" / "runs" / "20261026-212256"
    run.mkdir(parents=True)
    for n in (1, 2, 10, 18):
        (run / f"step-{n:02d}.jpg").write_bytes(b"x")
    monkeypatch.setattr(computer_use, "COMPUTER_USE_DIR", tmp_path)
    db = DatabaseManager(tmp_path / "s.db")
    tid = db.create_task({"title": "Computer: AI Studio", "prompt": "p", "engine": "computer",
                          "metadata": {"result": {"run": "20261026-212256"}}})
    out = AssistantTools(db, None, tmp_path).call("computer_screenshots", {"task_id": tid})
    assert "4 screenshots (step-01 to step-18)" in out
    assert "(/api/computer/runs/20261026-212256/screenshots/step-18.jpg)" in out and "step-01.jpg" not in out
    assert "step-02.jpg" in AssistantTools(db, None, tmp_path).call("computer_screenshots", {"steps": "2"})
    assert computer_use.run_screenshot_path("20261026-212256", "../../s.db") is None
    assert computer_use.run_screenshot_path("20261026-212256", "step-10.jpg") == run / "step-10.jpg"


def test_open_loop_research_backs_off_to_weekly_when_nothing_turns_up():
    """B7.4: one open loop was researched every 1-2 days from Oct 17 with no news."""
    from core.open_loops import CADENCE_MAX, CADENCE_NEAR, OpenLoops
    now = datetime.datetime(2026, 10, 27, 12, tzinfo=datetime.UTC)
    # Past its check date the loop counts as "near" for good: daily research, the cause of that week's 16 calls
    loop = {"check_after": "2026-10-17", "quiet_runs": 0}
    assert OpenLoops.research_cadence(loop, now) == CADENCE_NEAR
    assert OpenLoops.research_cadence({**loop, "quiet_runs": 1}, now) == CADENCE_NEAR * 2
    assert OpenLoops.research_cadence({**loop, "quiet_runs": 5}, now) == CADENCE_MAX


def test_background_research_claims_check_the_real_pool(tmp_path):
    """B7.2: it counted rows in "candidate_pool", which doesn't exist, behind a bare except."""
    db = DatabaseManager(tmp_path / "s.db")
    mem = MemoryManager(db, llm=None, embedder=None)
    db.set_state("away_shift_last_summary", "memory and pending matters checked")    # used to count as research
    assert mem._has_background_record("I looked into it in the background") is False
    CandidatePool(db).add("interest", "notice", "Styropyro posted a video", topic="Styropyro")
    assert mem._has_background_record("I looked into it in the background") is True
    old = (datetime.datetime.now().astimezone() - datetime.timedelta(days=5)).isoformat()
    with db.get_connection() as c:
        c.execute("UPDATE proactive_candidates SET created_at = ?", (old,))
    assert mem._has_background_record("…") is False


def _lockup(vid, title, ago):
    return {"lockupViewModel": {"contentId": vid, "contentType": "LOCKUP_CONTENT_TYPE_VIDEO", "metadata": {
        "lockupMetadataViewModel": {"title": {"content": title}, "metadata": {"contentMetadataViewModel": {
            "metadataRows": [{"metadataParts": [{"text": {"content": "6.4M views"}}, {"text": {"content": ago}}]}]}}}}}}


def test_when_youtubes_feed_fails_the_videos_page_is_read(tmp_path):
    """2026-10-27: YouTube's feeds answered 404 for every channel while the channel pages still loaded."""
    import urllib.error

    from core import feeds
    db = DatabaseManager(tmp_path / "s.db")
    now = datetime.datetime(2026, 10, 28, 12, tzinfo=datetime.UTC)
    cid = "UCJYJgj7rzsn0vdR7fkgjuIA"
    data = {"contents": {"tabs": [{"items": [_lockup("NEW1", "I built a tesla coil", "1d ago"),
                                             _lockup("OLD1", "400 car batteries wired together!!", "8mo ago"),
                                             {"lockupViewModel": {"contentId": "PL1",
                                                                  "contentType": "LOCKUP_CONTENT_TYPE_PLAYLIST"}}]}]}}
    page = f'<script>var ytInitialData = {json.dumps(data)};</script>'

    def fetch(url):
        if url == feeds.FEED.format(cid):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return {feeds.VIDEOS_PAGE.format(cid): page,
                "https://www.youtube.com/@styropyro": f'"externalId":"{cid}"'}[url]

    feeds.follow(db, "@styropyro")
    first = feeds.new_uploads(db, now, fetch)
    assert [(v["id"], v["title"], v["author"]) for v in first] == [("NEW1", "I built a tesla coil", "styropyro")]
    assert first[0]["url"] == "https://www.youtube.com/watch?v=NEW1"
    assert now - first[0]["when"] == datetime.timedelta(days=1)            # "1d ago": approximate
    assert feeds.new_uploads(db, now + datetime.timedelta(hours=7), fetch) == []          # told once
    assert feeds.parse_videos_page("<html>no data</html>", now) == []
