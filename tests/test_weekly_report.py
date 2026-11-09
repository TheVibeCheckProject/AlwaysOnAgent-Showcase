"""
The weekly numbers report: the agent asks VibeStats, TiktokVideos and SocialPostEngine and puts the answers
together. Every tool is a stand-in here, so no test reads a real account.
"""

import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real tools: point the agent at folders where they aren't installed
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

from core import actions, schedules, social_posts, tiktok_videos, weekly_report
from core.assistant_tools import AssistantTools
from core.database import DatabaseManager
from core.schedules import ScheduleStore

SITE = {"days": 7, "from": "2026-09-27", "to": "2026-10-04", "visits": 154, "hours_covered": 168,
        "by_channel": [{"name": "Direct", "visits": 120}, {"name": "OrganicSearch", "visits": 12}],
        "top_sources": [{"name": "(none)", "visits": 90}, {"name": "google", "visits": 12}, {"name": "localhost", "visits": 5},
                        {"name": "pinterest.com", "visits": 3}],
        "cards_sent": {"total": 8, "change": 2, "since": "2026-09-27", "full_period": True},
        "subscribers": {"total": 13, "change": 1, "since": "2026-09-29", "full_period": False}}
TIKTOK = {"followers": 2, "followers_change": 1, "views_total": 719, "views_change": 144, "since": "2026-09-27",
          "full_period": True, "posts_this_week": 7, "views_on_this_weeks_posts": 555,
          "best_post": {"posted": "Sep 28, 6:40 PM", "views": 128, "likes": 5, "caption": "x"}}
PINS = {"impressions": 4, "pin_clicks": 1, "outbound_clicks": 0, "saves": 2}


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "state.db")


def _dispatcher(db, tmp_path):
    from core.daemon import EngineDispatcher
    return EngineDispatcher(tmp_path / "ws", db=db, task_root=tmp_path / "ws" / "workspaces")


def test_the_real_tools_are_never_reached_from_tests():
    with pytest.raises(weekly_report.VibeStatsError, match="isn't installed"):
        weekly_report.vibestats(["week"])


def test_the_report_reads_plainly():
    text = weekly_report.format_report(SITE, TIKTOK, PINS)
    assert text.splitlines()[0] == "📊 Week of Sep 27 – Oct 4"
    assert "• Visits: 154 (Direct 120, OrganicSearch 12)" in text
    assert "• Top sources: google 12, pinterest.com 3" in text                 # no "(none)" or "localhost"
    assert "• Cards sent: +2 (8 in total)" in text
    assert "• Daily-email subscribers: 13 (+1 since 2026-09-29)" in text        # history younger than a week
    assert "• Views: +144 (719 in total)" in text and "• Followers: 2 (+1)" in text
    assert "• Best post so far: Sep 28, 6:40 PM, 128 views" in text
    assert "• 4 impressions · 0 clicks to the site · 2 saves" in text


def test_a_failed_source_is_named_and_the_rest_still_reported():
    text = weekly_report.format_report(SITE, tiktok_videos.TiktokVideosError("Studio didn't load"),
                                       {**PINS}, 7)
    assert "• Couldn't read TikTok: Studio didn't load" in text and "Visits: 154" in text
    first_week = weekly_report.format_report({**SITE, "hours_covered": 48}, {**TIKTOK, "views_change": None}, PINS)
    assert "(covers 2 of 7 days: visits are collected nightly)" in first_week
    assert "Views: 719 in total (the change shows from next week)" in first_week



def test_the_report_shows_what_resonates_once_posts_are_judged():
    resonance = {"posts_judged": 8, "themes": [
        {"theme": "peace", "posts": 3, "weight": 1.4, "avg_views": 120.0},
        {"theme": "grounding", "posts": 5, "weight": 0.7, "avg_views": 60.0}],
        "best": [{"quote": "Quiet is allowed.", "views": 210}], "weakest": [{"quote": "Feel the floor.", "views": 22}]}
    text = weekly_report.format_report(SITE, TIKTOK, PINS, 7, resonance=resonance)
    assert "• What resonates: peace posts average 120 views (3 posts; 8 posts judged in all)" in text
    assert '• Most viewed quote: "Quiet is allowed." (210 views)' in text
    assert '• Least viewed: "Feel the floor." (22 views)' in text
    assert "What resonates" not in weekly_report.format_report(SITE, TIKTOK, PINS, 7,
                                                               resonance={"posts_judged": 0})

def test_a_report_task_asks_all_three_tools(db, tmp_path, monkeypatch):
    from core import growth_review
    monkeypatch.setattr(growth_review, "run", lambda *a, **k: None)      # the plain report (no model call)
    calls = []
    monkeypatch.setattr(weekly_report, "vibestats", lambda args, timeout=180: calls.append(("vibestats", args)) or
                        ({"visits": 1} if args[0] == "collect" else {"pushed": True} if args[0] == "track" else SITE))
    monkeypatch.setattr(tiktok_videos, "run", lambda args, **k: calls.append(("tiktok", args)) or TIKTOK)
    monkeypatch.setattr(social_posts, "run", lambda args, **k: calls.append(("pins", args)) or PINS)
    tid = actions.queue_report(db, source="test")
    task = db.get_task(tid)
    assert task["engine"] == "weekly_report" and task["title"] == "Weekly report"
    code, out, engine, _ = _dispatcher(db, tmp_path).execute(task)
    assert code == 0 and engine == "weekly_report" and out.startswith("📊 Week of")
    assert [c[0] for c in calls] == ["vibestats", "vibestats", "tiktok", "tiktok", "pins", "vibestats"]
    assert ("tiktok", ["performance"]) in calls             # what resonates, after the stats (2026-10-10)
    track = calls[-1][1]
    assert track[0] == "track" and track[-1] == "--push"
    assert track[1:-1] == ["--week=Oct 4", "--cards=+2 (8)", "--visits=154", "--tiktok=+144 (719)", "--pins=0",
                           "--subscribers=13 (+1)"]
    assert ("tiktok", ["stats", "--days", "7"]) in calls
    kept = json.loads(db.get_state(weekly_report.STATE_REPORTS))
    assert len(kept) == 1 and kept[0]["pins"] == PINS and kept[0]["failed"] == []


def test_the_growth_review_is_reached_from_the_real_dispatcher(db, tmp_path, monkeypatch):
    """2026-10-06: the review crashed on every run ('EngineDispatcher' has no 'dispatcher') and fell back to the plain
    report, and it never got the proactive messenger or the owner's name. Through the real dispatcher this time."""
    from core import growth_review
    monkeypatch.setattr(weekly_report, "vibestats", lambda args, timeout=180:
                        {"visits": 1} if args[0] == "collect" else {"pushed": True} if args[0] == "track" else SITE)
    monkeypatch.setattr(tiktok_videos, "run", lambda args, **k: TIKTOK)
    monkeypatch.setattr(social_posts, "run", lambda args, **k: PINS)
    seen = {}

    def review(db_, report, llm, proactive=None, name=""):
        seen.update(llm=llm, proactive=proactive, name=name)
        return {"text": "the review"}
    monkeypatch.setattr(growth_review, "run", review)
    d = _dispatcher(db, tmp_path)
    d.proactive, d.memory = "the messenger", type("M", (), {"subject": lambda self: "Alex"})()
    code, out, _, _ = d.execute(db.get_task(actions.queue_report(db)))
    assert code == 0 and out == "the review"
    assert seen == {"llm": d.llm, "proactive": "the messenger", "name": "Alex"}


def test_only_all_sources_failing_fails_the_task(db, tmp_path, monkeypatch):
    from core import growth_review
    monkeypatch.setattr(growth_review, "run", lambda *a, **k: None)
    def fail_v(args, timeout=180):
        raise weekly_report.VibeStatsError("no keys")

    def fail_t(args, **k):
        raise tiktok_videos.TiktokVideosError("signed out")

    def fail_p(args, **k):
        raise social_posts.SocialPostError("not signed in")
    monkeypatch.setattr(weekly_report, "vibestats", fail_v)
    monkeypatch.setattr(tiktok_videos, "run", fail_t)
    monkeypatch.setattr(social_posts, "run", fail_p)
    code, out, _, _ = _dispatcher(db, tmp_path).execute(db.get_task(actions.queue_report(db)))
    assert code == 1 and "Couldn't read TikTok: signed out" in out


def test_the_nightly_collection_runs_once_a_night_from_23_50(db, monkeypatch):
    runs = []
    monkeypatch.setattr(weekly_report, "vibestats", lambda args, timeout=180: runs.append(args) or
                        {"visits": 22, "cards_total": 6, "subscribers": 13, "errors": []})
    evening = datetime.datetime(2026, 10, 4, 22, 0)
    assert weekly_report.collect_nightly(db, evening) is None and runs == []
    late = datetime.datetime(2026, 10, 4, 23, 55)
    assert weekly_report.collect_nightly(db, late)["visits"] == 22
    assert weekly_report.collect_nightly(db, late + datetime.timedelta(minutes=3)) is None     # once per date
    assert weekly_report.collect_nightly(db, datetime.datetime(2026, 10, 5, 23, 51)) is not None
    assert runs == [["collect"], ["collect"]]


def test_report_schedules_and_the_chat_tool(db, tmp_path):
    store = ScheduleStore(db)
    s = store.create("", "report", "0 18 * * 0")
    assert s["name"] == "Weekly report" and s["instruction"].startswith("Weekly numbers report")
    with pytest.raises(schedules.CronError, match="at least 720 min"):
        store.create("Spam", "report", "0 * * * *")
    s = store.update(s["id"], cron="0 9 * * 1")
    assert s["description"] == "every Mon at 09:00" and s["instruction"].startswith("Weekly numbers report")
    outcome = schedules.fire(db, s)
    assert db.get_task(outcome["task_id"])["engine"] == "weekly_report"

    tools = AssistantTools(db, None, tmp_path, user_text="how are we doing this week?")
    out = tools.call("weekly_report", {})
    assert out.startswith("Started task-") and "don't quote numbers" in out

    # A report from today: its own text, not a new run (2026-10-05: "started", then the model made up zeros)
    weekly_report.remember(db, {"text": "📊 Week of Sep 28 – Oct 5\n• Visits: 39", "failed": []})
    tools = AssistantTools(db, None, tmp_path, user_text="what did my numbers look like?")
    out = tools.call("weekly_report", {})
    assert out.startswith("The latest report, made") and "• Visits: 39" in out and not tools.started
    assert tools.call("weekly_report", {"new": True}).startswith("Started task-")


def test_the_tracker_row_marks_missing_sources_and_a_short_week():
    cells = weekly_report.tracker_cells({**SITE, "hours_covered": 48}, {**TIKTOK, "views_change": None},
                                        social_posts.SocialPostError("x"))
    assert cells == {"week": "Oct 4", "cards": "+2 (8)", "visits": "154 (2 of 7 days)", "tiktok": "719 in total",
                     "pins": "", "subscribers": "13 (+1)"}


def test_the_report_includes_proactive_numbers_when_present():
    pro = "proactive: 9 sent, 6 answered, 1 👎; 31 ideas not sent (low score 22, repeats 5, excluded 4)"
    text = weekly_report.format_report(SITE, TIKTOK, PINS, proactive=pro)
    assert "\n\nProactive\n• proactive: 9 sent, 6 answered, 1 👎; 31 ideas not sent" in text

