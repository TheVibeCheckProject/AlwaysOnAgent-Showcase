"""
Task thumbnails
===============
A small JPEG for every task that produced a video, kept in TASK_THUMBS_DIR (data/task_thumbs) after the video
itself is pruned (TiktokVideos keeps only the newest VIDEO_KEEP_COUNT videos), so DeskAI's task details still
show what was made. One file per task, `<task id>.jpg`, about 240 px wide (~15-30 KB); the newest
TASK_THUMBS_KEEP are kept. Served by GET /api/tasks/{id}/thumb (interfaces/hud/tasks.py).

The frame grab is the same ffmpeg call the Video Hub's thumbnails use (GET /api/videos/{name}/thumbnail).
Added 2026-10-07 for the tappable mission details.
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from core import config
except ImportError:
    import config

logger = logging.getLogger("TaskThumbs")
THUMB_WIDTH = 240
THUMB_QUALITY = 7          # ffmpeg's -q:v (2 best .. 31 worst); 7 keeps a 240 px frame well under 50 KB
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


def extract_frame(video: Path, out: Path, width: int = 360, quality: int = 3, timeout: float = 10) -> bool:
    """One frame of `video` (at 1 s, else the first) as a JPEG `width` px wide. True when `out` was written."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = config.resolve_ffmpeg_binary()
    for ss in ("00:00:01", "00:00:00"):
        cmd = [ffmpeg_bin, "-ss", ss, "-i", str(video), "-vf", f"scale={int(width)}:-2", "-frames:v", "1",
               "-update", "1", "-q:v", str(int(quality)), str(out), "-y"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if out.is_file() and out.stat().st_size > 0:
                return True
        except Exception as e:
            logger.debug(f"ffmpeg frame grab failed for {video.name}: {e}")
    return False


def thumbs_dir() -> Path:
    return Path(config.TASK_THUMBS_DIR)


def thumb_path(task_id: str) -> Path:
    return thumbs_dir() / f"{_SAFE_ID.sub('_', str(task_id))[:80]}.jpg"


def find(task: Dict[str, Any]) -> Optional[Path]:
    """The task's saved thumbnail, or None."""
    p = thumb_path(task.get("id") or "")
    return p if p.is_file() and p.stat().st_size > 0 else None


def video_of(task: Dict[str, Any], videos_dir: Optional[Path] = None) -> Optional[Path]:
    """The task's finished video if it's still on disk (inside the videos folder), else None."""
    name = Path(str((task.get("metadata") or {}).get("video_path") or "")).name
    if not name:
        return None
    folder = Path(videos_dir or config.VIDEOS_DIR)
    p = folder / name
    return p if p.is_file() else None


def make(task: Dict[str, Any], video: Path) -> Optional[Path]:
    """Writes the task's thumbnail from `video` (once; an existing one is kept). Returns its path or None."""
    existing = find(task)
    if existing:
        return existing
    out = thumb_path(task.get("id") or "")
    if not extract_frame(Path(video), out, width=THUMB_WIDTH, quality=THUMB_QUALITY):
        return None
    prune()
    return out


def stamp(db, task_id: str, path: Path) -> None:
    """Records the thumbnail in the task's metadata (`thumb`: the file's name in TASK_THUMBS_DIR)."""
    task = db.get_task(task_id) if db else None
    if not task:
        return
    meta = dict(task.get("metadata") or {})
    if meta.get("thumb") != path.name:
        meta["thumb"] = path.name
        db.update_task(task_id, {"metadata": meta})


def ensure(db, task: Dict[str, Any], videos_dir: Optional[Path] = None) -> Optional[Path]:
    """The task's thumbnail, made now from its video when it has none yet and the video is still kept."""
    found = find(task)
    if found:
        return found
    video = video_of(task, videos_dir)
    if video is None:
        return None
    made = make(task, video)
    if made and db and task.get("phase") in ("completed", "failed"):
        stamp(db, task["id"], made)          # finished tasks only: a running task's daemon owns its metadata
    return made


def backfill(db, videos_dir: Optional[Path] = None, limit: int = 20) -> int:
    """Thumbnails for finished video tasks whose video is still on disk (cheap: one frame each). Returns how many."""
    made = 0
    for task in db.list_tasks(limit=300):
        if made >= limit:
            break
        if task.get("phase") not in ("completed", "failed") or find(task) is not None:
            continue
        if video_of(task, videos_dir) is None:
            continue
        if ensure(db, task, videos_dir):
            made += 1
    return made


def prune(keep: Optional[int] = None) -> int:
    """Keeps the newest `keep` (TASK_THUMBS_KEEP) thumbnails. Returns how many were removed."""
    keep = int(keep if keep is not None else config.TASK_THUMBS_KEEP)
    folder = thumbs_dir()
    if not folder.is_dir():
        return 0
    files = sorted(folder.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed
