"""
The chat: DeskAI's conversation with the assistant. Quick commands (/status, /task, /restart, /cancel) are
answered directly, /image requests go to the ImageGen tool, and everything else is a turn with the assistant
(memory, tools, and the AI models in core/llm.py). Documents are uploaded first (POST /api/chat/attachments,
core/documents.py) and their text goes beside the message of the turn they're attached to.

How a message goes: POST /api/chat -> chat_endpoint() (§8) -> anything that must never reach a model is caught
first (sign-ins, passwords: §3, §7), then the quick commands, /image (§5), the self-check's "start the fix",
attachments (§6) -> _chat_turn(): the live status, then core/memory.py respond() with the tools.
(Route order matters in a web server file: functions here are mapped, not moved.)

This module is the shell: the router, the chat endpoint, and the image-job state. The work lives in the
sibling modules, one per section of the old 847-line file; everything other code imported from here is
re-exported below, so `from interfaces.hud.chat import ...` keeps working.

MAP OF THE CHAT MODULES
-----------------------
  chat.py               this shell: router assembly, the Stop button, image jobs, POST /api/chat
  chat_secrets.py   §3  sign-ins and passwords, caught before any AI model ever sees them
  chat_refine.py    §4  Refine: the chat box's ✨ button
  chat_images.py    §5  Images: /image, made by the ImageGen tool (the job plumbing stays here)
  chat_attachments.py §6  Attachments: upload, thumbnail, file, list (core/documents.py reads them)
  chat_signin.py    §7  Sign-in replies: what the chat answers for a Google or Zoho sign-in
  chat_turn.py      §8  The chat endpoint's turn, plus §2's Stop button state
"""

import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Response

from core import documents, vision  # re-exported: tests patch chat.documents / chat.vision
from interfaces.hud import chat_attachments, chat_images, chat_refine, chat_turn, state
from interfaces.hud.chat_attachments import (
    get_attachment_file,
    get_attachment_thumbnail,
    list_attachments,
    upload_attachment,
)
from interfaces.hud.chat_images import IMAGE_PREFIXES, SCENE_WAIT, _image_reply
from interfaces.hud.chat_refine import REFINE_SYSTEM_PROMPT, chat_refine_endpoint, refine_text
from interfaces.hud.chat_secrets import (
    GOOGLE_SIGNIN,
    PASSWORD_REPLY,
    PASSWORD_WORD,
    ZOHO_CODE,
    _holds_signin,
    _password_reply,
    _secret_like,
    holds_password,
)
from interfaces.hud.chat_signin import CONNECT_GOOGLE, _connect_google_reply, _google_signin_reply, _zoho_signin_reply
from interfaces.hud.chat_turn import (
    CANCEL_KEEP,
    COMMAND_CANCEL,
    COMMAND_TASK,
    OUTAGE_REPLY,
    TURN_KEEP,
    TURN_WAIT,
    _cancelled,
    _cancelled_lock,
    _chat_turn,
    _is_cancelled,
    _server_error,
    _turns,
    _turns_lock,
)
from interfaces.hud.models import ChatCancel, ChatRequest, ChatResponse

__all__ = [
    "CANCEL_KEEP",
    "COMMAND_CANCEL",
    "COMMAND_TASK",
    "CONNECT_GOOGLE",
    "GOOGLE_SIGNIN",
    "IMAGE_PREFIXES",
    "OUTAGE_REPLY",
    "PASSWORD_REPLY",
    "PASSWORD_WORD",
    "REFINE_SYSTEM_PROMPT",
    "SCENE_WAIT",
    "TURN_KEEP",
    "TURN_WAIT",
    "ZOHO_CODE",
    "_cancelled",
    "_cancelled_lock",
    "_chat_turn",
    "_connect_google_reply",
    "_google_signin_reply",
    "_holds_signin",
    "_image_jobs",
    "_is_cancelled",
    "_password_reply",
    "_secret_like",
    "_server_error",
    "_turns",
    "_turns_lock",
    "_zoho_signin_reply",
    "chat_attachments",
    "chat_cancel",
    "chat_endpoint",
    "chat_images",
    "chat_refine",
    "chat_refine_endpoint",
    "chat_turn",
    "documents",
    "get_attachment_file",
    "get_attachment_thumbnail",
    "get_image_job",
    "holds_password",
    "list_attachments",
    "refine_text",
    "router",
    "start_image_job",
    "upload_attachment",
    "vision",
]

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
# §2  THE STOP BUTTON
#     chat_cancel marks a turn stopped; the turn checks it and neither stores nor sends the reply.
#     (The state itself lives in chat_turn.py, which the turn reads; this endpoint mutates the same dict.)
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
@router.post("/api/chat/cancel")
def chat_cancel(payload: ChatCancel):
    """The user stopped this message's reply in the app: when it's ready it is dropped, not stored."""
    now = time.time()
    with _cancelled_lock:
        for old in [t for t, at in _cancelled.items() if now - at > CANCEL_KEEP]:
            del _cancelled[old]
        _cancelled[payload.turn_id] = now
    return {"ok": True}


router.include_router(chat_refine.router)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
# §5  IMAGES IN THE BACKGROUND (2026-10-27)
#     An image takes up to ~3 minutes, and a chat request held open that long was dropped on the way
#     ("Software caused connection abort") or kept the chat "thinking" after the image was made. The app
#     starts a job, gets its id at once, and asks how it's going; the chat stays free meanwhile.
#     The state lives here (tests reset chat._image_jobs); the reply itself is chat_images._image_reply.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
_image_jobs: dict[str, dict] = {}
_image_jobs_lock = threading.Lock()
IMAGE_JOB_KEEP = 3600       # seconds a finished job's answer is kept for the app to collect
IMAGE_JOBS_RUNNING = 3      # at most this many images are made at once


def _image_job_view(job_id: str, job: dict) -> dict:
    return {"job_id": job_id, "status": job["status"], "reply": job["reply"], "model": job["model"],
            "stage": job["stage"], "seconds": round((job["finished"] or time.time()) - job["started"], 1)}


def _run_image_job(job_id: str, message: str):
    def on_progress(stage: str):
        with _image_jobs_lock:
            _image_jobs[job_id]["stage"] = stage

    try:
        res = _image_reply(message, on_progress)
        # start_image_job only starts a job for a message that begins with an image prefix, and _image_reply
        # returns None only when there is no such prefix, so res cannot be None here.
        assert res is not None
        status, reply, model = ("done" if res.success else "failed"), res.reply, res.model
    except Exception as e:
        state.logger.exception("ImageGen job failed")
        status, reply, model = "failed", f"⚠️ Could not generate the image: {e}.", "auto"
    with _image_jobs_lock:
        _image_jobs[job_id].update(status=status, reply=reply, model=model, finished=time.time())


@router.post("/api/images/jobs")
def start_image_job(payload: ChatRequest):
    """Starts making the image a /image message asks for; answers at once with the job (help comes back done)."""
    message = payload.message.strip()
    if next((p for p in IMAGE_PREFIXES if message.lower().startswith(p)), None) is None:
        raise HTTPException(status_code=400, detail="Not an image request (start it with /image)")
    now = time.time()
    with _image_jobs_lock:
        for old in [j for j, e in _image_jobs.items() if e["finished"] and now - e["finished"] > IMAGE_JOB_KEEP]:
            del _image_jobs[old]
        # The same message sent again (the app retrying another address) is the same job, not a second image
        if payload.turn_id and payload.turn_id in _image_jobs:
            return _image_job_view(payload.turn_id, _image_jobs[payload.turn_id])
        if sum(1 for e in _image_jobs.values() if e["status"] == "running") >= IMAGE_JOBS_RUNNING:
            raise HTTPException(status_code=429, detail=f"{IMAGE_JOBS_RUNNING} images are already being made")
        job_id = payload.turn_id or uuid.uuid4().hex
        job = _image_jobs[job_id] = {"status": "running", "reply": None, "model": None, "stage": "Starting",
                                     "started": now, "finished": None}
    request = message.split(None, 1)[1].strip() if " " in message else ""
    if not request or request.lower() in ("help", "-h", "--help"):
        _run_image_job(job_id, message)        # the guide is quick: answer it now
    else:
        threading.Thread(target=_run_image_job, args=(job_id, message), name=f"image-{job_id[:8]}",
                         daemon=True).start()
    with _image_jobs_lock:
        return _image_job_view(job_id, job)


@router.get("/api/images/jobs/{job_id}")
def get_image_job(job_id: str):
    with _image_jobs_lock:
        job = _image_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such image job (the agent may have restarted)")
        return _image_job_view(job_id, job)


router.include_router(chat_attachments.router)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
# §8  THE CHAT ENDPOINT
#     chat_endpoint() handles a message from the app; _chat_turn() (chat_turn.py) builds the live status and
#     runs the turn (core/memory.py respond()), then returns the reply with any cards, questions or started task.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
@router.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(payload: ChatRequest, response: Response):
    # A plain def on purpose: replies call AI models and image services for seconds to minutes, and FastAPI runs
    # sync endpoints in its thread pool. As async def, that work blocked the event loop and froze every other
    # request (task cards, status pings) until it finished.
    """
    Main conversational and command interface for the DeskAI mobile companion.
    Supports built-in commands (/status, /task <prompt>, /cancel) as well as full
    multi-turn memory and LLM reasoning via AlwaysOnAgent's MemoryManager.
    """
    if not payload.turn_id:
        return _chat_turn(payload, response, scene_wait=SCENE_WAIT)
    now = time.time()
    with _turns_lock:
        for old in [t for t, e in _turns.items() if e["done"].is_set() and now - e["at"] > TURN_KEEP]:
            del _turns[old]
        entry = _turns.get(payload.turn_id)
        first = entry is None
        if first:
            entry = _turns[payload.turn_id] = {"done": threading.Event(), "at": now, "result": None, "error": None}
    # The turn was either already there or just created above, so entry is set either way.
    assert entry is not None
    if not first:
        state.logger.info(f"[chat] turn {payload.turn_id[:12]} arrived again: answering it once")
        if not entry["done"].wait(TURN_WAIT):
            raise HTTPException(status_code=409, detail="This message is still being answered.")
        if entry["error"] is not None:
            raise entry["error"]
        return entry["result"]
    try:
        entry["result"] = _chat_turn(payload, response, scene_wait=SCENE_WAIT)
        return entry["result"]
    except Exception as e:
        entry["error"] = e
        raise
    finally:
        entry["at"] = time.time()
        entry["done"].set()
