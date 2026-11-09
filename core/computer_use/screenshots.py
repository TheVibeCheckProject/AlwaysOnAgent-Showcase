"""Run screenshots for AgentComputerUse (core/computer_use/screenshots.py, moved from core/computer_use_screenshots.py §6 on 2026-11-04).

Where each run's screenshots are, for the chat's computer_screenshots and the app.
RUN_ID and SHOT_NAME live here and are re-exported by core.computer_use.
COMPUTER_USE_DIR is resolved from core.computer_use at call time via _cu(), so tests
patching it on that module take effect.
"""

import json
import re
from pathlib import Path
from typing import Any


def _cu():
    """core.computer_use, resolved at call time so test patches on it take effect."""
    import core.computer_use as _m
    return _m




# ------------------------------------------------------------------------------------------- run screenshots
# Every run keeps a screenshot per step (AgentComputerUse steplog.py: data/runs/<run id>/step-NN.jpg, snap-NNN.jpg).
# 2026-10-27, Alex: "I thought the assistant can view the stuff on the mini so it should be able to show the
# images". It couldn't: its file tools see only the agent's own code, so asked for task-233's screenshots it named
# folders that didn't exist. The chat tool computer_screenshots finds them; GET /api/computer/runs/... serves them.
# (Not screenshot_path: that name is the Needs-you request's screenshot, above.)
RUN_ID = re.compile(r"[0-9]{8}-[0-9]{6}")
SHOT_NAME = re.compile(r"(step-[0-9]{2,3}|snap-[0-9]{3})\.jpg")


def run_screenshots(run_id: str) -> list[str]:
    """The run's screenshot file names in step order ([] for an unknown or odd id)."""
    if not RUN_ID.fullmatch(run_id or ""):
        return []
    folder = _cu().COMPUTER_USE_DIR / "data" / "runs" / run_id
    if not folder.is_dir():
        return []
    names = [p.name for p in folder.iterdir() if SHOT_NAME.fullmatch(p.name)]
    def _sort_key(n: str) -> tuple[int, str]:
        m = re.search(r"\d+", n)
        val = int(m.group()) if m else 0
        return (val, n)
    return sorted(names, key=_sort_key)


def run_screenshot_path(run_id: str, name: str) -> Path | None:
    if not RUN_ID.fullmatch(run_id or "") or not SHOT_NAME.fullmatch(name or ""):
        return None
    p = _cu().COMPUTER_USE_DIR / "data" / "runs" / run_id / name
    return p if p.is_file() else None


def run_for_task(task: dict[str, Any] | None) -> str | None:
    """The run id a computer task recorded (its result's "run")."""
    meta = (task or {}).get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    run_id = str(((meta.get("result") or {}) if isinstance(meta, dict) else {}).get("run") or "")
    return run_id if RUN_ID.fullmatch(run_id) else None


def latest_runs(limit: int = 5) -> list[str]:
    folder = _cu().COMPUTER_USE_DIR / "data" / "runs"
    if not folder.is_dir():
        return []
    return sorted((p.name for p in folder.iterdir() if p.is_dir() and RUN_ID.fullmatch(p.name)), reverse=True)[:limit]


def _status_words(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status == "done":
        quotes = "; ".join(f'"{q}"' for q in (result.get("evidence") or [])[:3])
        # A retask: the same task done cleanly before, its steps repeated without the AI (the tool's retasks.py)
        n = result.get("requests") or 0
        repeat = (f"\n(Repeated an earlier run: {n} AI request{'' if n == 1 else 's'}.)"
                  if (result.get("detail") or {}).get("retask") else "")
        return f"{result.get('answer')}" + (f"\nFrom the page: {quotes}" if quotes else "") + repeat
    if status == "needs_approval":
        d = result.get("detail") or {}
        return (f"Stopped before {result.get('question') or 'a step that needs your OK'}"
                + (f" ({d.get('item')})" if d.get("item") else "") + ". Nothing was submitted.")
    if status == "needs_owner":
        return f"Needs you: {result.get('question') or 'it got stuck'}"
    if status == "stopped":
        return f"Stopped: {(result.get('detail') or {}).get('reason') or 'stopped'}"
    if status == "out_of_steps":
        return "Ran out of steps before finishing."
    return f"Couldn't finish: {(result.get('detail') or {}).get('reason') or status}"
