"""Running AgentComputerUse (core/computer_use/run.py, moved from core/computer_use_run.py §5 on 2026-11-04).

run() starts agentcomputeruse.py (the goal on stdin, never on the command line) and
reports its progress; describe() puts the result in words for the task card and the chat.

COMPUTER_USE_DIR, DESKTOP_ENV and ComputerUseError are resolved from core.computer_use
at call time via _cu(), so tests patching them on that module take effect.
"""

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from .screenshots import _status_words, run_screenshots

try:
    from core.config import get_safe_subprocess_env
except ImportError:
    from config import get_safe_subprocess_env  # type: ignore[no-redef]

logger = logging.getLogger("ComputerUse")

def _cu():
    """core.computer_use, resolved at call time so test patches on it take effect."""
    import core.computer_use as _m
    return _m


def _drain_events(proc, on_progress: Callable[[dict[str, Any]], None] | None,
                  on_needs_you: Callable[[dict[str, Any]], None] | None) -> dict[str, Any] | None:
    """Read the tool's stdout lines until a done/error event; returns it, or None if the stream ended first.
    Progress and needs_you events go to their callbacks as they arrive."""
    final: dict[str, Any] | None = None
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
        elif kind == "needs_you":
            if on_needs_you:
                try:
                    on_needs_you(event)
                except Exception as e:
                    logger.warning(f"needs_you callback failed: {e}")
        elif kind in ("done", "error"):
            final = {"event": kind, **event}
    return final


def run(args: list[str], on_progress: Callable[[dict[str, Any]], None] | None = None,
        on_start: Callable[[int], None] | None = None, timeout: float = 1200,
        on_needs_you: Callable[[dict[str, Any]], None] | None = None,
        input_text: str | None = None) -> dict[str, Any]:
    """Runs `agentcomputeruse.py <args>`; progress events go to on_progress, the process id to on_start (a cancel
    ends it). Returns the final "done" event; raises ComputerUseError on an "error" event or no result."""
    import os
    script = _cu().COMPUTER_USE_DIR / "agentcomputeruse.py"
    if not script.is_file():
        raise _cu().ComputerUseError(f"AgentComputerUse isn't installed at {_cu().COMPUTER_USE_DIR}")
    env = get_safe_subprocess_env({k: os.environ[k] for k in _cu().DESKTOP_ENV if k in os.environ})
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, "-u", str(script), *args], cwd=str(_cu().COMPUTER_USE_DIR), env=env,
                            stdin=subprocess.PIPE if input_text is not None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if input_text is not None and proc.stdin:
        try:
            proc.stdin.write(input_text)
            proc.stdin.flush()
            proc.stdin.close()
        except (OSError, ValueError):
            pass
    if on_start:
        on_start(proc.pid)
    final: dict[str, Any] | None = None
    try:
        final = _drain_events(proc, on_progress, on_needs_you)
        proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
    if final is None:
        raise _cu().ComputerUseError(f"AgentComputerUse stopped without a result (exit code {proc.returncode})")
    if final["event"] == "error":
        raise _cu().ComputerUseError(final.get("message") or "AgentComputerUse failed")
    final.pop("event")
    return final


def describe(result: dict[str, Any]) -> str:
    """The result in words for the task card and the chat, with where its step screenshots are. 2026-10-26: the
    run id wasn't in the result, so asked where task-233's screenshots went, the chat guessed a folder."""
    text = _status_words(result)
    run_id = str(result.get("run") or "")
    shots = run_screenshots(run_id)
    if shots:
        text += f"\nScreenshots: {len(shots)}, in {_cu().COMPUTER_USE_DIR / 'data' / 'runs' / run_id}"
    return text
