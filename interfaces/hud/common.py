"""Helpers the route modules share: task formatting, request de-duplication, suggestions, restart."""

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException

from core import actions, maintenance, task_thumbs
from interfaces.hud import state

IdempotencyKey = Header(default=None, alias="Idempotency-Key", max_length=200)
# Who started a task, for its details in DeskAI ("Hark", "DeskAI quick dispatch", ...). Hark sends it through the
# Mini MCP server (scripts/mini_mcp.py); any other caller may too. Added 2026-10-07.
TaskSourceHeader = Header(default=None, alias="X-Task-Source", max_length=120)

# Older tasks stored terse sources; shown in words (the stored metadata is left as it was)
_SOURCE_NAMES = {"deskai": "DeskAI", "the hud / deskai": "DeskAI", "hud": "DeskAI"}


def clean_source(*candidates: str | None, default: str = "unknown") -> str:
    """The first non-empty candidate as one short printable line (at most 80 characters), else `default`."""
    for c in candidates:
        text = " ".join("".join(ch for ch in str(c or "") if ch.isprintable()).split())[:80]
        if text:
            return text
    return default


def task_source(t: dict[str, Any]) -> str:
    """Who or what started the task, in words: metadata.source (stamped on every creation path since
    2026-10-07; schedules always stamped it), shown readably, or "unknown" for older tasks without one."""
    raw = clean_source((t.get("metadata") or {}).get("source"), default="")
    if not raw:
        return "unknown"
    return _SOURCE_NAMES.get(raw.lower(), raw)


def public_fact(f: dict[str, Any]) -> dict[str, Any]:
    """A fact for DeskAI, with where it came from (IDEAS #19): `evidence` (the owner's words backing it) and, when
    known, `source_message` {"id", "at", "excerpt"}, the message it was learned from."""
    out = {k: v for k, v in f.items() if k != "embedding"}
    msg_id = f.get("source_msg_id")
    if msg_id:
        try:
            row = state.db.get_connection().execute(
                "SELECT id, content, created_at FROM mem_messages WHERE id = ?", (int(msg_id),)).fetchone()
            if row:
                text = " ".join((row["content"] or "").split())
                out["source_message"] = {"id": row["id"], "at": row["created_at"],
                                         "excerpt": text[:160] + ("…" if len(text) > 160 else "")}
        except (OSError, sqlite3.Error, ValueError):
            pass                                       # a fact still shows without its source
    return out


def wake_daemon() -> bool:
    """New work wakes a daemon in standby. Returns True if it was asleep."""
    return actions.wake_daemon(state.db)


def asleep() -> bool:
    return state.db.get_state("daemon_status", "idle") in ("paused", "stopped", "sleep")


def format_wait(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds < 600 else f"{round(seconds / 60)} min"


def public_task(t: dict[str, Any]) -> dict[str, Any]:
    """A task as the API returns it: the stored fields plus a ready-to-show status line and its result."""
    phase = t.get("phase")
    progress = t.get("progress")
    if phase in ("backlog", "pending"):
        status_text = "Waiting to start"
    elif phase == "in_progress":
        status_text = "Running"
        if progress:
            status_text = f"{progress.get('label') or 'Working'}: {progress.get('percent', 0)}%"
            if progress.get("eta_seconds") is not None:
                status_text += f", about {format_wait(progress['eta_seconds'])} left"
    elif phase == "completed" and t.get("needs_input"):
        status_text = "Needs your input"
    elif phase == "completed":
        status_text = "Completed"
    elif t.get("cancelled"):
        status_text = "Cancelled"
    else:
        status_text = "Failed" + (f": {str(t.get('last_error'))[:160]}" if t.get("last_error") else "")

    result = None
    meta = t.get("metadata") or {}
    video = Path(str(meta.get("video_path") or "")).name
    video_kept = bool(video) and (state.VIDEOS_DIR / video).is_file()
    if video_kept:
        result = {"type": "video", "filename": video, "url": f"/videos/{video}"}
    # Task details (2026-10-07): a saved thumbnail outlives the video's pruning; one can still be made while
    # the video is kept (GET /api/tasks/{id}/thumb makes it on first ask)
    has_thumb = bool(t.get("id")) and (task_thumbs.find(t) is not None or video_kept)

    # Generate dynamic action chips for DeskAI
    try:
        from interfaces.hud.action_chips import build_task_action_chips
        actions_list = build_task_action_chips(t, result)
    except (ImportError, AttributeError, TypeError):
        actions_list = []

    return {**t, "status_text": status_text, "result": result, "actions": actions_list,
            "source": task_source(t),
            "thumb_url": f"/api/tasks/{t['id']}/thumb" if has_thumb else None,
            # The same stream the Video Hub plays (GET /videos/{name}); null once the video has been pruned
            "video_filename": video or None,
            "video_url": f"/videos/{video}" if video_kept else None,
            "video_kept": video_kept if video else None,
            # A computer task's steps in plain sentences, with errors (core/daemon/special.py _execute_computer)
            "activity_log": meta.get("activity_log") or None}


def task_for_key(key: str | None) -> str | None:
    """The task an earlier request with this Idempotency-Key created, so a retried request isn't queued twice."""
    key = (key or "").strip()
    if not key:
        return None
    task_id = state.db.task_for_request_key(key)
    return task_id if task_id and state.db.get_task(task_id) else None


def remember_key(key: str | None, task_id: str) -> None:
    if key and key.strip():
        state.db.remember_request_key(key.strip(), task_id)


def determine_agent_status() -> str:
    """Computes high-level status for DeskAI: 'idle', 'working', 'waiting_for_approval', or 'error'."""
    if state.db.get_state("daemon_status", "idle") == "error":
        return "error"
    if state.db.list_tasks(limit=1, phase="in_progress"):
        return "working"
    try:
        if state.db.get_active_blockers():
            return "waiting_for_approval"
    except (OSError, sqlite3.Error):
        pass
    return "idle"


def schedule_restart(reason: str, now: bool = False) -> dict[str, Any]:
    """Exits with code 3 once the running task has finished (at once with now=True), holding new work meanwhile;
    the launcher (run_forever.ps1) relaunches on 3. See core/maintenance.py."""
    return maintenance.restart_when_idle(state.db, f"[WebHUD] {reason}", now=now)


# ------------------------------------------------------------------ suggestions (propose_task in chat)
PROPOSAL_TTL = 24 * 3600          # a suggestion can be run or dismissed for a day (or until a restart)
MAX_PROPOSALS = 50
_proposals: dict[str, dict[str, Any]] = {}
_proposals_lock = threading.Lock()


def store_proposals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gives each propose_task suggestion an id the app can run or dismiss by."""
    out = []
    now = time.time()
    with _proposals_lock:
        for p in items:
            pid = uuid.uuid4().hex[:10]
            entry = {"instruction": p["instruction"], "reason": p.get("reason") or "", "project": p.get("project"),
                     "kind": p.get("kind", "task")}
            _proposals[pid] = {**entry, "add": p.get("add"), "created": now}
            # Texts and calls: the app opens Messages or the dialer itself, so it needs the number and message
            phone = {"phone": p.get("add")} if p.get("kind") in ("text", "call") else {}
            out.append({"id": pid, **entry, **phone})
        for pid in [k for k, v in _proposals.items() if now - v["created"] > PROPOSAL_TTL]:
            del _proposals[pid]
        while len(_proposals) > MAX_PROPOSALS:
            del _proposals[next(iter(_proposals))]
    return out


def take_proposal(proposal_id: str) -> dict[str, Any]:
    with _proposals_lock:
        p = _proposals.pop(proposal_id, None)
    if p is None or time.time() - p["created"] > PROPOSAL_TTL:
        raise HTTPException(status_code=404, detail="This suggestion expired (the agent restarted or it was already handled)")
    return p


def queue_instruction(prompt: str, project: str | None = None, how: str = "from DeskAI",
                      source: str = "DeskAI") -> str:
    """Queues an approved instruction (a suggestion's Run button): the default engine, or an AgentWork job."""
    task_id = actions.queue_instruction(state.db, prompt, project, source=source)
    try:
        state.get_memory().record_event(f"The user queued background task {task_id} {how}: {prompt}")
    except Exception as e:
        state.logger.warning(f"Could not record task in memory: {e}")
    return task_id


def add_agentwork_project(name: str, repo: str, description: str) -> dict[str, Any]:
    try:
        project = state.get_agentwork().add_project(name, repo, description)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Couldn't add the project: {e}")
    state.db.append_log("INFO", f"[WebHUD] Added AgentWork project '{project['name']}' ({project['repo']})")
    try:
        state.get_memory().record_event(f"The user added the AgentWork project '{project['name']}': {project['repo']}")
    except Exception as e:
        state.logger.warning(f"Could not record the new project in memory: {e}")
    return project
