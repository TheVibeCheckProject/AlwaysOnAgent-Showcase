"""
Calling TiktokVideos
====================
TiktokVideos (TIKTOK_VIDEOS_DIR, a separate tool) writes the quote, renders the video and posts to TikTok.
The agent runs it as its own program and reads the JSON lines it prints: progress events go to the task
card, and the last line is the result. Stopping a render is ending that program (its process tree).
"""

import json
import logging
import os
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional

try:
    from core.config import TIKTOK_VIDEOS_DIR, VIDEO_KEEP_COUNT, get_safe_subprocess_env
except ImportError:
    from config import TIKTOK_VIDEOS_DIR, VIDEO_KEEP_COUNT, get_safe_subprocess_env

logger = logging.getLogger("TiktokVideos")
BROWSER_ENV = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles", "ProgramData",
               "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "USERNAME", "USERDOMAIN", "PLAYWRIGHT_BROWSERS_PATH")


class TiktokVideosError(Exception):
    """The tool reported an error (its message is written for the owner)."""


# The TikTok numbers once a day (2026-10-10), so the quote writer's "what resonates" (TiktokVideos' performance.py)
# has fresh per-post views: at 14:00, between the 08:00 and 20:00 posts, when no task is running.
STATS_HOUR = 14
STATE_STATS_DAY = "tiktok_stats_day"


def stats_due(db, now=None) -> bool:
    import datetime as _dt
    now = now or _dt.datetime.now().astimezone()
    return now.hour >= STATS_HOUR and db.get_state(STATE_STATS_DAY, "") != now.date().isoformat()


def run_daily_stats(db, log=None) -> None:
    """Reads the TikTok numbers (they're kept per post by the tool). Once a day even if it fails: a sign-in problem
    would otherwise retry every heartbeat, and the Sunday report says when TikTok can't be read."""
    import datetime as _dt
    db.set_state(STATE_STATS_DAY, _dt.datetime.now().astimezone().date().isoformat())
    try:
        run(["stats", "--days", "7"], timeout=300)
    except Exception as e:
        if log:
            log.warning(f"[tiktok] daily stats read failed: {e}")


def run(args: List[str], on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_start: Optional[Callable[[int], None]] = None, timeout: float = 1800) -> Dict[str, Any]:
    """
    Runs `tiktokvideos.py <args>`, passing progress events to on_progress and the process id to on_start
    (so a cancel can end it). Returns the final "done" event; raises TiktokVideosError with the tool's own
    message on an "error" event, or when it ends without a result (e.g. it was stopped).
    """
    script = TIKTOK_VIDEOS_DIR / "tiktokvideos.py"
    if not script.is_file():
        raise TiktokVideosError(f"TiktokVideos isn't installed at {TIKTOK_VIDEOS_DIR}")
    # Its own .env holds the keys it needs; the agent's secrets are not passed on. Playwright finds
    # Microsoft Edge through the Program Files variables, so those are.
    env = get_safe_subprocess_env({k: os.environ[k] for k in BROWSER_ENV if k in os.environ})
    env["PYTHONIOENCODING"] = "utf-8"
    # How many finished videos the tool keeps (it prunes after every render and on cleanup). Before 2026-10-07
    # nothing passed this on, so the tool's default of 3 applied whatever the agent's .env said.
    env["MAX_SAVED_VIDEOS"] = str(VIDEO_KEEP_COUNT)
    proc = subprocess.Popen([sys.executable, str(script), *args], cwd=str(TIKTOK_VIDEOS_DIR), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if on_start:
        on_start(proc.pid)
    final: Optional[Dict[str, Any]] = None
    try:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.pop("event", "")
            if kind == "progress":
                if on_progress:
                    try:
                        on_progress(event)
                    except Exception as e:           # progress is cosmetic
                        logger.debug(f"progress callback failed: {e}")
            elif kind in ("done", "error"):
                final = {"event": kind, **event}
        proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
    if final is None:
        raise TiktokVideosError(f"TiktokVideos stopped without a result (exit code {proc.returncode})")
    if final["event"] == "error":
        raise TiktokVideosError(final.get("message") or "TiktokVideos failed")
    final.pop("event")
    return final


def list_videos(limit: int = 10) -> List[Dict[str, Any]]:
    return run(["list", "--limit", str(limit)], timeout=60).get("videos", [])


def cleanup(older_than: float = 0, keep: Optional[int] = None) -> Dict[str, Any]:
    """Temp files older than `older_than` seconds, and videos beyond the newest `keep` (VIDEO_KEEP_COUNT)."""
    args = ["cleanup", "--older-than", str(older_than), "--keep", str(keep if keep is not None else VIDEO_KEEP_COUNT)]
    return run(args, timeout=120)
