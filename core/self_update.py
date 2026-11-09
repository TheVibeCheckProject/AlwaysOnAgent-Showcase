"""
Going live: an AgentWork change put live safely, when the owner taps the chip
=============================================================================
An AgentWork job leaves its change in AgentWork's copy, on a branch. Two kinds can go live, each with its own
program (it runs on its own, so it outlives an agent restart):
- "alwaysonagent" (the agent itself), chip "⚡ Apply update": scripts/apply_update.py. Tests on the job's copy,
  merge, tests again, restart, a health check, then automatic rollback or a push to GitHub.
- "website", chip "🚀 Push live": scripts/apply_website.py. Brought up to date with the live main, `npm run build`
  and `npm test`, then a push to main, which is what deploys example-site.com.
The owner's tap on the chip (POST /api/tasks/{id}/apply) is the go-ahead. The outcome is in
data/updates/<job>.json and on the owner's phone.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from core.config import AGENT_WORK_DIR
    from core.database import NEEDS_INPUT_EXIT_CODE
except ImportError:
    from config import AGENT_WORK_DIR  # type: ignore[no-redef]
    from database import NEEDS_INPUT_EXIT_CODE  # type: ignore[no-redef]

LIVE = Path(__file__).resolve().parent.parent
UPDATES = LIVE / "data" / "updates"
SELF_PROJECT = "alwaysonagent"          # AgentWork's project for this repo
# The projects that can go live, and the program that does it
GO_LIVE = {"alwaysonagent": "apply_update.py", "website": "apply_website.py"}
FINAL = ("applied", "rolled_back", "rollback_failed")


class UpdateRefused(Exception):
    """Why an update can't start; written for the owner."""


def workspace(job: str) -> Path:
    return Path(AGENT_WORK_DIR) / "workspaces" / job


def status(job: str) -> dict[str, Any] | None:
    try:
        return json.loads((UPDATES / f"{job}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def running() -> bool:
    return (UPDATES / "lock").exists()


def check(task: dict[str, Any] | None) -> Path:
    """The job's copy if this task's change can be applied; raises UpdateRefused otherwise."""
    if not task:
        raise UpdateRefused("there's no such task")
    job = task["id"]
    if (task.get("metadata") or {}).get("project") not in GO_LIVE:
        raise UpdateRefused(f"{job} can't go live from here (only {', '.join(GO_LIVE)} jobs can)")
    if task.get("phase") != "completed":
        raise UpdateRefused(f"{job} hasn't finished successfully (it's '{task.get('phase')}')")
    if task.get("exit_code") == NEEDS_INPUT_EXIT_CODE:
        raise UpdateRefused(f"{job} changed nothing, so there's nothing to apply")
    done = status(job)
    if done and done.get("status") in FINAL:
        raise UpdateRefused(f"{job} was already applied once (outcome: {done['status']})")
    if running():
        raise UpdateRefused("another update is being applied right now")
    folder = workspace(job)
    if not folder.is_dir():
        raise UpdateRefused(f"AgentWork's copy of {job} is gone (it may have been discarded)")
    return folder


def start(task: dict[str, Any] | None) -> None:
    """Launches the project's go-live program for this task, detached. Raises UpdateRefused if it can't go live."""
    folder = check(task)
    assert task is not None  # check() raises UpdateRefused for a missing task, so it cannot be None here
    UPDATES.mkdir(parents=True, exist_ok=True)
    project = (task.get("metadata") or {}).get("project")
    assert isinstance(project, str)  # check() only returns when the project is one of GO_LIVE
    title = str(task.get("title") or "an AgentWork change").removeprefix(f"[{project}] ")
    # Shown as "going live" on the card at once; the program then writes its own progress here
    (UPDATES / f"{task['id']}.json").write_text(json.dumps({"job": task["id"], "status": "starting",
                                                            "project": project}), encoding="utf-8")
    cmd = [sys.executable, str(LIVE / "scripts" / GO_LIVE[project]), "--job", task["id"],
           "--workspace", str(folder), "--title", title[:120]]
    log = open(UPDATES / f"{task['id']}.log", "w", encoding="utf-8")  # noqa: SIM115  (the detached child writes to it)
    flags = 0
    if os.name == "nt":
        # Windows-only flags: getattr keeps this importable on Linux (mypy: subprocess has no such attributes here)
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        # Out of the agent's job object too, when Windows allows it, so the restart can't take it down
        subprocess.Popen(cmd, cwd=str(LIVE), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), close_fds=True,
                         env=env)
    except OSError:
        subprocess.Popen(cmd, cwd=str(LIVE), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True, env=env)
    finally:
        log.close()


def _git(folder: Path, args: list[str], timeout: int) -> str:
    """Runs one git command in the workspace and returns its stdout, stripped."""
    res = subprocess.run(
        ["git", *args],
        cwd=str(folder),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return res.stdout.strip()


def _collect_diff(folder: Path) -> str:
    """The workspace's changes: unstaged/staged against HEAD, else branch commits against main."""
    diff_text = _git(folder, ["diff", "HEAD"], timeout=15)
    if not diff_text:
        diff_text = _git(folder, ["diff", "main...HEAD"], timeout=15)
    return diff_text


def _untracked_summary(folder: Path) -> str:
    """The untracked-files message, or the no-changes message when there is nothing."""
    untracked = _git(folder, ["status", "--porcelain"], timeout=10)
    return f"No modified lines. Untracked files:\n{untracked}" if untracked else "No changes detected in workspace."


def get_task_diff(job: str, max_lines: int = 100) -> str:
    """
    Returns the git diff of pending code changes in an AgentWork workspace.
    Checks unstaged/staged changes against HEAD, followed by branch commits against main.
    """
    job = (job or "").strip()
    if not job:
        return "Error: No job ID provided."

    folder = workspace(job)
    if not folder.is_dir():
        return f"Error: AgentWork copy for {job} is not available at {folder}."

    max_lines = max(10, min(int(max_lines or 100), 500))

    try:
        diff_text = _collect_diff(folder)
        if not diff_text:
            return _untracked_summary(folder)
        lines = diff_text.splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n\n... [truncated, {len(lines) - max_lines} more lines]"
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading git diff for {job}: {e}"
