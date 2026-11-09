"""Schedules: videos, pins, reports, reminders and tasks the agent runs at set times (core/schedules.py), for DeskAI's Schedules screen."""

import datetime

from fastapi import APIRouter, HTTPException

from core import schedules
from interfaces.hud import common, state
from interfaces.hud.models import ScheduleCreateRequest, SchedulePatchRequest

router = APIRouter()


def _store() -> schedules.ScheduleStore:
    return schedules.ScheduleStore(state.db)


def _note(text: str) -> None:
    """The assistant remembers schedule changes made in the app, like ones made in chat."""
    try:
        state.get_memory().record_event(text)
    except Exception as e:
        state.logger.warning(f"Could not record the schedule change in memory: {e}")


@router.get("/api/schedules")
def list_schedules():
    return {"schedules": _store().list(), "timezone": schedules.timezone_name(), "kinds": list(schedules.KINDS),
            "min_gap_minutes": schedules.MIN_GAP_MINUTES}


@router.get("/api/schedules/preview")
def preview_schedule(cron: str, kind: str = "video"):
    """Checks a schedule before saving it: whether it's allowed, in plain English, and its next three runs."""
    try:
        c = schedules.validate(kind, cron)
    except schedules.CronError as e:
        return {"valid": False, "error": str(e)}
    return {"valid": True, "description": schedules.describe(cron),
            "next_runs": [schedules.local_iso(t) for t in c.next_runs(datetime.datetime.now(), 3)]}


@router.post("/api/schedules")
def create_schedule(req: ScheduleCreateRequest, idempotency_key: str | None = common.IdempotencyKey):
    key = (idempotency_key or "").strip()
    earlier = state.db.task_for_request_key(key) if key else None
    if earlier and earlier.startswith("schedule:"):
        s = _store().get(int(earlier.split(":", 1)[1]))
        if s:
            return {"status": "success", "schedule": s, "duplicate": True}
    try:
        s = _store().create(req.name, req.kind, req.cron, source="DeskAI", enabled=req.enabled,
                            post_to_tiktok=req.post_to_tiktok, quote=req.quote, text=req.text,
                            instruction=req.instruction, project=req.project, count=req.count)
    except schedules.CronError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if key:
        state.db.remember_request_key(key, f"schedule:{s['id']}")
    _note(f"The user created schedule #{s['id']} '{s['name']}' in DeskAI: {s['kind']}, {s['description']}"
          + (", posting to TikTok" if s["post_to_tiktok"] else ""))
    return {"status": "success", "schedule": s}


@router.patch("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: int, req: SchedulePatchRequest):
    try:
        before = _store().get(schedule_id)
        s = _store().update(schedule_id, name=req.name, cron=req.cron, enabled=req.enabled,
                            post_to_tiktok=req.post_to_tiktok, quote=req.quote, text=req.text,
                            instruction=req.instruction, project=req.project, count=req.count)
    except KeyError:
        raise HTTPException(status_code=404, detail="Schedule not found")
    except schedules.CronError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if before and before["enabled"] != s["enabled"]:
        _note(f"The user {'resumed' if s['enabled'] else 'paused'} schedule #{s['id']} '{s['name']}' in DeskAI.")
    elif before and (before["cron"], before["post_to_tiktok"]) != (s["cron"], s["post_to_tiktok"]):
        _note(f"The user changed schedule #{s['id']} '{s['name']}' in DeskAI: now {s['description']}"
              + (", posting to TikTok" if s["post_to_tiktok"] else ""))
    return {"status": "success", "schedule": s}


@router.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    s = _store().get(schedule_id)
    if s is None or not _store().delete(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    state.db.append_log("INFO", f"Schedule #{schedule_id} '{s['name']}' deleted from DeskAI")
    _note(f"The user deleted schedule #{schedule_id} '{s['name']}' ({s['kind']}, {s['description']}) in DeskAI.")
    return {"status": "success", "id": schedule_id}


@router.post("/api/schedules/{schedule_id}/run")
def run_schedule_now(schedule_id: int):
    """Runs it once now (even in standby); the next scheduled time doesn't change."""
    try:
        outcome = schedules.run_now(state.db, schedule_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "success", **outcome}
