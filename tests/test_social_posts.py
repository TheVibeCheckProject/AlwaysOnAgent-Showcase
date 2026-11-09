"""
Pins: the agent's side of SocialPostEngine
==========================================
SocialPostEngine (a separate tool) posts Example Site's pins to Pinterest; the agent queues the task
(from chat, a schedule or the app), runs the tool and relays its progress to the task card. These tests replace
the tool with a stand-in, so no test ever posts anything real.
"""

import datetime
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

from core import actions, schedules, social_posts
from core.assistant_tools import AssistantTools
from core.database import DatabaseManager
from core.schedules import CronError, ScheduleRunner, ScheduleStore


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "state.db")


def _dispatcher(db, tmp_path):
    from core.daemon import EngineDispatcher
    return EngineDispatcher(tmp_path / "ws", db=db, task_root=tmp_path / "ws" / "workspaces")


def test_the_real_tool_is_never_reached_from_tests():
    with pytest.raises(social_posts.SocialPostError, match="isn't installed"):
        social_posts.post_pins(1)


def test_a_pins_task_runs_the_tool_and_reports_what_went_up(db, tmp_path, monkeypatch):
    calls = []

    def fake_post(count, on_progress=None, on_start=None):
        calls.append(count)
        on_start(4242)
        on_progress({"stage": "post", "label": "Pin 1 of 2", "detail": "grief-1 to Grief & Sympathy Support",
                     "percent": 0})
        return {"env": "production", "left": 24, "skipped": [{"id": "anxiety-5", "reason": "already on the board"}],
                "posted": [{"id": "grief-1", "board": "Grief & Sympathy Support",
                            "title": "When There Are No Words for Their Loss", "pin": "https://www.pinterest.com/pin/1/"}]}
    monkeypatch.setattr(social_posts, "post_pins", fake_post)
    tid = actions.queue_pins(db, 2, source="test")
    task = db.get_task(tid)
    assert task["engine"] == "social_posts" and task["metadata"]["count"] == 2
    code, out, engine, _ = _dispatcher(db, tmp_path).execute(task)
    assert code == 0 and engine == "social_posts" and calls == [2]
    assert out == ("Posted 1 pin: When There Are No Words for Their Loss (Grief & Sympathy Support); "
                   "skipped 1 already on the board. 24 pins left.")


def test_a_failed_post_is_reported_in_the_tools_words(db, tmp_path, monkeypatch):
    def broken(count, on_progress=None, on_start=None):
        raise social_posts.SocialPostError("not signed in to Pinterest (production): run auth")
    monkeypatch.setattr(social_posts, "post_pins", broken)
    code, out, engine, _ = _dispatcher(db, tmp_path).execute(db.get_task(actions.queue_pins(db, 1)))
    assert code == 1 and "not signed in to Pinterest" in out and engine == "social_posts"


def test_sandbox_posts_say_they_are_practice():
    line = social_posts.summary({"env": "sandbox", "left": 49, "posted": [{"id": "a", "title": "T", "board": "B"}]})
    assert "sandbox (practice, only you see it)" in line


def test_pin_schedules(db):
    store = ScheduleStore(db)
    s = store.create("", "pins", "0 10,16 * * *", count=2)
    assert s["name"] == "Pins" and s["count"] == 2 and s["instruction"] == "Post 2 pins to Pinterest"
    with pytest.raises(CronError, match="from 1 to 5"):
        store.create("Too many", "pins", "0 12 * * *", count=9)
    with pytest.raises(CronError, match="at least 60 min"):
        store.create("Spam", "pins", "*/30 * * * *", count=1)
    s = store.update(s["id"], count=1)
    assert s["count"] == 1 and s["instruction"] == "Post 1 pin to Pinterest"

    runner = ScheduleRunner(db)
    ten = datetime.datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    conn = db.get_connection()
    with conn:
        conn.execute("UPDATE schedules SET next_run_at = ? WHERE id = ?", (schedules.local_iso(ten), s["id"]))
    done = runner.check_once(now=ten + datetime.timedelta(seconds=30))
    task = db.get_task(done[0]["task_id"])
    assert task["engine"] == "social_posts" and task["metadata"]["count"] == 1
    assert "schedule #" in task["metadata"]["source"]


def test_chat_offers_pins_and_the_tap_posts_them(db, tmp_path):
    from interfaces.hud import app as hud_app
    from interfaces.hud import common
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    asked = AssistantTools(db, None, tmp_path, user_text="post 2 pins to pinterest")
    out = asked.call("post_pins", {"count": 2})
    assert "tap Run" in out and asked.started == [] and db.list_tasks() == []     # nothing posts from chat
    card = common.store_proposals(asked.proposals)[0]
    assert card["kind"] == "pins" and card["instruction"] == "Post 2 pins to Pinterest"
    original = hud_state.db
    hud_state.set_db(db)
    try:
        r = client_from(hud_app, addr=LOCAL_ADDR).post(f"/api/proposals/{card['id']}/run")
    finally:
        hud_state.set_db(original)
    assert db.get_task(r.json()["task_id"])["metadata"]["count"] == 2

    # schedules (posts that happen later by themselves) still need the user's own words
    plain = AssistantTools(db, None, tmp_path, user_text="do some marketing for me")
    assert "needs the user to ask for Pinterest" in plain.call(
        "create_schedule", {"kind": "pins", "cron": "0 10 * * *", "name": "Pins", "count": 2})

    sched = AssistantTools(db, None, tmp_path, user_text="post 2 pins every day at 10 and 4")
    out = sched.call("create_schedule", {"kind": "pins", "cron": "0 10,16 * * *", "name": "Daily pins", "count": 2})
    assert out.startswith("Created #") and "2 Pinterest pin(s)" in out


# ---------------------------------------------------------------- the pin maker (2026-10-10)
DRAFTS = {"made": 2, "drafts": [
    {"n": 1, "id": "100-encouraging-a1b2c3", "board": "Encouraging Words",
     "title": "100 Encouraging Messages for a Friend: When They're Doubting Themselves", "link": "https://x"},
    {"n": 2, "id": "75-thinking-d4e5f6", "board": "Daily Affirmations",
     "title": "75 Thinking of You Messages: Something Reminded Me of You", "link": "https://y"}],
    "messages_left": 220, "dead_links": []}


def _fake_tool(monkeypatch, tmp_path, left=5, waiting=None):
    sheet = tmp_path / "preview.jpg"
    sheet.write_bytes(b"jpg")
    calls = []

    def run(args, **kw):
        calls.append(args)
        if args[0] == "status":
            return {"pins_left": left}
        if args[0] == "drafts":
            return {"drafts": waiting or [], "preview": None}
        if args[0] == "make":
            return {**DRAFTS, "preview": str(sheet)}
        if args[0] == "approve":
            return {"approved": ["100-encouraging-a1b2c3"], "drafts_left": 1, "pins_left": 26}
        if args[0] == "reject":
            return {"rejected": ["75-thinking-d4e5f6"], "drafts_left": 0}
        raise AssertionError(args)
    monkeypatch.setattr(social_posts, "run", run)
    return calls


def test_chat_makes_drafts_shows_the_preview_and_approves_by_number(db, tmp_path, monkeypatch):
    calls = _fake_tool(monkeypatch, tmp_path)
    images = tmp_path / "images"
    monkeypatch.setattr("core.config.IMAGES_DIR", images)
    t = AssistantTools(db, None, tmp_path, user_text="make some new pins")
    assert {"make_pins", "approve_pins", "reject_pins"} <= {s["function"]["name"] for s in t.tool_specs()}
    out = t.call("make_pins", {"count": 2})
    shown = list(images.glob("pin-drafts-*.jpg"))
    assert len(shown) == 1 and f"![Pin drafts](/images/{shown[0].name})" in out
    assert "1. 100 Encouraging Messages for a Friend: When They're Doubting Themselves (Encouraging Words)" in out
    assert "Nothing posts until you approve" in out and calls[-1] == ["make", "--count", "2"]
    assert "Approved 1 pin(s)" in AssistantTools(db, None, tmp_path, user_text="approve 1").call(
        "approve_pins", {"pins": "1"})
    assert calls[-1] == ["approve", "--pins", "1"]
    assert "Dropped 1 draft(s)" in t.call("reject_pins", {"pins": "2"})


def test_the_queue_is_topped_up_once_a_day_when_it_runs_low(db, tmp_path, monkeypatch):
    class Pro(list):
        def deliver(self, kind, text, topic=""):
            self.append((kind, text, topic))
    pro = Pro()
    tz = datetime.datetime.now().astimezone().tzinfo
    noon, night = datetime.datetime(2026, 10, 20, 12, tzinfo=tz), datetime.datetime(2026, 10, 20, 22, tzinfo=tz)
    assert social_posts.topup_due(db, noon) and not social_posts.topup_due(db, night)
    _fake_tool(monkeypatch, tmp_path, left=12)
    assert social_posts.run_topup(db, pro, images_dir=tmp_path / "images") is None and pro == []   # plenty left
    db.set_state(social_posts.STATE_TOPUP_DAY, "")
    _fake_tool(monkeypatch, tmp_path, left=5, waiting=[{"n": 1}])
    assert social_posts.run_topup(db, pro, images_dir=tmp_path / "images") is None and pro == []   # drafts wait
    _fake_tool(monkeypatch, tmp_path, left=5)
    assert social_posts.run_topup(db, pro, images_dir=tmp_path / "images")["made"] == 2
    kind, text, topic = pro[0]
    assert kind == "suggestion" and topic == "pin-drafts" and text.startswith("🧷 Only 5 pins are left to post")
    assert "![Pin drafts](/images/pin-drafts-" in text


def test_the_chat_knows_when_pin_drafts_are_waiting(tmp_path, monkeypatch):
    """The owner's "approve all" got a Notepad task card: the chat didn't know drafts were waiting."""
    monkeypatch.setattr(social_posts, "SOCIALPOSTENGINE_DIR", tmp_path)
    assert social_posts.chat_note() == ""
    d = tmp_path / "data" / "pin_drafts"
    d.mkdir(parents=True)
    (d / "drafts.json").write_text('[{"id": "a"}, {"id": "b"}]', encoding="utf-8")
    note = social_posts.chat_note()
    assert "pin drafts waiting for the owner's approval: 2" in note and "approve_pins" in note
