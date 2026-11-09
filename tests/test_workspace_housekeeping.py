import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

from core.housekeeping import prune_old_workspaces


def test_prune_old_workspaces(tmp_path):
    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()

    old_task = ws_dir / "task-old"
    old_task.mkdir()
    (old_task / "code.py").write_text("print('old')", encoding="utf-8")

    fresh_task = ws_dir / "task-fresh"
    fresh_task.mkdir()
    (fresh_task / "code.py").write_text("print('fresh')", encoding="utf-8")

    now = time.time()
    old_mtime = now - (8 * 86400)  # 8 days old
    os.utime(str(old_task), (old_mtime, old_mtime))

    pruned = prune_old_workspaces(ws_dir, max_age_days=7, now=now)
    assert "task-old" in pruned
    assert not old_task.exists()
    assert fresh_task.exists()


def test_prune_nonexistent_directory():
    assert prune_old_workspaces(Path("/nonexistent/path/workspaces")) == []


def test_old_files_in_a_cache_folder_are_pruned(tmp_path):
    """2026-10-28 audit 4.5: data/tmp/share leftovers and the chat's video thumbnails were never removed."""
    import os
    import time

    from core.housekeeping import prune_old_files
    old, fresh = tmp_path / "old.zip", tmp_path / "fresh.zip"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    assert prune_old_files(tmp_path, 3600) == 1
    assert not old.exists() and fresh.exists()
    assert prune_old_files(tmp_path / "missing", 3600) == 0
