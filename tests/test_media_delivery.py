"""
Videos: the agent's side of TiktokVideos
========================================
TiktokVideos (a separate tool) writes, renders and posts the videos; the agent runs it, relays progress
to the task card and records its AI usage. The owner watches finished videos in DeskAI. These tests replace
the tool with a stand-in, so no test ever renders or posts anything real.
"""

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos (it would render and post real videos): point the agent at a
# folder where it isn't installed, so an accidental call fails harmlessly
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

from core import tiktok_videos
from core.database import DatabaseManager

TEST_DIR = Path(tempfile.mkdtemp(prefix="media_delivery_test_"))


class FakeTool:
    """Stands in for tiktok_videos.run: records the calls and replays scripted results."""

    def __init__(self, render=None, post=None):
        self.calls, self.render, self.post = [], render, post

    def __call__(self, args, on_progress=None, on_start=None, timeout=0):
        self.calls.append(args)
        if on_start:
            on_start(4242)
        step = self.render if args[0] == "render" else self.post
        if isinstance(step, Exception):
            raise step
        if on_progress and args[0] == "render":
            on_progress({"stage": "capture", "label": "Capturing frames", "percent": 40, "detail": "Frame 1 of 2",
                         "eta_seconds": 100, "updated_at": "x"})
        return step or {}


def _dispatcher(db):
    from core.daemon import EngineDispatcher
    return EngineDispatcher(TEST_DIR / "ws", db=db, task_root=TEST_DIR / "ws" / "workspaces")


def _with(tool, fn):
    saved = tiktok_videos.run
    tiktok_videos.run = tool
    try:
        return fn()
    finally:
        tiktok_videos.run = saved


def test_render_records_its_quote_progress_and_ai_usage():
    db = DatabaseManager(TEST_DIR / "render.db")
    video = TEST_DIR / "vibe_check_1.mp4"
    video.write_bytes(b"x")
    tool = FakeTool(render={"video": str(video), "quote": "Rest is a requirement.", "title": "Treasure Chest Reveal",
                            "ai_usage": [{"model": "gemini-3.5-flash-lite", "prompt_tokens": 290, "completion_tokens": 20}]})
    tid = db.create_task(title="Render", prompt="p", engine="media_pipeline", metadata={"action": "video"})
    code, out, _, _ = _with(tool, lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 0 and "vibe_check_1.mp4" in out and "Rest is a requirement." in out, out
    assert tool.calls == [["render"]]                           # TiktokVideos writes the quote itself
    meta = db.get_task(tid)["metadata"]
    assert meta["video_path"] == str(video) and meta["quote"] == "Rest is a requirement."
    row = db.get_connection().execute("select model, purpose, prompt_tokens, provider from llm_usage").fetchone()
    assert tuple(row) == ("gemini-3.5-flash-lite", "affirmation", 290, "gemini")
    task = db.get_task(tid)
    assert task["worker_pid"] is None                           # cleared once the tool has finished
    print("  - a render records its quote, and the quote's AI call counts toward the free budget")


def test_a_given_quote_is_passed_on():
    db = DatabaseManager(TEST_DIR / "given.db")
    video = TEST_DIR / "vibe_check_2.mp4"
    video.write_bytes(b"x")
    tool = FakeTool(render={"video": str(video), "quote": "You are enough.", "title": "3D Glass Monolith"})
    tid = db.create_task(title="Render", prompt="p", engine="media_pipeline",
                         metadata={"action": "video", "quote": "You are enough."})
    code, _, _, _ = _with(tool, lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 0 and tool.calls == [["render", "--quote", "You are enough."]]


def test_a_chosen_scene_is_passed_on():
    """The app's /render bottle <quote> (and chat's 'a bottle video'): the scene reaches TiktokVideos' --scene."""
    from core import actions
    db = DatabaseManager(TEST_DIR / "scene.db")
    video = TEST_DIR / "vibe_check_7.mp4"
    video.write_bytes(b"x")
    tool = FakeTool(render={"video": str(video), "quote": "You are enough.", "title": "Message in a Bottle"})
    tid = actions.queue_video(db, "You are enough.", scene="message_bottle")
    task = db.get_task(tid)
    assert task["metadata"]["scene"] == "bottle" and "Message in a Bottle" in task["title"]
    code, _, _, _ = _with(tool, lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 0 and tool.calls == [["render", "--quote", "You are enough.", "--scene", "bottle"]]
    # No scene (or one that isn't a scene): no --scene, TiktokVideos takes turns
    assert actions.video_scene("Treasure Chest") == "chest" and actions.video_scene("spaceship") is None
    assert "scene" not in db.get_task(actions.queue_video(db, None, scene="spaceship"))["metadata"]


def test_render_then_post_and_tiktok_failures_say_why():
    db = DatabaseManager(TEST_DIR / "tiktok.db")
    video = TEST_DIR / "vibe_check_3.mp4"
    video.write_bytes(b"x")
    tool = FakeTool(render={"video": str(video), "quote": "q", "title": "t"}, post={"posted": True})
    tid = db.create_task(title="Render and post", prompt="p", engine="media_pipeline", metadata={"action": "tiktok"})
    code, out, engine, _ = _with(tool, lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 0 and engine == "social_publisher" and "posted" in out.lower()
    assert tool.calls == [["render"], ["post", "--video", "vibe_check_3.mp4"]]

    tool = FakeTool(post=tiktok_videos.TiktokVideosError("the saved TikTok login has expired; run login_tiktok.bat"))
    tid = db.create_task(title="Post", prompt="p", engine="media_pipeline",
                         metadata={"action": "tiktok", "video_path": str(video)})
    code, out, engine, _ = _with(tool, lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 1 and engine == "social_publisher" and "login has expired" in out
    assert tool.calls == [["post", "--video", "vibe_check_3.mp4"]]

    tool = FakeTool(render=tiktok_videos.TiktokVideosError("the render failed: Edge crashed"))
    tid = db.create_task(title="Render", prompt="p", engine="media_pipeline", metadata={"action": "video"})
    code, out, _, _ = _with(tool, lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 1 and out.startswith("The video couldn't be made") and "Edge crashed" in out
    print("  - render-and-post works, and TikTok or render failures say why")


def test_cancelled_jobs_never_render_or_post():
    db = DatabaseManager(TEST_DIR / "cancel.db")
    video = TEST_DIR / "vibe_check_4.mp4"
    video.write_bytes(b"x")

    tid = db.create_task(title="Post", prompt="p", engine="media_pipeline",
                         metadata={"action": "tiktok", "video_path": str(video)})
    db.cancel_task(tid, "Cancelled for a test")
    tool = FakeTool(post={"posted": True})
    code, out, _, _ = _with(tool, lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 1 and out.startswith("Cancelled") and tool.calls == []

    tid = db.create_task(title="Render", prompt="p", engine="media_pipeline", metadata={"action": "video"})
    db.cancel_task(tid, "Cancelled for a test")
    tool = FakeTool(render={"video": "never"})
    code, out, _, _ = _with(tool, lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 1 and out.startswith("Cancelled before it started") and tool.calls == []

    # Stopped mid-way: the cancel ends the tool, which then reports no result
    tid = db.create_task(title="Render", prompt="p", engine="media_pipeline", metadata={"action": "video"})

    class CancelledMidway(FakeTool):
        def __call__(self, args, **kw):
            db.cancel_task(tid, "Cancelled for a test")
            raise tiktok_videos.TiktokVideosError("TiktokVideos stopped without a result (exit code 1)")
    code, out, _, _ = _with(CancelledMidway(), lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 1 and out.startswith("Cancelled")

    # A render that finishes after a cancel is not posted
    tid = db.create_task(title="Render and post", prompt="p", engine="media_pipeline", metadata={"action": "tiktok"})

    class CancelledDuringRender(FakeTool):
        def __call__(self, args, **kw):
            self.calls.append(args)
            db.cancel_task(tid, "Cancelled for a test")
            return {"video": str(video), "quote": "q", "title": "t"}
    tool = CancelledDuringRender()
    code, out, _, _ = _with(tool, lambda: _dispatcher(db)._execute_media_pipeline(db.get_task(tid)))
    assert code == 1 and "not posted" in out and tool.calls == [["render"]]
    print("  - cancelled jobs never render or post")


if __name__ == "__main__":
    import shutil
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_") and callable(fn):
                print(f"[{name}]")
                fn()
        print("\nALL MEDIA TESTS PASSED")
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)
