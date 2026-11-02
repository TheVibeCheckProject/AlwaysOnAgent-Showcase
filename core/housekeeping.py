"""
Log housekeeping
================
The active logs (worker.log) rotate on their own: RotatingFileHandler caps
them by size. This removes the rest: log files and screenshots in logs/ that nothing has written
to in LOG_MAX_AGE_DAYS (old rotated copies, leftovers from earlier launchers). It runs during the
daemon's quiet-time maintenance and with DeskAI's Cleanup button (POST /api/cleanup).
"""

import time
from pathlib import Path
from typing import List, Optional

LOG_MAX_AGE_DAYS = 30
# Never removed, however old: affirmations.log is the record that keeps videos from repeating a quote
LOG_KEEP = {".gitkeep", "affirmations.log"}
# Routine lines older logs are full of; used to compact a log (they carry no information)
LOG_NOISE = ("--- Heartbeat #", "No pending tasks in backlog", "Sleeping until next heartbeat",
             ": idle, no tasks waiting.", "is actively executing in background", "Daemon is in STANDBY mode")


def prune_old_logs(logs_dir: Path, max_age_days: int = LOG_MAX_AGE_DAYS, now: Optional[float] = None) -> List[str]:
    """Deletes files in logs/ unchanged for max_age_days (except LOG_KEEP). Returns the names removed."""
    logs_dir = Path(logs_dir)
    if not logs_dir.is_dir():
        return []
    cutoff = (now or time.time()) - max_age_days * 86400
    removed = []
    for f in logs_dir.iterdir():
        try:
            if f.is_file() and f.name not in LOG_KEEP and f.stat().st_mtime < cutoff:
                f.unlink()
                removed.append(f.name)
        except OSError:
            pass            # in use (e.g. an open log on Windows): try again next time
    return sorted(removed)


def prune_old_files(folder: Path, max_age_seconds: float, now: Optional[float] = None) -> int:
    """Deletes the files directly in folder unchanged for max_age_seconds. Returns how many. For caches and leftovers
    nothing else cleans (2026-10-28 audit 4.5: data/tmp/share after a failed download, the chat's video thumbnails)."""
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    cutoff = (now or time.time()) - max_age_seconds
    removed = 0
    for f in folder.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass            # in use: next time
    return removed


def compact_log(path: Path, noise=LOG_NOISE) -> int:
    """Rewrites a log without its routine noise lines. Returns how many lines were dropped.
    Only for a log no process is writing to (e.g. while the agent is stopped)."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    kept = [line for line in lines if not any(n in line for n in noise)]
    tmp = path.with_name(path.name + ".compacting")
    tmp.write_text("".join(kept), encoding="utf-8")
    tmp.replace(path)
    return len(lines) - len(kept)


WORKSPACE_MAX_AGE_DAYS = 7


def _force_remove_readonly(func, path, exc_info):
    """Clear the readonly bit and retry deletion on Windows."""
    try:
        import os
        import stat
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def prune_old_workspaces(workspaces_dir: Path, max_age_days: int = WORKSPACE_MAX_AGE_DAYS,
                         now: Optional[float] = None) -> List[str]:
    """Deletes abandoned task workspaces older than max_age_days. Returns removed directory names.
    Uses AgentWork's discard when available to cleanly unregister worktrees and delete branches."""
    import shutil
    import subprocess
    import sys

    workspaces_dir = Path(workspaces_dir)
    if not workspaces_dir.is_dir():
        return []

    cutoff = (now or time.time()) - (max_age_days * 86400)
    removed: List[str] = []

    # Check if agentwork.py lives next to the workspaces dir
    agentwork_py = workspaces_dir.parent / "agentwork.py"
    has_agentwork = agentwork_py.is_file()

    for item in sorted(workspaces_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("."):
            continue
        try:
            if item.stat().st_mtime < cutoff:
                job_id = item.name
                pruned = False
                if has_agentwork:
                    try:
                        res = subprocess.run(
                            [sys.executable, str(agentwork_py), "discard", job_id],
                            capture_output=True,
                            timeout=15,
                            cwd=str(workspaces_dir.parent)
                        )
                        if res.returncode == 0 or not item.exists():
                            removed.append(job_id)
                            pruned = True
                    except Exception:
                        pass
                if not pruned and item.exists():
                    shutil.rmtree(item, onerror=_force_remove_readonly)
                    removed.append(job_id)
        except OSError:
            pass

    # Prune any dangling git worktrees across clones in repos/ if present
    repos_dir = workspaces_dir.parent / "repos"
    if repos_dir.is_dir():
        for clone in repos_dir.iterdir():
            if (clone / ".git").exists() or clone.name.endswith(".git"):
                try:
                    subprocess.run(["git", "worktree", "prune"], cwd=str(clone), capture_output=True, timeout=10)
                except Exception:
                    pass

    return sorted(removed)
