"""
Task details (2026-10-07)
=========================
DeskAI's mission details show who or what started a task (`source`, stamped on every creation path) and, for a
video task, a small thumbnail kept after the video itself is pruned (`thumb_url`, GET /api/tasks/{id}/thumb),
plus the video's stream while it's still kept (`video_url`). Also: the number of videos kept is the agent's
VIDEO_KEEP_COUNT, passed on to TiktokVideos.
"""

import subprocess
import urllib.request
from pathlib import Path

import pytest

from core import actions, config, schedules, task_thumbs, tiktok_videos
from core.database import DatabaseManager
from interfaces.hud import app as hud_app
from interfaces.hud import common as hud_common, state as hud_state
from tests._hud_client import LAN_ADDR, LOCAL_ADDR, client_from

ROOT = Path(__file__).resolve().parent.parent


class FakeMemory:
    def __init__(self):
        self.events = []

    def record_event(self, text):
        self.events.append(text)

    def add_message(self, *a, **k):
        return 1


@pytest.fixture
def hud(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "state.db")
    original = (hud_state.db, hud_state._memory, hud_state.HUD_AUTH_TOKEN)
    hud_state.set_db(db)
    hud_state.set_memory(FakeMemory())
    hud_state.HUD_AUTH_TOKEN = ""
    videos = tmp_path / "videos"
    videos.mkdir()
    monkeypatch.setattr(hud_state, "VIDEOS_DIR", videos)
    monkeypatch.setattr(config, "TASK_THUMBS_DIR", tmp_path / "task_thumbs")
    hud_common._proposals.clear()
    yield db, client_from(hud_app, addr=LOCAL_ADDR), videos
    hud_state.set_db(original[0])
    hud_state._memory = original[1]
    hud_state.HUD_AUTH_TOKEN = original[2]


def _source(client, task_id):
    return client.get(f"/api/tasks/{task_id}").json()["source"]


# ------------------------------------------------------------------------------------------------ source
def test_quick_dispatch_hark_and_body_sources(hud):
    db, client, _ = hud
    body = {"title": "Check disk", "prompt": "How full is the disk?"}
    tid = client.post("/api/tasks", json=body).json()["task_id"]
    assert _source(client, tid) == "DeskAI quick dispatch"
    tid = client.post("/api/tasks", json=body, headers={"X-Task-Source": "Hark"}).json()["task_id"]
    assert _source(client, tid) == "Hark" and db.get_task(tid)["metadata"]["source"] == "Hark"
    tid = client.post("/api/tasks", json={**body, "source": "  Laptop\n script "}).json()["task_id"]
    assert _source(client, tid) == "Laptop script"                     # one tidy line


def test_tasks_run_and_media_sources(hud):
    db, client, videos = hud
    tid = client.post("/api/tasks/run", json={"command": "Summarise the logs"}).json()["task_id"]
    assert _source(client, tid) == "DeskAI"
    tid = client.post("/api/tasks/run", json={"command": "Summarise the logs"},
                      headers={"X-Task-Source": "Hark"}).json()["task_id"]
    assert _source(client, tid) == "Hark"
    tid = client.post("/api/trigger_media", json={"action": "video"}).json()["task_id"]
    assert _source(client, tid) == "DeskAI Video Hub"
    (videos / "vibe_check_1.mp4").write_bytes(b"x")
    tid = client.post("/api/videos/vibe_check_1.mp4/publish").json()["task_id"]
    assert _source(client, tid) == "DeskAI Video Hub"


def test_chat_task_command_source(hud):
    db, client, _ = hud
    data = client.post("/api/chat", json={"message": "/task Tidy the downloads folder"}).json()
    assert _source(client, data["task_id"]) == "DeskAI chat (/task)"


def test_chat_tool_tasks_and_suggestions_keep_their_source(hud):
    db, client, _ = hud
    # The chat's tools pass their own source ("DeskAI chat", interfaces/hud/chat.py)
    tid = actions.queue_instruction(db, "Write the weekly summary", source="DeskAI chat")
    assert _source(client, tid) == "DeskAI chat"
    assert 'source="DeskAI chat"' in (ROOT / "interfaces" / "hud" / "chat.py").read_text(encoding="utf-8")
    pid = hud_common.store_proposals([{"instruction": "Clean up the old render folders"}])[0]["id"]
    tid = client.post(f"/api/proposals/{pid}/run").json()["task_id"]
    assert _source(client, tid) == "DeskAI suggestion"


def test_schedule_source_is_kept(hud):
    db, client, _ = hud
    s = schedules.ScheduleStore(db).create("Twice Daily", "task", "0 8,20 * * *", instruction="Check the site is up")
    tid = schedules.fire(db, s)["task_id"]
    assert _source(client, tid).startswith("schedule #") and "Twice Daily" in _source(client, tid)


def test_older_tasks_read_sensibly(hud):
    db, client, _ = hud
    old = db.create_task(title="Old", prompt="p", metadata={"source": "deskai"})
    older = db.create_task(title="Older", prompt="p")
    assert _source(client, old) == "DeskAI"
    assert _source(client, older) == "unknown"
    assert {t["id"]: t["source"] for t in client.get("/api/tasks").json()}[older] == "unknown"


# ------------------------------------------------------------------------------------------------ thumbnails
def _make_video(path: Path) -> Path:
    ffmpeg = config.resolve_ffmpeg_binary()
    try:
        subprocess.run([ffmpeg, "-f", "lavfi", "-i", "testsrc=size=360x640:rate=10:duration=2", "-pix_fmt",
                        "yuv420p", str(path), "-y"], capture_output=True, timeout=60)
    except Exception:
        pass
    if not path.is_file() or path.stat().st_size == 0:
        pytest.skip("ffmpeg isn't available here")
    return path


def test_render_saves_a_thumbnail_that_outlives_the_video(hud, tmp_path):
    db, client, videos = hud
    video = _make_video(videos / "vibe_check_100.mp4")
    from core.daemon import EngineDispatcher
    tid = db.create_task(title="Auto-Post", prompt="p", engine="media_pipeline", metadata={"action": "video"})
    saved = tiktok_videos.run
    tiktok_videos.run = lambda args, **kw: {"video": str(video), "quote": "Rest."}
    try:
        code, _, _, _ = EngineDispatcher(tmp_path / "ws", db=db, task_root=tmp_path / "ws" / "w")\
            ._execute_media_pipeline(db.get_task(tid))
    finally:
        tiktok_videos.run = saved
    assert code == 0
    db.update_task(tid, {"phase": "completed"})
    meta = db.get_task(tid)["metadata"]
    thumb = config.TASK_THUMBS_DIR / meta["thumb"]
    assert thumb.is_file() and 0 < thumb.stat().st_size < 50_000

    card = client.get(f"/api/tasks/{tid}").json()
    assert card["thumb_url"] == f"/api/tasks/{tid}/thumb"
    assert card["video_url"] == "/videos/vibe_check_100.mp4" and card["video_kept"] is True
    res = client.get(card["thumb_url"])
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"

    video.unlink()                                               # pruned by TiktokVideos
    card = client.get(f"/api/tasks/{tid}").json()
    assert card["video_url"] is None and card["video_kept"] is False and card["result"] is None
    assert card["video_filename"] == "vibe_check_100.mp4"
    assert card["thumb_url"] and client.get(card["thumb_url"]).status_code == 200


def test_thumb_is_made_on_first_ask_and_404s(hud):
    db, client, videos = hud
    _make_video(videos / "vibe_check_200.mp4")
    tid = db.create_task(title="Render", prompt="p", engine="media_pipeline", phase="completed",
                         metadata={"video_path": str(videos / "vibe_check_200.mp4")})
    assert client.get(f"/api/tasks/{tid}").json()["thumb_url"] == f"/api/tasks/{tid}/thumb"
    assert client.get(f"/api/tasks/{tid}/thumb").status_code == 200
    assert db.get_task(tid)["metadata"]["thumb"] == f"{tid}.jpg"

    plain = db.create_task(title="Plain", prompt="p")
    card = client.get(f"/api/tasks/{plain}").json()
    assert card["thumb_url"] is None and card["video_url"] is None and card["video_kept"] is None
    assert client.get(f"/api/tasks/{plain}/thumb").status_code == 404
    assert client.get("/api/tasks/task-nope/thumb").status_code == 404


def test_thumb_needs_the_token(hud):
    db, client, _ = hud
    tid = db.create_task(title="Plain", prompt="p")
    hud_state.HUD_AUTH_TOKEN = "s" * 32
    lan = client_from(hud_app, addr=LAN_ADDR)
    assert lan.get(f"/api/tasks/{tid}/thumb").status_code == 401
    assert lan.get(f"/api/tasks/{tid}/thumb", headers={"X-HUD-Token": "s" * 32}).status_code == 404


def test_backfill_and_cap(hud):
    db, _, videos = hud
    _make_video(videos / "vibe_check_300.mp4")
    done = db.create_task(title="Render", prompt="p", engine="media_pipeline", phase="completed",
                          metadata={"video_path": str(videos / "vibe_check_300.mp4")})
    db.create_task(title="Running", prompt="p", engine="media_pipeline", phase="in_progress",
                   metadata={"video_path": str(videos / "vibe_check_300.mp4")})
    assert task_thumbs.backfill(db, videos) == 1                  # finished tasks only
    assert task_thumbs.find(db.get_task(done)) is not None
    assert task_thumbs.backfill(db, videos) == 0                  # once
    for i in range(5):
        (config.TASK_THUMBS_DIR / f"task-x{i}.jpg").write_bytes(b"j")
    assert task_thumbs.prune(keep=3) == 3
    assert len(list(config.TASK_THUMBS_DIR.glob("*.jpg"))) == 3


# ------------------------------------------------------------------------------------------------ video retention
def test_video_keep_count_reaches_tiktokvideos(monkeypatch, tmp_path):
    monkeypatch.setattr(tiktok_videos, "VIDEO_KEEP_COUNT", 50)
    monkeypatch.setattr(tiktok_videos, "TIKTOK_VIDEOS_DIR", tmp_path)
    (tmp_path / "tiktokvideos.py").write_text("")
    seen = {}

    class FakeProc:
        pid, returncode = 1, 0
        stdout = iter(['{"event": "done", "removed_temp_files": 0, "pruned_videos": 0}\n'])

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def fake_popen(cmd, env=None, **kw):
        seen["cmd"], seen["env"] = cmd, env
        return FakeProc()
    monkeypatch.setattr(tiktok_videos.subprocess, "Popen", fake_popen)
    tiktok_videos.cleanup(older_than=3600)
    assert seen["env"]["MAX_SAVED_VIDEOS"] == "50"
    assert seen["cmd"][-2:] == ["--keep", "50"]
    assert config._int_config("NO_SUCH_KEY_FOR_TESTS", 50) == 50
