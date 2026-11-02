"""
Stopping processes and everything they started (task engines, the sandbox, the watchdog).
"""

import logging
from typing import Optional

import psutil

# Headless browsers and FFmpeg ignore polite termination; they are killed outright
HEADLESS_BROWSER_NAMES = {"chrome", "chromium", "msedge", "ffmpeg", "playwright"}


def kill_process_tree(pid: int, timeout: float = 2.0, logger: Optional[logging.Logger] = None) -> None:
    """Terminates a process and all its descendants, force-killing browsers, FFmpeg and anything that lingers."""
    log = logger or logging.getLogger("ProcessTree")
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        log.info(f"Terminating process tree for PID {pid} ({len(children)} child processes)...")
        all_procs = children + [parent]

        for p in all_procs:
            try:
                name = p.name().lower()
                if any(b in name for b in HEADLESS_BROWSER_NAMES):
                    log.info(f"Force-killing browser/media worker PID {p.pid} ({name})...")
                    p.kill()
                else:
                    p.terminate()
            except psutil.NoSuchProcess:
                pass

        _, alive = psutil.wait_procs(all_procs, timeout=timeout)
        for p in alive:
            try:
                log.warning(f"Force killing stubborn process PID {p.pid} ({p.name()})...")
                p.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    except Exception as e:
        log.error(f"Error killing process tree for PID {pid}: {e}")
