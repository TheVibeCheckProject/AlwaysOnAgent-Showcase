import os
import subprocess
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

from core.self_update import get_task_diff


def test_get_task_diff_missing_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("core.self_update.AGENT_WORK_DIR", tmp_path)
    res = get_task_diff("task-missing")
    assert "not available" in res


def test_get_task_diff_no_changes(tmp_path, monkeypatch):
    ws_dir = tmp_path / "workspaces" / "task-clean"
    ws_dir.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(ws_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(ws_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(ws_dir), capture_output=True, check=True)
    (ws_dir / "file.txt").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=str(ws_dir), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws_dir), capture_output=True, check=True)

    monkeypatch.setattr("core.self_update.AGENT_WORK_DIR", tmp_path)
    res = get_task_diff("task-clean")
    assert res == "No changes detected in workspace."


def test_get_task_diff_with_edits(tmp_path, monkeypatch):
    ws_dir = tmp_path / "workspaces" / "task-dirty"
    ws_dir.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(ws_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(ws_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(ws_dir), capture_output=True, check=True)
    (ws_dir / "file.txt").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=str(ws_dir), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws_dir), capture_output=True, check=True)

    # Edit file
    (ws_dir / "file.txt").write_text("modified", encoding="utf-8")

    monkeypatch.setattr("core.self_update.AGENT_WORK_DIR", tmp_path)
    res = get_task_diff("task-dirty")
    assert "+modified" in res
    assert "-initial" in res


def test_get_task_diff_untracked(tmp_path, monkeypatch):
    ws_dir = tmp_path / "workspaces" / "task-untracked"
    ws_dir.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(ws_dir), capture_output=True, check=True)
    (ws_dir / "new_file.py").write_text("print('hello')", encoding="utf-8")

    monkeypatch.setattr("core.self_update.AGENT_WORK_DIR", tmp_path)
    res = get_task_diff("task-untracked")
    assert "Untracked files" in res
    assert "new_file.py" in res
