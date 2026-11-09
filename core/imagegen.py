"""
Calling ImageGen
================
ImageGen (IMAGEGEN_DIR, a separate tool) makes an image from the owner's request with free image services.
The agent runs it as its own program and reads the JSON line it prints: the result names the saved image.
"""

import json
import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.config import IMAGEGEN_DIR, get_safe_subprocess_env

logger = logging.getLogger("ImageGen")


class ImageGenError(Exception):
    """The tool reported an error (its message is written for the owner). .result has its other fields."""

    def __init__(self, message: str, result: dict[str, Any] | None = None):
        super().__init__(message)
        self.result = result or {}


def run(args: list[str], timeout: float = 240,
        on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Runs `imagegen.py <args>` and returns its "done" event; raises ImageGenError on an "error" event.
    Its "progress" events ("AI Horde: 3rd in line") go to on_progress as they come."""
    script = IMAGEGEN_DIR / "imagegen.py"
    if not script.is_file():
        raise ImageGenError(f"ImageGen isn't installed at {IMAGEGEN_DIR}")
    # Its own .env holds the keys it needs; the agent's secrets are not passed on
    env = get_safe_subprocess_env()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, str(script), *args], cwd=str(IMAGEGEN_DIR), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    timed_out = threading.Event()

    def stop():
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, stop)
    timer.daemon = True
    timer.start()
    final: dict[str, Any] = {}
    try:
        if proc.stdout is None:  # unreachable with stdout=PIPE above; without it there is nothing to read
            raise ImageGenError("ImageGen produced no output")
        # Read as it prints (not at the end): progress is shown while the image is made
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") in ("done", "error"):
                final = event
            elif event.get("event") == "progress" and on_progress and event.get("stage"):
                try:
                    on_progress(str(event["stage"])[:120])
                except Exception:
                    logger.debug("progress callback failed", exc_info=True)
        proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set() and not final:
        raise ImageGenError(f"ImageGen didn't finish within {timeout:.0f}s")
    if not final:
        raise ImageGenError(f"ImageGen stopped without a result (exit code {proc.returncode})")
    kind = final.pop("event")
    if kind == "error":
        raise ImageGenError(final.get("message") or "ImageGen failed", final)
    return final


def generate(request: str, out_dir: Path, on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Makes an image for what the owner typed (flags included) and saves it in out_dir."""
    return run(["generate", "--request", request, "--out-dir", str(out_dir)], on_progress=on_progress)


def help_text() -> str:
    return run(["help"], timeout=60).get("text", "")
