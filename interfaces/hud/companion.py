"""DeskAI companion: health and status, suggested tasks, AgentWork, push, and maintenance."""

import time

import psutil
from fastapi import APIRouter, HTTPException

from core import actions, backup, connectors, push, tiktok_videos
from core.housekeeping import LOG_MAX_AGE_DAYS, prune_old_logs
from interfaces.hud import common, state
from interfaces.hud.models import (
    AgentWorkJobRequest,
    AgentWorkProjectRequest,
    InboxFeedbackRequest,
    PushRegisterRequest,
    PushTokenRequest,
)

router = APIRouter()


@router.get("/")
async def root():
    """DeskAI's connection check (it also tells the app whether its token is accepted)."""
    return {"status": "online", "message": "AlwaysOnAgent is running"}


@router.get("/api/inbox")
def inbox(after: int = 0, limit: int = 50):
    """Messages the agent started (brief, notices, suggestions, questions) newer than message id `after`.
    The push that announces one carries no text; the app fetches it here over the owner's own connection."""
    from core.proactive import Proactive
    messages = Proactive(state.db).inbox(after=max(0, after), limit=max(1, min(limit, 100)))
    return {"messages": messages, "last_id": messages[-1]["id"] if messages else max(0, after)}


@router.post("/api/inbox/{message_id}/feedback")
def inbox_feedback(message_id: int, req: InboxFeedbackRequest):
    """Records user feedback (thumbs_up, thumbs_down, less_like_this) on a proactive message."""
    from core.proactive import Proactive
    p = Proactive(state.db)
    try:
        res = p.record_feedback(message_id=message_id, reaction=req.reaction, reason=req.reason or "")
        return {"status": "ok", **res}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/proactive/candidates")
def proactive_candidates(status: str | None = None, days: int = 7, limit: int = 50):
    """Inspects candidates from the proactive candidate pool (status: pending, sent, dropped, expired, or all)."""
    from core.candidates import CandidatePool
    pool = CandidatePool(state.db)
    pool.update_outcomes()
    pool.expire()
    if status == "pending":
        cands = pool.pending(limit=max(1, min(limit, 200)))
    else:
        cands = pool.recent(days=max(1, min(days, 90)), status=status if status != "all" else None,
                            limit=max(1, min(limit, 200)))
    return {"candidates": cands, "counts": pool.counts(days=days)}


@router.get("/api/health")
async def health_check():
    """Health check endpoint for mobile/desktop companion clients."""
    return {"status": "online", "message": "AlwaysOnAgent is running"}


@router.get("/api/status")
async def api_status():
    """Health & Telemetry endpoint for DeskAI companion failover checks."""
    try:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
    except psutil.Error:
        cpu = 0.0
        mem = 0.0

    in_progress = state.db.list_tasks(phase="in_progress")
    active_model = state.db.get_state("default_engine", "auto")

    return {
        "status": "online",
        "version": state.API_VERSION,
        "uptime_seconds": int(time.time() - state.SERVER_START_TIME),
        "cpu_percent": round(cpu, 1),
        "memory_percent": round(mem, 1),
        "active_model": active_model,
        "active_tasks_count": len(in_progress) if in_progress else 0
    }


@router.post("/api/proposals/{proposal_id}/run")
def run_proposal(proposal_id: str):
    p = common.take_proposal(proposal_id)
    if p.get("kind") == "add_project":
        project = common.add_agentwork_project(**p["add"])
        return {"status": "success", "project": project["name"],
                "message": f"Added the project '{project['name']}'. Tasks can now change it."}
    # Public posts proposed from chat: the owner's tap on Run is the go-ahead
    if p.get("kind") == "tiktok":
        path = actions.resolve_video(state.VIDEOS_DIR, p["add"]["video"])
        if path is None:
            raise HTTPException(status_code=404, detail=f"The video {p['add']['video']} no longer exists")
        return {"status": "success", "task_id": actions.queue_tiktok_post(state.db, path, source="DeskAI suggestion")}
    if p.get("kind") == "pins":
        return {"status": "success", "task_id": actions.queue_pins(state.db, int(p["add"]["count"]),
                                                                   source="DeskAI suggestion")}
    # Email and calendar: the card showed exactly this; the tap on Run is the go-ahead
    if p.get("kind") == "email":
        try:
            sent = connectors.send_email(p["add"], draft=bool(p["add"].get("draft")))
        except connectors.ConnectorError as e:
            raise HTTPException(status_code=502, detail=f"The email wasn't sent: {e}")
        what = "Saved a draft" if p["add"].get("draft") else "Sent"
        _remember(f"{what} the email '{sent.get('subject', '')}' to {sent.get('to', '')} (the user approved the card).")
        return {"status": "success", "message": f"{what}: '{sent.get('subject', '')}' to {sent.get('to', '')}."}
    if p.get("kind") == "calendar_event":
        try:
            connectors.add_event(p["add"])
        except connectors.ConnectorError as e:
            raise HTTPException(status_code=502, detail=f"The event wasn't added: {e}")
        _remember(f"Added '{p['add']['title']}' at {p['add']['start']} to the user's calendar (they approved the card).")
        return {"status": "success", "message": f"Added '{p['add']['title']}' to your calendar."}
    # Texts and calls: the phone opened Messages or the dialer (the app does it); the owner sends or calls
    if p.get("kind") in ("text", "call"):
        a = p["add"]
        what = f"Opened a text to {a['name']}" if p["kind"] == "text" else f"Opened a call to {a['name']}"
        _remember(f"{what} ({a['number']}) on the user's phone; they send or call themselves.")
        return {"status": "success", "message": f"{what}."}
    if p.get("kind") == "computer":
        return {"status": "success", "task_id": actions.queue_computer(state.db, p["add"]["goal"],
                                                                       source="DeskAI suggestion")}
    task_id = common.queue_instruction(p["instruction"], p.get("project"), how="from a suggestion in DeskAI",
                                       source="DeskAI suggestion")
    return {"status": "success", "task_id": task_id}


def _remember(event: str) -> None:
    try:
        state.get_memory().record_event(event)
    except Exception as e:
        state.logger.warning(f"Could not record in memory: {e}")


@router.post("/api/proposals/{proposal_id}/dismiss")
def dismiss_proposal(proposal_id: str):
    p = common.take_proposal(proposal_id)
    try:
        state.get_memory().record_event(f"The user dismissed a suggested task: {p['instruction'][:300]}")
    except Exception as e:
        state.logger.warning(f"Could not record the dismissal in memory: {e}")
    return {"status": "ok"}


@router.get("/api/agentwork/projects")
def agentwork_projects():
    return [{"name": p["name"], "description": p.get("description") or "", "repo": p.get("repo") or ""}
            for p in state.get_agentwork().list_projects()]


@router.post("/api/agentwork/projects")
def agentwork_add_project(req: AgentWorkProjectRequest):
    """Registers a repo with AgentWork (it checks the repo can be reached first)."""
    project = common.add_agentwork_project(req.name, req.repo, req.description)
    return {"status": "success", "project": {k: project.get(k, "") for k in ("name", "description", "repo")}}


@router.post("/api/agentwork/jobs")
def agentwork_job(req: AgentWorkJobRequest, idempotency_key: str | None = common.IdempotencyKey,
                  task_source: str | None = common.TaskSourceHeader):
    existing = common.task_for_key(idempotency_key)
    if existing:
        return {"status": "success", "task_id": existing, "duplicate": True}
    instruction = req.instruction.strip()
    if len(instruction) < 8:
        raise HTTPException(status_code=400, detail="Describe the change in a sentence (at least 8 characters).")
    if req.project not in {p["name"] for p in state.get_agentwork().list_projects()}:
        raise HTTPException(status_code=400, detail=f"Unknown AgentWork project: {req.project}")
    task_id = common.queue_instruction(instruction, req.project, how="as an AgentWork job from DeskAI",
                                       source=common.clean_source(task_source, default="DeskAI AgentWork"))
    common.remember_key(idempotency_key, task_id)
    return {"status": "success", "task_id": task_id}


@router.post("/api/push/register")
def push_register(req: PushRegisterRequest):
    push.register_device(req.token.strip(), req.device_name or "")
    return {"status": "ok", "push_ready": push.is_configured()}


@router.post("/api/push/unregister")
def push_unregister(req: PushTokenRequest):
    push.unregister_device(req.token.strip())
    return {"status": "ok"}


@router.post("/api/push/test")
def push_test():
    if push.device_count() == 0:
        return {"status": "no_devices"}
    if not push.is_configured():
        return {"status": "not_configured", "message": "The Firebase service account key isn't on the PC yet."}
    sent = push.send("test", "🔔 DeskAI", "Push notifications are working.", channel="alerts")
    return {"status": "sent", "devices": sent}


@router.post("/api/backup")
def backup_now():
    """Takes a memory backup now (the daemon also takes one nightly)."""
    try:
        result = backup.create_backup(state.db.db_path, local_dir=state.db.db_path.parent / "backups", mirror=backup.mirror_dir())
    except Exception as e:
        result = backup.BackupResult(ok=False, errors=[str(e)])
    backup.record_result(state.db, result)
    if not result.ok:
        return {"ok": False, "message": result.summary()}
    places = ["this PC"]
    if backup.mirror_dir() is not None:
        places.append("Google Drive" if len(result.saved_to) > 1 else "Google Drive (failed)")
    message = (f"Memory backed up: {result.name} ({result.size_bytes / 1e6:.1f} MB) to {', '.join(places)}; "
               f"newest {backup.keep_count()} kept")
    if result.errors:
        message += ". Problems: " + "; ".join(result.errors)
    return {"ok": True, "message": message}


@router.post("/api/cleanup")
def cleanup_storage():
    """Removes temp render files, older videos and logs unused for a while."""
    try:
        cleaned = tiktok_videos.cleanup(older_than=600)   # spare a render that is in progress
        c_temp, c_prune = cleaned.get("removed_temp_files", 0), cleaned.get("pruned_videos", 0)
        c_logs = prune_old_logs(state.WORKSPACE_DIR / "logs")
    except Exception as e:
        state.logger.exception(f"Cleanup failed: {e}")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {e}")
    kept = len(list(state.VIDEOS_DIR.glob("*.mp4")))
    return {"message": f"Cleaned {c_temp} temp files, pruned {c_prune} older videos ({kept} kept), "
                       f"removed {len(c_logs)} log files unused for {LOG_MAX_AGE_DAYS}+ days."}


@router.post("/api/restart", status_code=202)
def restart_agent(now: bool = False):
    """Restarts once the running task has finished ("draining"; new work waits), or at once with ?now=true."""
    return common.schedule_restart("DeskAI", now=now)


@router.get("/api/research/opportunities")
def get_research_opportunities():
    """Recent AI opportunities digests compiled by the automated research worker."""
    from core import research_worker
    return research_worker.get_latest_digest(state.db)


@router.post("/api/research/run")
def run_research_now():
    """Runs the automated AI research worker on-demand, saving the digest to Canvas and memory."""
    from core import research_worker
    result = research_worker.run_research_cycle(
        state.db, memory=state.get_memory(), llm=None, force=True
    )
    if not result:
        return {"status": "ok", "message": "Research cycle finished with no new opportunities"}
    return {"status": "success", "digest": result}

