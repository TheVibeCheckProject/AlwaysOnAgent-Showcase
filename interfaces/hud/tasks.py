"""Tasks: the queue, cancel / retry, video jobs, the daemon's state, the live stream and the log."""

import asyncio
import datetime
import json
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from core import actions, task_thumbs
from core.daemon import kill_process_tree
from core.task_engines import DEFAULT_ENGINE_CHOICES, ENGINE_ALIASES, normalize_engine
from interfaces.hud import common, state
from interfaces.hud.models import (
    DaemonStateRequest,
    EngineChangeRequest,
    MediaTriggerRequest,
    TaskCancelRequest,
    TaskCreateRequest,
    TaskRunRequest,
)

router = APIRouter()


@router.post("/api/daemon/state")
async def set_daemon_state(req: DaemonStateRequest):
    """Sets the operational state of the daemon ('idle'/'running' vs 'paused'/'sleep')."""
    target = req.status.lower().strip()
    if target in ("idle", "running", "wake", "boot", "ignite"):
        state.db.set_state("daemon_status", "idle")
        state.db.append_log("INFO", "[WebHUD] Daemon awakened / ignited via Web HUD control.")
        return {"status": "success", "daemon_status": "idle", "message": "Daemon awakened"}
    elif target in ("paused", "sleep", "stop", "standby"):
        state.db.set_state("daemon_status", "paused")
        state.db.append_log("INFO", "[WebHUD] Daemon placed in standby mode via Web HUD control.")
        return {"status": "success", "daemon_status": "paused", "message": "Daemon placed in standby"}
    else:
        raise HTTPException(status_code=400, detail=f"Invalid daemon state: {req.status}")


@router.post("/api/engine")
async def set_default_engine(req: EngineChangeRequest):
    """Updates the global default execution engine."""
    requested = req.engine.lower().strip()
    if requested not in DEFAULT_ENGINE_CHOICES and ENGINE_ALIASES.get(requested) not in DEFAULT_ENGINE_CHOICES:
        raise HTTPException(status_code=400, detail=f"Unsupported engine: {req.engine}")
    target = normalize_engine(requested)
    state.db.set_state("default_engine", target)
    state.db.append_log("INFO", f"[WebHUD] Default engine updated to '{target}' via Web HUD.")
    return {"status": "success", "default_engine": target}


@router.get("/api/tasks")
async def list_tasks_endpoint(limit: int = 50, phase: str | None = None):
    """Returns a list of pending, running, and recent background tasks."""
    return [common.public_task(t) for t in state.db.list_tasks(limit=limit, phase=phase)]


@router.get("/api/tasks/{task_id}")
async def get_task_endpoint(task_id: str):
    """One task, for a live task card in DeskAI."""
    task = state.db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return common.public_task(task)


@router.get("/api/tasks/{task_id}/thumb")
def task_thumb(task_id: str):
    """The task's small video thumbnail (core/task_thumbs.py): kept after the video itself is pruned, made on the
    first ask while the video is still there. 404 when the task has none."""
    task = state.db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    path = task_thumbs.ensure(state.db, task, state.VIDEOS_DIR)
    if path is None:
        raise HTTPException(status_code=404, detail="No thumbnail for this task")
    return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/api/tasks")
async def create_task(req: TaskCreateRequest, idempotency_key: str | None = common.IdempotencyKey,
                      task_source: str | None = common.TaskSourceHeader):
    """Enqueues a new autonomous task into the database."""
    existing = common.task_for_key(idempotency_key)
    if existing:
        return {"status": "success", "task_id": existing, "woke_daemon": False, "duplicate": True,
                "message": "Task already created by an earlier try of this request"}
    try:
        task_id = state.db.create_task(
            title=req.title,
            prompt=req.prompt,
            priority=req.priority,
            engine=normalize_engine(req.engine),
            mode=req.mode,
            timeout_seconds=req.timeout_seconds,
            # DeskAI's Quick-Dispatch is the usual caller; Hark (via the Mini MCP server) says so in a header
            metadata={"source": common.clean_source(req.source, task_source, default="DeskAI quick dispatch")}
        )
        common.remember_key(idempotency_key, task_id)
        state.db.append_log("INFO", f"[WebHUD] Enqueued new task [{task_id}]: '{req.title}' (engine: {req.engine}, priority: {req.priority})")
        woke = common.wake_daemon()
        try:
            state.get_memory().record_event(f"Task {task_id} [{req.engine}] \"{req.title}\" was enqueued.")
        except (OSError, sqlite3.Error, AttributeError):
            pass
        return {"status": "success", "task_id": task_id, "woke_daemon": woke, "message": "Task created successfully"}
    except Exception as e:
        state.logger.exception(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tasks/run")
async def run_task_endpoint(req: TaskRunRequest, idempotency_key: str | None = common.IdempotencyKey,
                            task_source: str | None = common.TaskSourceHeader):
    """Triggers or runs an agent task approved by the user on mobile."""
    existing = common.task_for_key(idempotency_key)
    if existing:
        return {"status": "success", "task_id": existing, "duplicate": True,
                "message": "Task already started by an earlier try of this request"}
    if req.task_id:
        task = state.db.get_task(req.task_id)
        if not task:
            if req.command or req.prompt:
                cmd = req.command or req.prompt
                assert cmd is not None  # guarded by the `if req.command or req.prompt` above
                tid = state.db.create_task(title=req.title or actions.task_title(cmd), prompt=cmd, engine=normalize_engine(req.engine),
                                           metadata={"source": common.clean_source(task_source, default="DeskAI")})
                common.remember_key(idempotency_key, tid)
                common.wake_daemon()
                return {"status": "success", "task_id": tid, "message": "Task created and triggered successfully"}
            raise HTTPException(status_code=404, detail="Task not found")
        if task.get("phase") in ("failed", "cancelled"):
            state.db.retry_task(req.task_id)
        common.wake_daemon()
        return {"status": "success", "task_id": req.task_id, "message": "Task triggered successfully"}

    cmd = req.command or req.prompt
    if not cmd:
        raise HTTPException(status_code=400, detail="Either task_id or command must be provided")
    tid = state.db.create_task(title=req.title or actions.task_title(cmd), prompt=cmd, engine=normalize_engine(req.engine),
                               metadata={"source": common.clean_source(task_source, default="DeskAI")})
    common.remember_key(idempotency_key, tid)
    common.wake_daemon()
    try:
        state.get_memory().record_event(f"Task {tid} \"{req.title or cmd[:60]}\" was triggered.")
    except (OSError, sqlite3.Error, AttributeError):
        pass
    return {"status": "success", "task_id": tid, "message": "Task created and triggered successfully"}


@router.post("/api/tasks/cancel")
async def cancel_task_json_endpoint(req: TaskCancelRequest | None = None):
    """Halts an executing background task."""
    target_id = req.task_id if req and req.task_id else None
    if not target_id:
        in_prog = state.db.list_tasks(limit=1, phase="in_progress")
        if not in_prog:
            return {"status": "idle", "message": "No active task to cancel"}
        target_id = in_prog[0]["id"]
    return await cancel_task(target_id)


@router.post("/api/cancel/{task_id}")
async def cancel_task(task_id: str):
    """Cancels a task. If active, kills its process tree immediately."""
    task = state.db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    worker_pid = task.get("worker_pid")
    if worker_pid and task.get("phase") == "in_progress":
        state.db.append_log("WARNING", f"[WebHUD] Terminating process tree PID {worker_pid} for task [{task_id}]...")
        kill_process_tree(worker_pid, logger=state.logger)

    if not state.db.cancel_task(task_id, "Cancelled manually via Mission Control Web HUD"):
        raise HTTPException(status_code=409, detail=f"Task already finished ({task.get('phase')}); nothing to cancel")
    state.db.end_busy_status()
    try:
        from core import computer_use
        computer_use.cancel_for_task(task_id, reason="Cancelled manually via Mission Control Web HUD")
    except Exception as e:
        state.logger.warning(f"[WebHUD] Failed to cancel computer use requests: {e}")
    state.db.append_log("INFO", f"[WebHUD] Task [{task_id}] cancelled.")
    return {"status": "success", "task_id": task_id, "message": "Task cancelled successfully"}


@router.post("/api/tasks/{task_id}/apply", status_code=202)
def apply_task(task_id: str):
    """Puts a finished AgentWork change live: the agent itself ("⚡ Apply update") or the website ("🚀 Push live").
    The owner's tap on the chip is the go-ahead; the outcome comes as a push notification (core/self_update.py)."""
    from core import self_update
    task = state.db.get_task(task_id)
    try:
        self_update.start(task)
    except self_update.UpdateRefused as e:
        raise HTTPException(status_code=409, detail=str(e))
    assert task is not None  # start() raises UpdateRefused for a missing task, so it cannot be None here
    project = (task.get("metadata") or {}).get("project")
    state.db.append_log("INFO", f"[WebHUD] Going live with [{task_id}] ({project}).")
    return {"status": "applying", "task_id": task_id, "project": project,
            "message": ("Building and testing the site, then pushing it live. You'll get a notification."
                        if project == "website" else
                        "Testing, applying and restarting, with an automatic rollback if anything's wrong. "
                        "You'll get a notification.")}


@router.post("/api/retry/{task_id}")
async def retry_task(task_id: str):
    """Resets a failed task back to backlog for retry."""
    task = state.db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("phase") == "in_progress":
        raise HTTPException(status_code=409, detail="Task is still running; cancel it first, then retry")
    success = state.db.retry_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot retry task in current state")

    state.db.append_log("INFO", f"[WebHUD] Task [{task_id}] requeued to backlog for retry.")
    return {"status": "success", "task_id": task_id, "message": "Task requeued to backlog"}


@router.post("/api/trigger_media")
async def trigger_media_pipeline(req: MediaTriggerRequest | None = None,
                                 idempotency_key: str | None = common.IdempotencyKey,
                                 task_source: str | None = common.TaskSourceHeader):
    """Dispatches a video render / TikTok upload task (scene: 'chest', 'bottle', 'window' or 'card'; omitted, the
    scenes take turns)."""
    existing = common.task_for_key(idempotency_key)
    if existing:
        return {"status": "success", "task_id": existing, "woke_daemon": False, "duplicate": True,
                "message": "Already dispatched by an earlier try of this request"}
    quote = (req.quote.strip() if req and req.quote and req.quote.strip() else None)
    action = (req.action.lower().strip() if req and req.action else "video")
    is_tiktok = (action == "tiktok")
    scene = None
    if req and req.scene and req.scene.strip():
        scene = actions.video_scene(req.scene)
        if not scene:
            raise HTTPException(status_code=400, detail=f"Unknown scene '{req.scene}': use chest, bottle, window or card")
    woke = common.asleep()
    task_id = actions.queue_video(state.db, quote, post_to_tiktok=is_tiktok, scene=scene,
                                  source=common.clean_source(task_source, default="DeskAI Video Hub"))
    common.remember_key(idempotency_key, task_id)
    return {"status": "success", "task_id": task_id, "woke_daemon": woke, "message": f"{'TikTok upload' if is_tiktok else 'Video render'} task dispatched"}


def _sse_initial_logs() -> tuple[list, int | None]:
    """The first batch of recent logs, and the id new logs are measured from."""
    initial_logs = state.db.get_recent_logs(limit=40)
    last_log_id = initial_logs[-1]["id"] if initial_logs else None
    return initial_logs, last_log_id


def _sse_snapshot(last_log_id: int | None, initial_logs: list) -> tuple[dict, int | None]:
    """One tick's worth of state: stats, tasks, blockers, and any logs since the last tick."""
    stats = state.db.get_stats()
    active_task = None
    in_prog = state.db.list_tasks(limit=1, phase="in_progress")
    if in_prog:
        active_task = common.public_task(in_prog[0])

    tasks = [common.public_task(t) for t in state.db.list_tasks(limit=30)]
    blockers = state.db.get_active_blockers()

    if last_log_id is not None:
        new_logs = state.db.get_recent_logs(limit=50, since_id=last_log_id)
        if new_logs:
            last_log_id = new_logs[-1]["id"]
    else:
        new_logs = initial_logs

    payload = {
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
        "stats": stats,
        "active_task": active_task,
        "tasks": tasks,
        "blockers": blockers,
        "new_logs": new_logs,
        "daemon_status": state.db.get_state("daemon_status", "idle"),
        "overall_phase": state.db.get_state("overall_phase", "idle"),
        "last_heartbeat": state.db.get_state("last_heartbeat", ""),
        "default_engine": state.db.get_state("default_engine", "auto")
    }
    return payload, last_log_id


async def _sse_event_generator(request: Request):
    """Yields one snapshot a second until the client goes away; cleans up either way."""
    initial_logs, last_log_id = _sse_initial_logs()
    state.logger.debug("Client connected to Mission Control SSE stream.")
    try:
        while True:
            # 1. Graceful disconnection check
            if await request.is_disconnected():
                state.logger.debug("Client disconnected from SSE stream (request.is_disconnected).")
                break

            # 2. Gather snapshot state from SQLite WAL, plus new logs since last seen id
            payload, last_log_id = _sse_snapshot(last_log_id, initial_logs)
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1.0)

    except (asyncio.CancelledError, GeneratorExit):
        state.logger.debug("SSE client connection closed gracefully (CancelledError / GeneratorExit).")
    except Exception as e:
        state.logger.error(f"Error in SSE event generator: {e}")
    finally:
        state.logger.debug("SSE stream cleaned up.")


@router.get("/api/stream")
async def sse_stream(request: Request):
    """
    Real-time Server-Sent Events (SSE) state streaming.
    Checks `request.is_disconnected()` every 1s and catches `asyncio.CancelledError`
    to guarantee zero connection or generator leaks on tab close.
    Streams logs from SQLite `system_logs` to avoid filesystem locks on worker.log.
    """
    return StreamingResponse(
        _sse_event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/api/logs")
def recent_logs(limit: int = Query(100, ge=1, le=500)):
    """Recent agent log lines, newest last."""
    return state.db.get_recent_logs(limit=limit)
