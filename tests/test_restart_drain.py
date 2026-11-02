"""Restarts wait for the running task and hold new work meanwhile (core/maintenance.py, docs/IDEAS.md #23)."""

import datetime
import sys
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import maintenance, push, schedules  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.schedules import ScheduleRunner  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "HOLD", threading.Event())
    monkeypatch.setattr(maintenance, "_waiter", None)
    monkeypatch.setattr(maintenance, "RESPONSE_GRACE", 0)
    monkeypatch.setattr(maintenance, "POLL_SECONDS", 0.05)
    return DatabaseManager(tmp_path / "state.db")


def _exits():
    done = threading.Event()
    return done, done.set


def test_idle_agent_restarts_at_once(db):
    exited, exit_fn = _exits()
    answer = maintenance.restart_when_idle(db, "test", exit_fn=exit_fn)
    assert answer["status"] == "restarting"
    assert exited.wait(2)


def test_waits_for_the_running_task_and_holds_new_work(db):
    tid = db.create_task(title="Render video", prompt="x")
    db.update_task(tid, {"phase": "in_progress"})
    exited, exit_fn = _exits()
    answer = maintenance.restart_when_idle(db, "deploy", exit_fn=exit_fn)
    assert answer["status"] == "draining" and answer["waiting_for"] == ["Render video"]
    assert "Render video" in answer["message"] and maintenance.holding()
    # A second request joins the first instead of starting another wait
    assert maintenance.restart_when_idle(db, "again", exit_fn=exit_fn)["status"] == "draining"
    assert not exited.wait(0.3)                       # still running: no restart yet
    db.update_task(tid, {"phase": "done"})
    assert exited.wait(2)


def test_waits_for_a_chat_reply_being_written(db):
    """2026-10-06: a deploy restarted the agent 30 s after a reply was ready but before it was saved and sent."""
    exited, exit_fn = _exits()
    turn = maintenance.chat_turn().__enter__()
    try:
        maintenance.restart_when_idle(db, "deploy", exit_fn=exit_fn)
        assert not exited.wait(1.3)                   # the reply is still being written
    finally:
        turn.__exit__(None, None, None)
    assert exited.wait(3)


def test_now_skips_the_wait(db):
    tid = db.create_task(title="Render video", prompt="x")
    db.update_task(tid, {"phase": "in_progress"})
    exited, exit_fn = _exits()
    assert maintenance.restart_when_idle(db, "now", now=True, exit_fn=exit_fn)["status"] == "restarting"
    assert exited.wait(2)


def test_gives_up_waiting_after_drain_max(db, monkeypatch):
    monkeypatch.setattr(maintenance, "DRAIN_MAX", 0.2)
    tid = db.create_task(title="Stuck job", prompt="x")
    db.update_task(tid, {"phase": "in_progress"})
    exited, exit_fn = _exits()
    maintenance.restart_when_idle(db, "deploy", exit_fn=exit_fn)
    assert exited.wait(3)
    assert any("Restarting anyway" in l["message"] for l in db.get_recent_logs(limit=10))


def test_held_daemon_starts_no_queued_task(db, tmp_path):
    from core.daemon import AutonomousDaemon
    ws = tmp_path / "ws"
    daemon = AutonomousDaemon(workspace_dir=ws, db_file=str(ws / "state.db"), log_file=str(ws / "worker.log"))
    try:
        tid = daemon.db.create_task(title="Queued job", prompt="p", engine="cloud")
        maintenance.HOLD.set()
        daemon.heartbeat_cycle()
        assert daemon.db.get_task(tid)["phase"] != "in_progress" and daemon.active_future is None
    finally:
        daemon.executor.shutdown(wait=False)
        for h in list(daemon.logger.handlers):
            h.close()
            daemon.logger.removeHandler(h)


def test_held_schedules_fire_after_the_restart_but_reminders_go_out(db, monkeypatch):
    sent = []
    monkeypatch.setattr(push, "send", lambda kind, title, body, task_id="", channel="tasks": sent.append(body) or 1)
    runner = ScheduleRunner(db)
    video = runner.store.create("Video", "video", "0 9 * * *")
    reminder = runner.store.create("Plants", "reminder", "0 9 * * *", text="Water the plants")
    nine = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    conn = db.get_connection()
    with conn:
        for s in (video, reminder):
            conn.execute("UPDATE schedules SET next_run_at = ? WHERE id = ?", (schedules.local_iso(nine), s["id"]))
    maintenance.HOLD.set()
    done = runner.check_once(now=nine + datetime.timedelta(seconds=30))
    assert [d["id"] for d in done] == [reminder["id"]] and sent == ["Water the plants"]
    assert runner.store.get(video["id"])["runs"] == 0 and not db.list_tasks(limit=5)
    maintenance.HOLD.clear()                          # the restarted agent: the video is still due and fires
    done = runner.check_once(now=nine + datetime.timedelta(minutes=3))
    assert [d["id"] for d in done] == [video["id"]] and db.list_tasks(limit=5)
