"""
"Needs you": what a computer task (AgentComputerUse) is waiting on, its screenshot, and the owner's answer.
The tool waits for the answer and carries on with it; the alerts and reminders come from core/computer_use.py.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from core import actions, computer_use
from interfaces.hud import state

router = APIRouter()


class ComputerAnswer(BaseModel):
    answer: str = Field(..., min_length=1, max_length=1000)


@router.get("/api/computer/requests")
def computer_requests():
    """What the computer tasks are waiting on, newest first."""
    return {"requests": computer_use.pending_requests()}


@router.get("/api/computer/requests/{request_id}/screenshot")
def computer_request_screenshot(request_id: str):
    try:
        path = computer_use.screenshot_path(request_id)
    except computer_use.ComputerUseError:
        path = None
    if path is None:
        raise HTTPException(status_code=404, detail="no screenshot for that request")
    return FileResponse(str(path), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/api/computer/runs/{run_id}/screenshots/{name}")
def computer_run_screenshot(run_id: str, name: str):
    """One step's screenshot from a computer run (step-NN.jpg / snap-NNN.jpg), for the chat's image links."""
    path = computer_use.run_screenshot_path(run_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="no such screenshot")
    return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/api/computer/requests/{request_id}/answer")
def computer_request_answer(request_id: str, payload: ComputerAnswer):
    """The owner's answer (Approve/Deny, one of the choices, or text); the waiting task carries on with it."""
    try:
        return {"status": "ok", **computer_use.answer(request_id, payload.answer)}
    except computer_use.ComputerUseError as e:
        raise HTTPException(status_code=409 if "already" in str(e) else 400, detail=str(e))


# ---------------------------------------------------------------------------------------------------- Take over
# The owner uses the computer from their phone. Start, then show /frame about once a second and send each tap or
# key to /input; /stop hands back (a task waiting on request_id then looks at the screen again).

class TakeoverStart(BaseModel):
    request_id: Optional[str] = Field(None, max_length=40)
    machine: Optional[str] = Field(None, max_length=20)      # "mini" (default) or "laptop"


def _takeover_error(e: Exception) -> HTTPException:
    msg = str(e)
    if "isn't installed" in msg:
        return HTTPException(status_code=503, detail=msg)
    if "isn't set up" in msg or "isn't answering" in msg:
        return HTTPException(status_code=503, detail=msg)
    if "take over first" in msg or "already" in msg or "closed" in msg:
        return HTTPException(status_code=409, detail=msg)
    return HTTPException(status_code=400, detail=msg)


@router.get("/api/computer/takeover/machines")
def takeover_machines():
    """The computers Take over can show (the laptop once it's set up)."""
    return {"machines": computer_use.takeover_machines()}


@router.get("/api/computer/takeover")
def takeover_status(machine: Optional[str] = None):
    try:
        return computer_use.takeover_status(machine)
    except computer_use.ComputerUseError as e:
        raise _takeover_error(e)


@router.post("/api/computer/takeover/start")
def takeover_start(payload: TakeoverStart = TakeoverStart()):
    try:
        return computer_use.takeover_start(payload.request_id, payload.machine)
    except computer_use.ComputerUseError as e:
        raise _takeover_error(e)


@router.get("/api/computer/takeover/activity")
def takeover_activity(machine: Optional[str] = None):
    """What a computer-use run is doing (goal, step, plain words): the banner on Take over's screen. Read-only."""
    try:
        return computer_use.takeover_activity(machine)
    except computer_use.ComputerUseError as e:
        raise _takeover_error(e)


@router.get("/api/computer/takeover/frame")
def takeover_frame(machine: Optional[str] = None, view: int = 0):
    """The screen as a JPEG. view=1: watch only (no control needed; a running task keeps going)."""
    try:
        jpeg, info = computer_use.takeover_frame(machine, view_only=bool(view))
    except computer_use.ComputerUseError as e:
        raise _takeover_error(e)
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Screen": f"{info['width']}x{info['height']}"})


@router.post("/api/computer/takeover/input")
def takeover_input(event: Dict[str, Any] = Body(...)):
    """One tap, drag, scroll, text or key from the phone: {"type": "tap", "x": 0.5, "y": 0.4} (x and y are 0 to 1
    across and down the picture)."""
    try:
        event = dict(event)
        machine = event.pop("machine", None)
        return {"status": "ok", "done": computer_use.takeover_input(event, machine)}
    except computer_use.ComputerUseError as e:
        raise _takeover_error(e)


@router.post("/api/computer/takeover/stop")
def takeover_stop(payload: TakeoverStart = TakeoverStart()):
    try:
        return {"status": "ok", **computer_use.takeover_stop(payload.request_id, payload.machine)}
    except computer_use.ComputerUseError as e:
        raise _takeover_error(e)


# ---------------------------------------------------------------------------------------------------- retasks
# Clean runs saved so the same task asked again is repeated without the AI (answers and writing always fresh).

@router.get("/api/computer/retasks")
def computer_retasks():
    try:
        return {"retasks": computer_use.list_retasks()}
    except computer_use.ComputerUseError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/api/computer/retasks/{rid}/run")
def computer_run_retask(rid: str):
    """Runs a retask now (DeskAI's /retask): the computer task is queued with the retask's exact wording, so the
    tool repeats it. Started by the owner's own tap, like /task, so no proposal card."""
    try:
        found = next((r for r in computer_use.list_retasks() if r.get("id") == rid), None)
    except computer_use.ComputerUseError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if found is None:
        raise HTTPException(status_code=404, detail="no such retask")
    task_id = actions.queue_computer(state.db, found["goal"], source="DeskAI /retask")
    return {"status": "ok", "task_id": task_id, "goal": found["goal"]}


@router.delete("/api/computer/retasks/{rid}")
def computer_forget_retask(rid: str):
    try:
        forgotten = computer_use.forget_retask(rid)
    except computer_use.ComputerUseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not forgotten:
        raise HTTPException(status_code=404, detail="no such retask")
    return {"status": "ok"}


# ------------------------------------------------------------------------------------------------ credentials
# Credentials stored securely in Windows Credential Manager (DPAPI).
# Passwords are never returned in list responses or logs.

class SaveCredentialPayload(BaseModel):
    domain: str = Field(..., min_length=1, max_length=200)
    username: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=500)


class DeleteCredentialPayload(BaseModel):
    domain: str = Field(..., min_length=1, max_length=200)
    username: str = Field(..., min_length=1, max_length=200)


@router.get("/api/computer/credentials")
def get_credentials():
    try:
        return {"credentials": computer_use.list_passwords()}
    except computer_use.ComputerUseError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/api/computer/credentials")
def save_credential(payload: SaveCredentialPayload):
    try:
        res = computer_use.save_password(payload.domain, payload.username, payload.password)
        return {"status": "ok", "saved": res.get("saved", payload.domain), "username": payload.username}
    except computer_use.ComputerUseError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/computer/credentials")
def forget_credential(domain: Optional[str] = None, username: Optional[str] = None,
                      payload: Optional[DeleteCredentialPayload] = None):
    d = (payload.domain if payload else None) or domain
    u = (payload.username if payload else None) or username
    if not d or not u:
        raise HTTPException(status_code=400, detail="domain and username are required")
    try:
        forgotten = computer_use.forget_password(d, u)
    except computer_use.ComputerUseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not forgotten:
        raise HTTPException(status_code=404, detail="no such credential")
    return {"status": "ok"}

