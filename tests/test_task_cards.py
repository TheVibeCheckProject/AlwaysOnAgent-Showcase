"""
Live task cards for DeskAI
==========================
Render progress with a time estimate, "cancelled" as its own outcome, GET /api/tasks/{id} with a
status line and the finished video, and Idempotency-Key so a retried request can't queue a task twice.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos (it would render and send real videos): point the agent at a
# folder where it isn't installed, so an accidental call fails harmlessly
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

_IMPORT_TEMP_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ.setdefault("STATE_DB_PATH", str(Path(_IMPORT_TEMP_DIR.name) / "task_cards_test.db"))

from core import push
from core.database import CANCELLED_EXIT_CODE, DatabaseManager
from interfaces.hud import app as hud_app
from interfaces.hud import state as hud_state
from tests._hud_client import LOCAL_ADDR, client_from


@pytest.fixture
def hud(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "state.db")
    original_db = hud_state.db
    hud_state.set_db(db)
    monkeypatch.setattr(hud_state, "VIDEOS_DIR", tmp_path / "videos")
    (tmp_path / "videos").mkdir()
    yield db, client_from(hud_app, addr=LOCAL_ADDR), tmp_path / "videos"
    hud_state.set_db(original_db)


def test_progress_shows_only_while_running(hud):
    db, client, _ = hud
    tid = db.create_task(title="Render", prompt="render", engine="media_pipeline")
    db.set_task_progress(tid, {"stage": "capture", "label": "Capturing frames", "percent": 42,
                               "detail": "Frame 480 of 1243", "eta_seconds": 95})
    card = client.get(f"/api/tasks/{tid}").json()
    assert card["progress"] is None and card["status_text"] == "Waiting to start"

    db.update_task(tid, {"phase": "in_progress"})
    card = client.get(f"/api/tasks/{tid}").json()
    assert card["progress"]["percent"] == 42 and card["progress"]["detail"] == "Frame 480 of 1243"
    assert card["status_text"] == "Capturing frames: 42%, about 1 min 35 s left"
    assert "progress_json" not in card
    assert client.get("/api/tasks").json()[0]["status_text"] == card["status_text"]

    db.update_task(tid, {"phase": "completed"})
    card = client.get(f"/api/tasks/{tid}").json()
    assert card["progress"] is None and card["status_text"] == "Completed" and card["cancelled"] is False
    assert client.get("/api/tasks/task-999").status_code == 404


def test_finished_video_is_linked_on_the_card(hud):
    db, client, videos = hud
    (videos / "vibe_check_1.mp4").write_bytes(b"x")
    tid = db.create_task(title="Render", prompt="render", engine="media_pipeline", phase="completed",
                         metadata={"video_path": str(videos / "vibe_check_1.mp4"), "delivered": True})
    result = client.get(f"/api/tasks/{tid}").json()["result"]
    assert result == {"type": "video", "filename": "vibe_check_1.mp4", "url": "/videos/vibe_check_1.mp4"}

    (videos / "vibe_check_1.mp4").unlink()                      # pruned later: no dead link
    assert client.get(f"/api/tasks/{tid}").json()["result"] is None


def test_cancel_is_its_own_outcome_and_cannot_overwrite_a_result(hud):
    db, client, _ = hud
    waiting = db.create_task(title="Render", prompt="render", engine="media_pipeline")
    assert client.post(f"/api/cancel/{waiting}").status_code == 200
    card = client.get(f"/api/tasks/{waiting}").json()
    assert card["phase"] == "failed" and card["exit_code"] == CANCELLED_EXIT_CODE
    assert card["cancelled"] is True and card["status_text"] == "Cancelled"

    done = db.create_task(title="Done", prompt="x", phase="completed")
    res = client.post(f"/api/cancel/{done}")
    assert res.status_code == 409
    assert db.get_task(done)["phase"] == "completed"

    failed = db.create_task(title="Broke", prompt="x", phase="failed", last_error="Engine 'cloud' failed")
    card = client.get(f"/api/tasks/{failed}").json()
    assert card["cancelled"] is False and card["status_text"] == "Failed: Engine 'cloud' failed"


def test_retry_after_cancel_clears_old_progress(hud):
    db, client, _ = hud
    tid = db.create_task(title="Render", prompt="render", engine="media_pipeline", phase="in_progress")
    db.set_task_progress(tid, {"label": "Capturing frames", "percent": 80})
    assert db.cancel_task(tid, "Cancelled for a test")
    assert db.retry_task(tid)
    db.update_task(tid, {"phase": "in_progress"})
    assert client.get(f"/api/tasks/{tid}").json()["progress"] is None


def test_idempotency_key_returns_the_first_task(hud):
    db, client, _ = hud
    headers = {"Idempotency-Key": "tap-7f3a"}
    first = client.post("/api/trigger_media", json={"action": "video"}, headers=headers).json()
    again = client.post("/api/trigger_media", json={"action": "video"}, headers=headers).json()
    assert again["task_id"] == first["task_id"] and again["duplicate"] is True and "duplicate" not in first
    assert len(db.list_tasks()) == 1

    other = client.post("/api/trigger_media", json={"action": "video"}, headers={"Idempotency-Key": "tap-2"}).json()
    no_key = client.post("/api/trigger_media", json={"action": "video"}).json()
    assert len({first["task_id"], other["task_id"], no_key["task_id"]}) == 3

    body = {"title": "Tidy", "prompt": "Tidy the render folders"}
    a = client.post("/api/tasks", json=body, headers={"Idempotency-Key": "k1"}).json()
    b = client.post("/api/tasks", json=body, headers={"Idempotency-Key": "k1"}).json()
    assert a["task_id"] == b["task_id"] and b["duplicate"] is True

    c = client.post("/api/tasks/run", json={"command": "Check disk space"}, headers={"Idempotency-Key": "k2"}).json()
    d = client.post("/api/tasks/run", json={"command": "Check disk space"}, headers={"Idempotency-Key": "k2"}).json()
    assert c["task_id"] == d["task_id"] and d["duplicate"] is True


def test_cancelled_task_sends_no_push(hud, monkeypatch):
    db, _, _ = hud
    sent = []
    monkeypatch.setattr(push, "send", lambda kind, *a, **k: sent.append(kind) or 1)
    watcher = push.TaskPushWatcher(db, interval=0)
    tid = db.create_task(title="Render", prompt="render", engine="media_pipeline", phase="in_progress")
    watcher.check_once()
    db.cancel_task(tid, "Stopped")          # reason doesn't have to say "cancelled"
    watcher.check_once()
    assert sent == []
