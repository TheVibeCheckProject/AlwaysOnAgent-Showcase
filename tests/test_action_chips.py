import os
import sys
import tempfile
from pathlib import Path
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

from interfaces.hud.action_chips import build_task_action_chips


def test_in_progress_task_action_chips():
    task = {"id": "task-100", "phase": "in_progress"}
    chips = build_task_action_chips(task)
    assert len(chips) == 1
    assert chips[0]["label"] == "⏹ Cancel"
    assert chips[0]["action"] == "post"
    assert chips[0]["url"] == "/api/cancel/task-100"
    assert chips[0]["variant"] == "danger"


def test_completed_video_render_chips():
    task = {
        "id": "task-101",
        "phase": "completed",
        "metadata": {"action": "video"}
    }
    result = {"type": "video", "filename": "vibe_check_123.mp4", "url": "/videos/vibe_check_123.mp4"}
    chips = build_task_action_chips(task, result=result)
    assert len(chips) == 2
    assert chips[0]["label"] == "▶ Play Preview"
    assert chips[0]["action"] == "stream"
    assert chips[1]["label"] == "🚀 Post to TikTok"
    assert chips[1]["action"] == "post"
    assert chips[1]["url"] == "/api/videos/vibe_check_123.mp4/publish"


def test_completed_alwaysonagent_update_chips():
    task = {
        "id": "task-102",
        "phase": "completed",
        "needs_input": False,
        "metadata": {"project": "alwaysonagent"}
    }
    chips = build_task_action_chips(task)
    assert len(chips) == 2
    assert chips[0]["label"] == "🔍 Preview diff"
    assert chips[0]["action"] == "chat"
    assert chips[0]["command"] == "Show diff for task-102"
    # the tap itself is the go-ahead: a direct call, no chat turn
    assert chips[1]["label"] == "⚡ Apply update"
    assert chips[1]["action"] == "post"
    assert chips[1]["url"] == "/api/tasks/task-102/apply"


def test_any_other_agentwork_job_can_be_previewed_but_not_sent_live():
    """A job on a repo set up from DeskAI's Repos screen (or the sandbox): Preview diff, no go-live chip."""
    task = {"id": "task-076", "phase": "completed", "engine": "agentwork", "metadata": {"project": "sandbox-site"}}
    assert build_task_action_chips(task) == [{"label": "🔍 Preview diff", "action": "chat",
                                              "command": "Show diff for task-076", "variant": "secondary"}]
    plain = {"id": "task-077", "phase": "completed", "engine": "cloud", "metadata": {}}
    assert build_task_action_chips(plain) == []


def test_completed_needs_input_chips():
    task = {
        "id": "task-103",
        "phase": "completed",
        "needs_input": True,
        "metadata": {"project": "alwaysonagent"}
    }
    chips = build_task_action_chips(task)
    assert len(chips) == 1
    assert chips[0]["label"] == "💬 Discuss Blockers"
    assert chips[0]["action"] == "chat"
    assert chips[0]["command"] == "Explain why task-103 needs my input"


def test_failed_task_retry_chip():
    task = {"id": "task-104", "phase": "failed"}
    chips = build_task_action_chips(task)
    assert len(chips) == 1
    assert chips[0]["label"] == "🔄 Retry Task"
    assert chips[0]["action"] == "post"
    assert chips[0]["url"] == "/api/retry/task-104"
