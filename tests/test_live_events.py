"""
Live events (IDEAS #26): the in-process bus (core/events.py), GET /api/events (interfaces/hud/events_api.py) and
the daemon waking when a task is queued instead of waiting out its 30 s sleep. No network, no real services.
"""

import asyncio
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import events
from core.database import DatabaseManager
from interfaces.hud import app as hud_app
from interfaces.hud import events_api
from interfaces.hud import state as hud_state
from tests._hud_client import LAN_ADDR, client_from


@pytest.fixture
def tmp_db():
    d = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    yield DatabaseManager(Path(d.name) / "events.db")
    d.cleanup()


@pytest.fixture
def listener():
    sub = events.BUS.subscribe()
    yield sub
    events.BUS.unsubscribe(sub)


def _drain(sub):
    out = []
    while True:
        e = sub.get(timeout=0.05)
        if e is None:
            return out
        out.append(e)


# ---------------- the bus ----------------

def test_bus_delivers_to_every_subscriber():
    bus = events.EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish({"type": "done", "task_id": "task-1"})
    assert a.get(1)["task_id"] == "task-1" and b.get(1)["task_id"] == "task-1"
    bus.unsubscribe(a)
    bus.publish({"type": "failed", "task_id": "task-2"})
    assert a.get(0.05) is None and b.get(1)["task_id"] == "task-2"


def test_bus_drops_a_slow_subscriber_instead_of_growing():
    bus = events.EventBus(max_queue=3)
    slow, fast = bus.subscribe(), bus.subscribe()
    for i in range(3):
        bus.publish({"n": i})
    assert [fast.get(1)["n"] for _ in range(3)] == [0, 1, 2]
    bus.publish({"n": 3})                          # slow never read: its queue is full
    assert slow.dropped and bus.subscriber_count() == 1
    assert slow.queue.qsize() == 3                 # nothing more was kept for it
    assert fast.get(1)["n"] == 3 and not fast.dropped


def test_bus_caps_open_streams():
    bus = events.EventBus(max_subscribers=2)
    assert bus.subscribe() and bus.subscribe()
    assert bus.subscribe() is None


def test_bus_is_thread_safe():
    bus = events.EventBus(max_queue=10_000)
    sub = bus.subscribe()
    threads = [threading.Thread(target=lambda: [bus.publish({"x": 1}) for _ in range(500)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sub.queue.qsize() == 4000 and not sub.dropped


# ---------------- task status writes publish ----------------

def test_task_status_writes_publish_events(tmp_db, listener):
    tid = tmp_db.create_task(title="t", prompt="p", engine="cloud")
    tmp_db.update_task(tid, {"phase": "in_progress"})
    tmp_db.set_task_progress(tid, {"label": "Rendering", "percent": 40, "eta_seconds": 12})
    tmp_db.update_task(tid, {"phase": "completed", "exit_code": 0})
    tmp_db.update_task(tid, {"output_summary": "no phase change: no event"})
    got = [(e["type"], e["task_id"], e["status"]) for e in _drain(listener)]
    assert got == [("created", tid, "queued"), ("started", tid, "started"), ("progress", tid, "progress"),
                   ("done", tid, "done")]


def test_failed_and_cancelled_are_published(tmp_db, listener):
    a = tmp_db.create_task(title="a", prompt="p")
    b = tmp_db.create_task(title="b", prompt="p")
    tmp_db.update_task(a, {"phase": "failed", "exit_code": 1})
    tmp_db.cancel_task(b, "test")
    evs = [e for e in _drain(listener) if e["type"] == "failed"]
    assert [(e["task_id"], e.get("cancelled", False)) for e in evs] == [(a, False), (b, True)]


# ---------------- GET /api/events ----------------

def test_events_endpoint_requires_auth():
    original = hud_state.HUD_AUTH_TOKEN
    hud_state.HUD_AUTH_TOKEN = "events-token-123"
    try:
        lan = client_from(hud_app, addr=LAN_ADDR)
        assert lan.get("/api/events").status_code == 401
        assert lan.get("/api/events", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert events.BUS.subscriber_count() == 0          # a rejected client never subscribes
    finally:
        hud_state.HUD_AUTH_TOKEN = original


def test_events_endpoint_streams_a_task_status_change(tmp_db):
    """A real uvicorn on a loopback port (TestClient waits for a response to end, and this one never does)."""
    import socket

    import httpx
    import uvicorn

    original_token = hud_state.HUD_AUTH_TOKEN
    hud_state.HUD_AUTH_TOKEN = "events-token-123"
    tid = tmp_db.create_task(title="t", prompt="p")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(hud_app, host="127.0.0.1", port=port, log_level="error"))
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        before = events.BUS.subscriber_count()

        def change_status_once_connected():
            end = time.time() + 5
            while events.BUS.subscriber_count() == before and time.time() < end:
                time.sleep(0.02)
            tmp_db.update_task(tid, {"phase": "in_progress"})

        threading.Thread(target=change_status_once_connected, daemon=True).start()
        data = None
        # trust_env=False: an HTTP(S)_PROXY on the machine must not catch a loopback call
        with httpx.Client(trust_env=False, timeout=10) as http, \
                http.stream("GET", f"http://127.0.0.1:{port}/api/events",
                            headers={"Authorization": "Bearer events-token-123"}) as res:
            assert res.status_code == 200
            assert res.headers["content-type"].startswith("text/event-stream")
            for line in res.iter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    break
        assert data["task_id"] == tid and data["type"] == "started" and data["status"] == "started"
    finally:
        hud_state.HUD_AUTH_TOKEN = original_token
        server.should_exit = True
        thread.join(timeout=10)


def test_stream_sends_heartbeats_and_ends_for_a_dropped_client():
    bus_sub = events.Subscription(5)

    async def run():
        out = []
        calls = {"n": 0}

        async def connected():
            return False

        gen = events_api.event_stream(bus_sub, connected, heartbeat=0.05, poll=0.01)
        async for chunk in gen:
            out.append(chunk)
            calls["n"] += 1
            if calls["n"] == 3:
                bus_sub.queue.put_nowait({"type": "done", "task_id": "task-9"})
                bus_sub.dropped = True
        return out

    out = asyncio.run(asyncio.wait_for(run(), 5))
    assert out[0].startswith("retry: ")
    assert ": ping\n\n" in out                       # keeps phones and proxies from closing a quiet line
    assert out[-1].startswith("event: task\ndata: ") and '"task-9"' in out[-1]


# ---------------- the daemon wakes on enqueue ----------------

def test_daemon_wakes_when_a_task_is_queued(tmp_path, monkeypatch):
    from core.daemon import AutonomousDaemon
    monkeypatch.setenv("SUPERVISOR_ACTIVE", "1")
    daemon = AutonomousDaemon(workspace_dir=_ROOT, interval_seconds=30, db_file=str(tmp_path / "wake.db"),
                              log_file=str(tmp_path / "wake.log"))
    beats = []
    monkeypatch.setattr(daemon, "heartbeat_cycle", lambda: beats.append(time.time()))
    events.DAEMON_WAKE.clear()
    loop = threading.Thread(target=daemon.run, daemon=True)
    loop.start()
    try:
        deadline = time.time() + 5
        while not beats and time.time() < deadline:
            time.sleep(0.02)
        assert len(beats) == 1
        time.sleep(0.3)
        queued_at = time.time()
        DatabaseManager(tmp_path / "wake.db").create_task(title="now please", prompt="p")
        while len(beats) < 2 and time.time() < queued_at + 5:
            time.sleep(0.02)
        assert len(beats) == 2, "the daemon slept on instead of picking up the new task"
        assert beats[1] - queued_at < 2.0           # not the 30 s heartbeat
    finally:
        daemon.running = False
        loop.join(timeout=5)


def test_daemon_still_wakes_on_its_timer(tmp_path, monkeypatch):
    from core.daemon import AutonomousDaemon
    daemon = AutonomousDaemon(workspace_dir=_ROOT, interval_seconds=1, db_file=str(tmp_path / "timer.db"),
                              log_file=str(tmp_path / "timer.log"))
    events.DAEMON_WAKE.clear()
    start = time.time()
    assert daemon._sleep_until_woken() is False
    assert 0.9 <= time.time() - start < 3


# ---------------- presence: is the app open? (docs/PROACTIVE_RESEARCH.md 1j) ----------------

class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_presence_counts_streams_and_measures_time_away():
    clock = Clock()
    p = events.Presence(clock)
    assert not p.is_open() and p.away_seconds() == 0          # just started: never "away for days"
    p.stream_opened()
    p.stream_opened()                                          # a reconnect can overlap the old stream
    clock.t += 3600
    assert p.is_open() and p.away_seconds() == 0 and p.last_seen() == clock.t
    p.stream_closed()
    assert p.is_open()                                         # one stream still open
    p.stream_closed()
    clock.t += 7200
    assert not p.is_open() and p.away_seconds() == 7200
    p.stream_closed()                                          # an extra close never goes negative
    assert not p.is_open()


def test_presence_restores_the_saved_time_after_a_restart():
    clock = Clock(10_000)
    p = events.Presence(clock)
    p.restore(10_000 - 5 * 3600)
    assert p.away_seconds() == 5 * 3600
    p.restore(None)
    assert p.away_seconds() == 5 * 3600                        # nothing saved: unchanged
    p.stream_opened()
    p.restore(1)
    assert p.is_open() and p.away_seconds() == 0               # an open app wins over an old saved time


def test_a_stream_marks_the_app_open_then_saves_when_it_closed(tmp_db, monkeypatch):
    clock = Clock(50_000)
    monkeypatch.setattr(events, "PRESENCE", events.Presence(clock))
    hud_state.set_db(tmp_db)
    sub = events.Subscription(5)
    seen = {}

    async def run():
        checks = {"n": 0}

        async def disconnected():
            checks["n"] += 1
            return checks["n"] > 1

        gen = events_api.event_stream(sub, disconnected, heartbeat=0.05, poll=0.01)
        async for chunk in gen:
            if not seen:
                seen["first"] = chunk
            if events.PRESENCE.is_open():
                seen["open_while_streaming"] = True
                seen["saved_while_open"] = tmp_db.get_state(events_api.APP_LAST_SEEN, "")
                clock.t += 600                                  # ten minutes in the app
    asyncio.run(asyncio.wait_for(run(), 10))
    assert seen["first"].startswith("retry: ")                  # the stream itself is unchanged
    assert seen["open_while_streaming"] and seen["saved_while_open"] == "50000"
    assert not events.PRESENCE.is_open()
    assert tmp_db.get_state(events_api.APP_LAST_SEEN, "") == f"{clock.t:.0f}"   # when it closed


# ---------------- background activity: the app's pulsing icon (docs/PROACTIVE_RESEARCH.md 1m) ----------------

def test_activity_moves_through_started_step_finished():
    clock = Clock(100.0)
    a = events.Activity(clock)
    assert a.snapshot()["active"] is False
    s = a.update("started", label="Researching in the background")
    assert s["active"] and s["started_at"] == 100.0 and s["found"] == 0
    s = a.update("step", step="Checking what you're waiting on (1 of 2)")
    assert s["active"] and s["step"].startswith("Checking") and s["label"] == "Researching in the background"
    clock.t = 160.0
    s = a.update("finished", found=3)
    assert not s["active"] and s["step"] == "" and s["found"] == 3 and s["finished_at"] == 160.0
    with pytest.raises(ValueError):
        a.update("paused")


def test_activity_events_have_their_own_name_and_task_events_are_unchanged(listener, monkeypatch):
    monkeypatch.setattr(events, "ACTIVITY", events.Activity())
    events.publish_activity("started", label="Researching in the background")
    got = _drain(listener)
    assert len(got) == 1
    text = events_api.format_event(got[0])
    assert text.startswith("event: activity\ndata: ") and '"_event"' not in text
    body = json.loads(text.split("data: ", 1)[1])
    assert body["state"] == "started" and body["active"] is True
    # a task event is exactly what it always was
    task = {"type": "done", "task_id": "task-1", "status": "done", "ts": 1.0}
    assert events_api.format_event(task) == 'event: task\ndata: {"type":"done","task_id":"task-1","status":"done","ts":1.0}\n\n'


def test_publish_activity_never_raises(monkeypatch):
    class Broken:
        def update(self, *a, **k):
            raise RuntimeError("boom")
    monkeypatch.setattr(events, "ACTIVITY", Broken())
    events.publish_activity("started")                          # background work must not fail because of the bus


def test_activity_endpoint_needs_auth_and_returns_the_state(monkeypatch):
    monkeypatch.setattr(events, "ACTIVITY", events.Activity())
    events.ACTIVITY.update("started", label="Researching in the background", step="Looking into Styropyro")
    local = client_from(hud_app)
    data = local.get("/api/activity").json()
    assert data["active"] is True and data["step"] == "Looking into Styropyro"
    assert client_from(hud_app, LAN_ADDR).get("/api/activity").status_code in (401, 403)
