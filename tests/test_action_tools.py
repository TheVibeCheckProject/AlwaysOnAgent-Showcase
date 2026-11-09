"""
The assistant's action tools
============================
Chat can render videos, post finished videos to TikTok (only when the user's own message asks for TikTok),
start AgentWork jobs, cancel tasks and list videos; the task it starts comes back as the chat reply's
task_id so DeskAI shows a live card. The tasks are the same ones the app's buttons create.
"""

import os
import sys
import tempfile
import time
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
os.environ.setdefault("STATE_DB_PATH", str(Path(_IMPORT_TEMP_DIR.name) / "action_tools_test.db"))

from core import actions
from core.assistant_tools import MAX_STARTS, AssistantTools
from core.database import CANCELLED_EXIT_CODE, DatabaseManager
from interfaces.hud import app as hud_app
from interfaces.hud import state as hud_state
from tests._hud_client import LOCAL_ADDR, client_from


class FakeAgentWork:
    def list_projects(self):
        return [{"name": "site", "description": "The website"}, {"name": "alwaysonagent", "description": "Me"}]


class FakeMemory:
    def __init__(self):
        self.events = []

    def record_event(self, text):
        self.events.append(text)


@pytest.fixture
def env(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    videos = tmp_path / "output_videos"
    videos.mkdir()
    for i, name in enumerate(["vibe_check_1.mp4", "vibe_check_2.mp4", "vibe_check_2_tg.mp4"]):
        (videos / name).write_bytes(b"x")
        os.utime(videos / name, (time.time() + i, time.time() + i))
    mem = FakeMemory()

    def tools(text):
        return AssistantTools(db, mem, tmp_path, agentwork=FakeAgentWork(), user_text=text, source="test chat",
                              videos_dir=videos)
    return db, tools, mem, videos


def test_render_video_starts_the_same_task_as_the_button(env):
    db, tools, _, _ = env
    t = tools("make me a video")
    out = t.call("render_video", {})
    task = db.get_task(t.started[0])
    assert "Started task-" in out and task["engine"] == "media_pipeline" and task["phase"] == "backlog"
    assert task["metadata"]["action"] == "video" and task["metadata"]["quote"] is None
    assert task["metadata"]["source"] == "test chat" and task["timeout_seconds"] == 2400   # the rainy window's render

    t = tools("video with: You are enough")
    t.call("render_video", {"quote": "You are enough"})
    assert db.get_task(t.started[0])["metadata"]["quote"] == "You are enough"


def test_posting_to_tiktok_is_the_users_tap(env, monkeypatch):
    """Chat never posts: it shows a card (a 'tiktok' proposal), and the owner's tap on Run queues the post."""
    from interfaces.hud import app as hud_app
    from interfaces.hud import common
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    db, tools, _, videos = env
    t = tools("render one and post it to tiktok")
    t.call("render_video", {"post_to_tiktok": True})                     # accepted, never acted on
    assert db.get_task(t.started[0])["metadata"]["action"] == "video"

    t = tools("post the last one to TikTok")
    out = t.call("post_video_to_tiktok", {})
    assert "vibe_check_2.mp4" in out and "Nothing is posted yet" in out    # newest, never an old _tg copy
    assert t.started == [] and len(db.list_tasks()) == 1                  # (only the render above)
    assert t.proposals == [{"instruction": "Post vibe_check_2.mp4 to TikTok", "reason": "", "project": None,
                            "kind": "tiktok", "add": {"video": "vibe_check_2.mp4"}}]
    assert "no video called" in tools("post x to tik tok").call("post_video_to_tiktok", {"video": "nope.mp4"})

    card = common.store_proposals(t.proposals)[0]
    original = hud_state.db
    hud_state.set_db(db)
    monkeypatch.setattr(hud_state, "VIDEOS_DIR", videos)
    try:
        r = client_from(hud_app, addr=LOCAL_ADDR).post(f"/api/proposals/{card['id']}/run")
    finally:
        hud_state.set_db(original)
    task = db.get_task(r.json()["task_id"])
    assert task["metadata"]["action"] == "tiktok"
    assert task["metadata"]["video_path"] == str((videos / "vibe_check_2.mp4").resolve())


def test_project_jobs_start_on_known_projects_only(env):
    db, tools, mem, _ = env
    t = tools("fix the website header")
    assert "no AgentWork project called 'blog'" in t.call("start_project_job", {"instruction": "Fix the header", "project": "blog"})
    out = t.call("start_project_job", {"instruction": "Fix the header spacing on mobile", "project": "site"})
    task = db.get_task(t.started[0])
    assert "own branch" in out and task["engine"] == "agentwork" and task["title"].startswith("[site] ")
    assert task["metadata"]["project"] == "site" and task["timeout_seconds"] == 1800
    assert any("started task-" in e for e in mem.events)


def test_start_limits_and_questions(env):
    db, tools, _, _ = env
    t = tools("three videos please")
    for _ in range(MAX_STARTS):
        t.call("render_video", {})
    assert "at most" in t.call("render_video", {})
    assert len(db.list_tasks()) == MAX_STARTS

    t = tools("change something")
    t.call("ask_user", {"question": "Which project?", "options": ["site", "alwaysonagent"]})
    assert "wait for their answer" in t.call("start_project_job", {"instruction": "Change the colors", "project": "site"})


def test_cancel_and_list_videos(env):
    db, tools, _, _ = env
    tid = actions.queue_video(db)
    t = tools("stop that")
    assert f"Cancelled {tid}" in t.call("cancel_task", {"task_id": tid})
    assert db.get_task(tid)["exit_code"] == CANCELLED_EXIT_CODE
    assert "already finished" in t.call("cancel_task", {"task_id": tid})
    listing = t.call("list_videos", {})
    assert listing.index("vibe_check_2.mp4") < listing.index("vibe_check_1.mp4") and "_tg" not in listing


def test_chat_reply_carries_the_started_task(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    original = (hud_state.db, hud_state._memory, hud_state._agentwork)
    hud_state.set_db(db)
    hud_state.set_agentwork(FakeAgentWork())

    class RenderingMemory:
        llm = None

        def __init__(self, db):
            self.db = db

        def respond(self, user_text, system_context="", tools=None, **kwargs):
            tools.call("render_video", {})
            return "Rendering it now."

        def record_event(self, text):
            pass

        def add_message(self, *a, **k):
            return 1

    hud_state.set_memory(RenderingMemory(db))
    try:
        data = client_from(hud_app, addr=LOCAL_ADDR).post("/api/chat", json={"message": "make a video"}).json()
        assert data["task_id"] and db.get_task(data["task_id"])["engine"] == "media_pipeline"
    finally:
        hud_state.set_db(original[0])
        hud_state._memory = original[1]
        hud_state.set_agentwork(original[2])
