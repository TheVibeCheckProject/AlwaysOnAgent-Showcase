"""Schedules: the cron reader, the store, the runner (catch-up, standby, reminders), the API and the chat tools."""

import datetime
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos or ImageGen: point the agent at folders where they aren't installed
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

from core import push, schedules
from core.assistant_tools import AssistantTools
from core.database import DatabaseManager
from core.schedules import CronError, ScheduleRunner, ScheduleStore

TUE = datetime.datetime(2026, 9, 29, 3, 20)          # a Tuesday


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "state.db")


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "send", lambda kind, title, body, task_id="", channel="tasks": sent.append(
        (kind, title, body, channel)) or 1)
    return sent


def _set_next(db, schedule_id, when: datetime.datetime):
    """Moves a schedule's next run, as if it had been planned for `when`."""
    conn = db.get_connection()
    with conn:
        conn.execute("UPDATE schedules SET next_run_at = ? WHERE id = ?", (schedules.local_iso(when), schedule_id))


# ---------------------------------------------------------------------------- cron
def test_cron_times_and_descriptions():
    cases = {
        "0 9 * * *": ("every day at 09:00", ["Tue 09:00", "Wed 09:00", "Thu 09:00"]),
        "30 8 * * mon-fri": ("weekdays at 08:30", ["Tue 08:30", "Wed 08:30", "Thu 08:30"]),
        "0 18 * * 6,0": ("weekends at 18:00", ["Sat 18:00", "Sun 18:00", "Sat 18:00"]),
        "0 9,21 * * *": ("every day at 09:00 and 21:00", ["Tue 09:00", "Tue 21:00", "Wed 09:00"]),
        "15 14 * * 1,3,5": ("every Mon, Wed, Fri at 14:15", ["Wed 14:15", "Fri 14:15", "Mon 14:15"]),
        "0 9 * * 7": ("every Sun at 09:00", ["Sun 09:00", "Sun 09:00", "Sun 09:00"]),
        "*/15 * * * *": ("every 15 minutes", ["Tue 03:30", "Tue 03:45", "Tue 04:00"]),
        "0 10 1 * *": ("on day 1 of every month at 10:00", ["Thu 10:00", "Sun 10:00", "Tue 10:00"]),
        "@daily": ("every day at 00:00", ["Wed 00:00", "Thu 00:00", "Fri 00:00"]),
    }
    for expr, (text, runs) in cases.items():
        assert schedules.describe(expr) == text, expr
        assert [f"{t:%a %H:%M}" for t in schedules.parse(expr).next_runs(TUE, 3)] == runs, expr
    # Day of month and day of week both set: either matches (standard cron)
    assert [f"{t:%a %d}" for t in schedules.parse("0 12 13 * 5").next_runs(TUE, 3)] == ["Fri 02", "Fri 09", "Tue 13"]
    # A run exactly now isn't "after" now
    assert schedules.parse("20 3 * * *").next_after(TUE) == datetime.datetime(2026, 9, 30, 3, 20)


@pytest.mark.parametrize("expr, message", [
    ("0 9 * *", "5 fields"), ("61 9 * * *", "out of range"), ("0 9 * * funday", "isn't a valid day of week"),
    ("0 9 30 2 *", "never runs"), ("*/0 * * * *", "bad step"), ("0 5-2 * * *", "out of range"),
])
def test_bad_cron_is_explained(expr, message):
    with pytest.raises(CronError, match=message):
        schedules.parse(expr).next_after(TUE)


def test_minimum_gap_per_kind():
    with pytest.raises(CronError, match="at least 60 min"):
        schedules.validate("video", "*/30 * * * *", TUE)
    with pytest.raises(CronError, match="at least 5 min"):
        schedules.validate("reminder", "* * * * *", TUE)
    schedules.validate("task", "*/15 * * * *", TUE)
    schedules.validate("video", "0 */2 * * *", TUE)
    with pytest.raises(CronError, match="kind must be"):
        schedules.validate("email", "0 9 * * *", TUE)


# ---------------------------------------------------------------------------- store
def test_store_create_update_delete(db):
    store = ScheduleStore(db)
    v = store.create("Morning TikTok", "video", "0 9 * * *", source="test", post_to_tiktok=True)
    assert v["kind"] == "video" and v["post_to_tiktok"] and v["enabled"] and v["description"] == "every day at 09:00"
    assert datetime.datetime.fromisoformat(v["next_run_at"]) > datetime.datetime.now().astimezone()
    r = store.create("", "reminder", "0 20 * * 0", text="  water   the plants ")
    assert r["name"] == "Reminder" and r["text"] == "water the plants"
    with pytest.raises(CronError, match="needs its text"):
        store.create("x", "reminder", "0 9 * * *")
    with pytest.raises(CronError, match="needs its instruction"):
        store.create("x", "task", "0 9 * * *", instruction="short")

    paused = store.update(v["id"], enabled=False)
    assert not paused["enabled"] and paused["post_to_tiktok"]              # other settings kept
    moved = store.update(v["id"], cron="30 7 * * 1-5", enabled=True, post_to_tiktok=False)
    assert moved["description"] == "weekdays at 07:30" and moved["enabled"] and not moved["post_to_tiktok"]
    assert datetime.datetime.fromisoformat(moved["next_run_at"]).strftime("%H:%M") == "07:30"
    with pytest.raises(CronError):
        store.update(v["id"], cron="*/5 * * * *")                          # too often for a video
    assert {x["id"] for x in store.list()} == {r["id"], v["id"]}
    assert store.delete(r["id"]) and not store.delete(r["id"])
    with pytest.raises(KeyError):
        store.update(r["id"], enabled=False)


# ---------------------------------------------------------------------------- runner
def test_due_schedules_fire_once_and_plan_the_next_run(db, pushes):
    events = []
    runner = ScheduleRunner(db, on_event=events.append)
    store = runner.store
    video = store.create("Daily post", "video", "0 9 * * *", post_to_tiktok=True)
    reminder = store.create("Plants", "reminder", "0 9 * * *", text="Water the plants")
    task = store.create("Report", "task", "0 9 * * *", instruction="Write a short weekly status report", project="website")
    later = store.create("Later", "reminder", "0 23 * * *", text="not yet")
    nine = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    for s in (video, reminder, task):
        _set_next(db, s["id"], nine)
    _set_next(db, later["id"], nine + datetime.timedelta(hours=14))

    done = runner.check_once(now=nine + datetime.timedelta(seconds=30))
    assert sorted(d["id"] for d in done) == sorted([video["id"], reminder["id"], task["id"]])
    tasks = {t["id"]: t for t in db.list_tasks(limit=10)}
    v_task = tasks[next(d["task_id"] for d in done if d["id"] == video["id"])]
    assert v_task["engine"] == "media_pipeline" and v_task["metadata"]["action"] == "tiktok"
    assert "schedule #" in v_task["metadata"]["source"]
    t_task = tasks[next(d["task_id"] for d in done if d["id"] == task["id"])]
    assert t_task["engine"] == "agentwork" and t_task["metadata"]["project"] == "website"
    assert pushes == [("reminder", "⏰ Reminder", "Water the plants", "reminders")]
    assert len(events) == 3 and any("Water the plants" in e for e in events)

    after = store.get(video["id"])
    assert after["runs"] == 1 and after["last_result"].startswith("started task-")
    assert datetime.datetime.fromisoformat(after["next_run_at"]).replace(tzinfo=None) == nine + datetime.timedelta(days=1)
    # The same moment again: nothing is due any more
    assert runner.check_once(now=nine + datetime.timedelta(seconds=50)) == []
    assert store.get(later["id"])["runs"] == 0


def test_missed_runs_are_made_up_only_briefly(db, pushes):
    runner = ScheduleRunner(db)
    a = runner.store.create("Soon after", "reminder", "0 9 * * *", text="made up")
    b = runner.store.create("Long ago", "reminder", "0 9 * * *", text="skipped")
    nine = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    _set_next(db, a["id"], nine)
    _set_next(db, b["id"], nine - datetime.timedelta(hours=5))
    runner.check_once(now=nine + datetime.timedelta(minutes=90))           # the agent was off for 1.5 h
    assert pushes[0][2] == "made up"
    # The skipped one is announced once, as an alert (until 2026-11-01 it was only logged)
    assert len(pushes) == 2 and pushes[1][0] == "alert" and "Long ago" in pushes[1][2]
    skipped = runner.store.get(b["id"])
    assert skipped["last_result"].startswith("missed") and skipped["runs"] == 0
    runner.check_once(now=nine + datetime.timedelta(minutes=95))
    assert len(pushes) == 2                                                  # nothing new: no repeat alert


def test_standby_skips_work_but_not_reminders(db, pushes):
    runner = ScheduleRunner(db)
    v = runner.store.create("Video", "video", "0 9 * * *")
    r = runner.store.create("Reminder", "reminder", "0 9 * * *", text="still sent")
    nine = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    for s in (v, r):
        _set_next(db, s["id"], nine)
    db.set_state("daemon_status", "paused")
    runner.check_once(now=nine)
    assert db.list_tasks(limit=5) == [] and [p[2] for p in pushes] == ["still sent"]
    assert runner.store.get(v["id"])["last_result"] == "skipped: the agent was in standby"
    # Run now works in standby (the owner pressed it), and doesn't move the next run
    before = runner.store.get(v["id"])["next_run_at"]
    out = schedules.run_now(db, v["id"])
    assert out["task_id"] and runner.store.get(v["id"])["next_run_at"] == before


def test_paused_schedules_dont_run(db, pushes):
    runner = ScheduleRunner(db)
    r = runner.store.create("Off", "reminder", "0 9 * * *", text="no")
    runner.store.update(r["id"], enabled=False)
    _set_next(db, r["id"], datetime.datetime.now() - datetime.timedelta(minutes=1))
    assert runner.check_once() == [] and pushes == []


# ---------------------------------------------------------------------------- API
@pytest.fixture
def api(db, monkeypatch):
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from

    class Mem:
        def __init__(self):
            self.events = []

        def record_event(self, text):
            self.events.append(text)

    mem = Mem()
    original_db, original_mem = hud_state.db, hud_state._memory
    hud_state.set_db(db)
    monkeypatch.setattr(hud_state, "get_memory", lambda: mem)
    yield client_from(hud_app, addr=LOCAL_ADDR), mem
    hud_state.set_db(original_db)
    hud_state._memory = original_mem


def test_schedules_api(api, pushes):
    client, mem = api
    assert client.get("/api/schedules").json()["schedules"] == []
    p = client.get("/api/schedules/preview", params={"cron": "30 8 * * 1-5", "kind": "video"}).json()
    assert p["valid"] and p["description"] == "weekdays at 08:30" and len(p["next_runs"]) == 3
    bad = client.get("/api/schedules/preview", params={"cron": "*/10 * * * *", "kind": "video"}).json()
    assert not bad["valid"] and "at least 60 min" in bad["error"]

    body = {"name": "Morning TikTok", "kind": "video", "cron": "0 9 * * *", "post_to_tiktok": True}
    res = client.post("/api/schedules", json=body, headers={"Idempotency-Key": "k-1"})
    s = res.json()["schedule"]
    assert res.status_code == 200 and s["post_to_tiktok"] and s["description"] == "every day at 09:00"
    again = client.post("/api/schedules", json=body, headers={"Idempotency-Key": "k-1"}).json()
    assert again["duplicate"] and again["schedule"]["id"] == s["id"]
    assert len(client.get("/api/schedules").json()["schedules"]) == 1
    assert "posting to TikTok" in mem.events[0]

    assert client.post("/api/schedules", json={"kind": "video", "cron": "* * * * *"}).status_code == 400
    assert client.post("/api/schedules", json={"kind": "reminder", "cron": "0 9 * * *"}).status_code == 400

    s2 = client.patch(f"/api/schedules/{s['id']}", json={"enabled": False}).json()["schedule"]
    assert not s2["enabled"] and "paused" in mem.events[-1]
    assert client.patch("/api/schedules/999", json={"enabled": False}).status_code == 404

    run = client.post(f"/api/schedules/{s['id']}/run").json()
    assert run["task_id"] and run["ran"]
    assert client.delete(f"/api/schedules/{s['id']}").status_code == 200
    assert client.delete(f"/api/schedules/{s['id']}").status_code == 404
    assert "deleted" in mem.events[-1]


# ---------------------------------------------------------------------------- chat tools
def test_chat_tools(db, tmp_path):
    tools = AssistantTools(db, None, tmp_path, user_text="post a video to tiktok every morning at 9")
    out = tools.call("create_schedule", {"kind": "video", "cron": "0 9 * * *", "name": "Morning TikTok",
                                         "post_to_tiktok": True})
    assert out.startswith("Created #") and "TikTok post" in out and "every day at 09:00" in out and "Next runs" in out
    assert "#1 Morning TikTok" in tools.call("list_schedules", {})
    assert "Error" in tools.call("create_schedule", {"kind": "video", "cron": "*/5 * * * *", "name": "spam"})

    # Posting needs the user's own words
    plain = AssistantTools(db, None, tmp_path, user_text="make a video every evening at 7")
    assert "needs the user to ask for TikTok" in plain.call(
        "create_schedule", {"kind": "video", "cron": "0 19 * * *", "name": "Evening", "post_to_tiktok": True})
    assert plain.call("create_schedule", {"kind": "video", "cron": "0 19 * * *", "name": "Evening"}).startswith("Created")
    assert "needs the user to ask for TikTok" in plain.call("change_schedule", {"schedule_id": 2, "post_to_tiktok": True})

    # At most two new schedules per reply
    busy = AssistantTools(db, None, tmp_path, user_text="remind me")
    for i in range(2):
        assert busy.call("create_schedule", {"kind": "reminder", "cron": f"{i} 8 * * *", "name": f"r{i}",
                                             "text": "stretch"}).startswith("Created")
    assert "at most 2" in busy.call("create_schedule", {"kind": "reminder", "cron": "5 8 * * *", "name": "r3", "text": "x"})

    assert "paused" in tools.call("change_schedule", {"schedule_id": 1, "enabled": False})
    assert "every Mon at 10:00" in tools.call("change_schedule", {"schedule_id": 1, "cron": "0 10 * * 1"})
    # Deleting needs the user's own words (2026-10-28 audit 2.3): this turn asked for a video, not a delete
    assert "doesn't ask for this" in tools.call("change_schedule", {"schedule_id": 1, "delete": True})
    tools.user_text = "delete the morning video schedule"
    assert tools.call("change_schedule", {"schedule_id": 1, "delete": True}).startswith("Deleted")
    assert "no schedule #1" in tools.call("change_schedule", {"schedule_id": 1, "enabled": True})
