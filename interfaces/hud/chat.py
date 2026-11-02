"""
The chat: DeskAI's conversation with the assistant. Quick commands (/status, /task, /restart, /cancel) are
answered directly, /image requests go to the ImageGen tool, and everything else is a turn with the assistant
(memory, tools, and the AI models in core/llm.py). Documents are uploaded first (POST /api/chat/attachments,
core/documents.py) and their text goes beside the message of the turn they're attached to.
"""

import base64
import binascii
import re
import threading
import time
import uuid
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from core import actions, connectors, documents, imagegen, maintenance, recent_images, vision
from core.assistant_tools import AssistantTools
from core.daemon import kill_process_tree
from core.task_engines import normalize_engine
from interfaces.hud import common, state
from interfaces.hud.models import (AttachmentUpload, ChatCancel, ChatRefineRequest, ChatRefineResponse,
                                   ChatRequest, ChatResponse)

router = APIRouter()

# Turns the user stopped in the app: their reply is dropped, not kept in memory (2026-10-05: a cancelled reply
# about a misspelt search still landed in the history). turn_id -> when it was cancelled.
_cancelled: Dict[str, float] = {}
_cancelled_lock = threading.Lock()
CANCEL_KEEP = 3600     # seconds a cancellation is remembered (a turn never runs that long)


def _is_cancelled(turn_id: Optional[str]) -> bool:
    if not turn_id:
        return False
    with _cancelled_lock:
        return turn_id in _cancelled


@router.post("/api/chat/cancel")
def chat_cancel(payload: ChatCancel):
    """The user stopped this message's reply in the app: when it's ready it is dropped, not stored."""
    now = time.time()
    with _cancelled_lock:
        for old in [t for t, at in _cancelled.items() if now - at > CANCEL_KEEP]:
            del _cancelled[old]
        _cancelled[payload.turn_id] = now
    return {"ok": True}


REFINE_SYSTEM_PROMPT = (
    "You are an expert prompt engineer and assistant optimizer.\n"
    "Your job is to enhance and refine the draft prompt or message typed by the user in their chat box before they send it to AlwaysOnAgent.\n\n"
    "Rules:\n"
    "1. Preserve the user's intent, core meaning, tone, and any specific constraints, names, code, or values.\n"
    "2. Make the prompt clearer, more specific, and effective for the assistant to understand and act on.\n"
    "3. If the user input starts with a command prefix (e.g. /task, /image, /render, etc.), keep the command intact and refine the instruction part.\n"
    "4. Return ONLY the refined prompt text. Do NOT add conversational filler, intros (like 'Here is the refined prompt:'), explanations, or surrounding quotation marks."
)


def _holds_signin(text: str) -> bool:
    """A Google sign-in address or Zoho Self Client details anywhere in the text (a draft can hold more than the
    paste). Same rule as the chat: these never reach a model (GOOGLE_SIGNIN, ZOHO_CODE below)."""
    text = text or ""
    return bool(GOOGLE_SIGNIN.match(text) or ZOHO_CODE.search(text) or holds_password(text)
                or re.search(r"(localhost|127\.0\.0\.1)[:/]\S*[?&](code|error)=", text, re.I))


# A password typed into the chat (2026-10-26: "The password is <it>" went to the cloud model, whose free tier may
# use prompts for training). Caught before any model, like the sign-ins: never sent, never stored. A word after
# "password", "pw", "pin"... counts only if it looks like a secret (AgentComputerUse's activity.secret_like), or, after
# ":" or "=", is 6+ characters: "the password is going to be typed in" is a sentence, not a password.
PASSWORD_WORD = re.compile(r"\b(?:pass(?:word|wd|code|phrase)?|pw|pwd|pin)\b\s*(?P<sep>is\b|was\b|[:=])?\s*"
                           r"[\"'`“‘]?(?P<value>[^\s\"'`”’]+)", re.I)


def _secret_like(t: str) -> bool:
    """Same test as AgentComputerUse's activity.secret_like: one word mixing letters with digits or symbols (6+),
    or 4+ digits; not a web address, email address, file name or path."""
    t = (t or "").strip().rstrip(".,!?;)")
    if not t or " " in t:
        return False
    if t.isdigit():
        return len(t) >= 4
    if re.fullmatch(r"(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?", t) and not re.search(r"\d", t.split("/")[0]):
        return False
    if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", t) or re.match(r"([A-Za-z]:\\|\\\\|/|~[\\/])", t):
        return False
    return any(c.isalpha() for c in t) and any(c.isdigit() or not c.isalnum() for c in t) and len(t) >= 6


def holds_password(text: str) -> bool:
    for m in PASSWORD_WORD.finditer(text or ""):
        value = m.group("value").rstrip(".,!?;)")
        if _secret_like(value) or (m.group("sep") in (":", "=") and len(value) >= 6):
            return True
    return False


PASSWORD_REPLY = ("🔒 That looks like a password, so I didn't send it to the AI or save it in the chat. To let "
                  "computer tasks sign in with it, save it in the Vault (🔐 in the menu): it goes into the PC's "
                  "Windows Credential Manager, where only the computer tool can read it.")


def _password_reply(user_msg: str) -> Optional[ChatResponse]:
    if not holds_password(user_msg):
        return None
    try:
        mem = state.get_memory()
        mem.add_message("user", "(a message holding a password, hidden)")
        mem.add_message("assistant", PASSWORD_REPLY)
    except Exception:
        pass
    return ChatResponse(success=True, reply=PASSWORD_REPLY, model="system",
                        agent_status=common.determine_agent_status(), task_id=None)


def refine_text(raw_text: str, instruction: Optional[str] = None) -> Tuple[str, str]:
    """Refines or enhances a user's prompt text using the available LLM routes.
    Returns (refined_text, model_name). If models fail, falls back to original text."""
    text = (raw_text or "").strip()
    if not text:
        return text, "none"
    # 2026-10-14: the chat box is where sign-in links and Zoho codes get pasted; refining one would send it to a
    # model, which the chat itself never does. Hand it back untouched; sending it goes through the chat's intercept.
    if _holds_signin(text) or _holds_signin(instruction or ""):
        return text, "none"

    user_prompt = f"Prompt to enhance:\n{text}"
    if instruction and instruction.strip():
        user_prompt += f"\n\nSpecial refinement instruction: {instruction.strip()}"

    messages = [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    from core.llm import CloudLLM
    mem = state.get_memory()
    llm = getattr(mem, "llm", None)
    if llm is None:
        try:
            cloud = CloudLLM()          # its first argument is a Groq key, not the database
            if cloud.is_available():
                llm = cloud
        except Exception:
            llm = None

    if llm is not None and getattr(llm, "is_available", lambda: True)():
        try:
            from core.llm_usage import usage_purpose
            # Background routes: Flash-Lite, Gemma, Groq... never Flash, whose ~20 a day are for chat replies
            with usage_purpose("refine"):
                content, route = llm.complete(messages, max_tokens=600, temperature=0.4, timeout=20,
                                              models=CloudLLM.BACKGROUND_MODELS)
                if content and content.strip():
                    cleaned = content.strip()
                    if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("“") and cleaned.endswith("”")):
                        if len(cleaned) >= 2:
                            cleaned = cleaned[1:-1].strip()
                    return cleaned, route
        except Exception as e:
            state.logger.warning(f"LLM prompt refinement failed: {e}")

    return text, "raw"


@router.post("/api/chat/refine", response_model=ChatRefineResponse)
@router.post("/api/chat/enhance", response_model=ChatRefineResponse)
def chat_refine_endpoint(payload: ChatRefineRequest, response: Response):
    """
    Enhance or refine draft text typed into the DeskAI chat input box.
    Returns the refined text ready to replace or populate the input field.
    """

    raw_text = payload.input_text
    if not raw_text:
        raise HTTPException(status_code=400, detail="Missing or empty text field")

    refined, model_used = refine_text(raw_text, instruction=payload.instruction)
    return ChatRefineResponse(
        success=True,
        refined=refined,
        original=raw_text,
        model=model_used,
    )


SCENE_WAIT = 50       # seconds a turn waits for an attached image's scene note (DeskAI waits 120 s for the reply)
IMAGE_PREFIXES = ("/image", "/imagine", "/draw", "generate an image of", "create an image of", "draw an image of")


def _image_reply(user_msg: str, on_progress=None) -> Optional[ChatResponse]:
    """The reply to an image request (ImageGen makes the image), or None if the message isn't one.
    on_progress gets what ImageGen is doing meanwhile ("AI Horde: 3rd in line")."""
    prefix = next((p for p in IMAGE_PREFIXES if user_msg.lower().startswith(p)), None)
    if prefix is None:
        return None
    request = user_msg[len(prefix):].strip()
    if not request or request.lower() in ("help", "-h", "--help"):
        try:
            guide = imagegen.help_text()
        except imagegen.ImageGenError as e:
            guide = f"⚠️ The image guide isn't available: {e}"
        return ChatResponse(success=True, reply=guide, model="system", agent_status="idle", task_id=None)

    try:
        made = imagegen.generate(request, state.IMAGES_DIR, on_progress=on_progress)
    except imagegen.ImageGenError as e:
        state.logger.warning(f"ImageGen: {e}")
        info = e.result
        recent_images.remember(state.db, info.get("prompt") or request, style=info.get("style") or "", ok=False)
        return ChatResponse(success=False, reply=f"⚠️ Could not generate the image: {e}.", model="auto",
                            agent_status="idle", task_id=None)

    prompt, enhanced, filename = made["prompt"], made.get("enhanced") or "", made["filename"]
    state.db.append_log("INFO", f"[WebHUD] Generated image for: '{prompt[:40]}'")
    details = [f"**Engine:** `{made['model']}`"]
    if made.get("style"):
        details.append(f"**Style:** `{made['style']}`")
    if enhanced:
        details.append("✨ *Prompt enriched by Gemini*")
    expansion_note = f"\n> *\"{enhanced}\"*\n" if enhanced else ""
    reply = (
        f"🎨 **Generated Image:** \"{prompt}\"\n\n"
        f"{expansion_note}"
        f"![{prompt}](/images/{filename})\n\n"
        f"{' • '.join(details)}\n\n*(Saved to `{filename}`)*"
    )
    # Short-term only (the owner's choice): kept for follow-ups like "enhance my last prompt", never in
    # long-term memory (no archive, no facts extracted)
    recent_images.remember(state.db, prompt, enhanced=enhanced, style=made.get("style") or "",
                           model=made["model"], filename=filename)
    return ChatResponse(success=True, reply=reply, model=made["model"], agent_status="idle", task_id=None)


# Images in the background (2026-10-27): an image takes up to ~3 minutes, and a chat request held open that long was
# dropped on the way ("Software caused connection abort") or kept the chat "thinking" after the image was made. The
# app starts a job, gets its id at once, and asks how it's going; the chat stays free meanwhile.
_image_jobs: Dict[str, dict] = {}
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


@router.post("/api/chat/attachments")
def upload_attachment(req: AttachmentUpload):
    """Keeps a document and reads its text (OCR for images and scans). A plain def: OCR can take seconds."""
    try:
        data = base64.b64decode(req.data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="data_b64 isn't valid base64")
    try:
        record = documents.save(req.name, data, conversation_id=req.conversation_id)
    except documents.DocumentError as e:
        raise HTTPException(status_code=422, detail=str(e))
    rec = documents.load(record["id"]) or {}
    if record["kind"] in ("image", "video"):           # a scene note from the local vision model, in the background
        vision.enqueue(record["id"], state.get_memory())
    thumb_url = f"/api/chat/attachments/{record['id']}/thumb" if record.get("has_thumb") else None
    return {
        "status": "success",
        "attachment": {**record, "thumb_url": thumb_url},
        "preview": (rec.get("text") or "")[:300],
    }


@router.get("/api/chat/attachments/{attachment_id}/thumb")
def get_attachment_thumbnail(attachment_id: str):
    """Returns the visual thumbnail (thumb.jpg) for an attachment."""
    if not re.fullmatch(r"att-[0-9a-f]{12}", attachment_id or ""):
        raise HTTPException(status_code=404, detail="Attachment not found")
    thumb_path = documents.ATTACH_DIR / attachment_id / "thumb.jpg"
    if not thumb_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(thumb_path, media_type="image/jpeg")


@router.get("/api/chat/attachments/{attachment_id}/file")
def get_attachment_file(attachment_id: str):
    """Returns the original uploaded attachment file."""
    rec = documents.load(attachment_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Attachment not found")
    file_path = documents.ATTACH_DIR / attachment_id / rec["name"]
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(file_path, filename=rec["name"])


@router.get("/api/chat/attachments")
def list_attachments(conversation_id: Optional[str] = None, limit: int = 10):
    items = documents.recent(conversation_id, max(1, min(limit, 50)))
    for a in items:
        if a.get("has_thumb") and "thumb_url" not in a:
            a["thumb_url"] = f"/api/chat/attachments/{a['id']}/thumb"
    return {"attachments": items}


# The address the owner's phone lands on after approving Google (connect_google): it carries a one-time code, so it
# goes straight to Connectors and never to an AI model, the chat history or memory.
# "connect google" is answered here with the real link, never by a model (2026-10-11: a model made up a sign-in
# link with the wrong permissions instead of calling connect_google, so pasting the address back failed)
CONNECT_GOOGLE = re.compile(r"^\s*(please\s+)?(re-?)?connect\s+(to\s+)?(my\s+)?google(\s+account)?(\s+again)?\s*[.!]*\s*$", re.I)


def _connect_google_reply(user_msg: str) -> Optional[ChatResponse]:
    if not CONNECT_GOOGLE.match(user_msg):
        return None
    try:
        url = connectors.google_auth_url()
        reply = ("Here's your Google link:\n\n"
                 f"[Connect Google]({url})\n\n"
                 "1. Open it, pick your Google account, and allow what's listed: reading Gmail, drafting and sending "
                 "email, your calendar, contacts, Drive, and your lists (Google Tasks). Nothing is sent or added to "
                 "your calendar without your tap on a card. If Google says the app isn't verified, it's your own app: "
                 "tap Advanced, then continue.\n"
                 "2. You'll land on a page that doesn't load (its address starts with http://localhost). Copy that "
                 "whole address and paste it here. It finishes the connection and is never saved or shown to the AI.")
        ok = True
    except Exception as e:
        reply, ok = f"⚠️ Couldn't get the Google link: {e}", False
    try:
        mem = state.get_memory()
        mem.add_message("user", user_msg.strip())
        mem.add_message("assistant", "(Gave the Google connection link and steps.)" if ok else reply)
    except Exception:
        pass
    return ChatResponse(success=ok, reply=reply, model="system", agent_status=common.determine_agent_status(),
                        task_id=None)


GOOGLE_SIGNIN = re.compile(r"^\s*(https?://)?(localhost|127\.0\.0\.1)[:/][^\s]*[?&](code|error)=", re.I)


# Zoho's Self Client details (connect_zoho): the Client ID and the one-time code are distinctive. The message holds
# the client secret, so it never reaches an AI model, the chat history or memory either. Either piece alone is
# caught too, and no word boundaries: the owner's first paste was the three run together (2026-10-06), and the
# boundaries let it through to the model.
ZOHO_CODE = re.compile(r"1000\.([A-Z0-9]{30}|[0-9a-f]{32}\.[0-9a-f]{32})")


def _zoho_signin_reply(user_msg: str) -> Optional[ChatResponse]:
    if not ZOHO_CODE.search(user_msg):
        return None
    try:
        done = connectors.zoho_connect(user_msg)
        reply, ok = f"✅ Zoho Mail is connected ({done.get('email')}). I can read it; I can't send, move or delete.", True
    except Exception as e:
        reply, ok = f"⚠️ That didn't connect Zoho Mail: {e}.", False
    try:
        mem = state.get_memory()
        mem.add_message("user", "(Zoho sign-in details, hidden)")
        mem.add_message("assistant", reply)
    except Exception:
        pass
    return ChatResponse(success=ok, reply=reply, model="system", agent_status=common.determine_agent_status(),
                        task_id=None)


def _google_signin_reply(user_msg: str) -> Optional[ChatResponse]:
    if not GOOGLE_SIGNIN.match(user_msg):
        return None
    try:
        done = connectors.google_auth_finish(user_msg.strip())
        reply = ("✅ Google is connected" + (f" ({done['email']})" if done.get("email") else "") + ". Allowed: "
                 + ", ".join(done.get("granted") or []) + ". Sending email and adding events still wait for your tap "
                 "on a card.")
        ok = True
    except Exception as e:
        reply, ok = f"⚠️ That didn't finish the Google connection: {e}. Ask me to connect Google for a new link.", False
    try:
        mem = state.get_memory()
        mem.add_message("user", "(Google sign-in address, hidden)")
        mem.add_message("assistant", reply)
    except Exception:
        pass
    return ChatResponse(success=ok, reply=reply, model="system", agent_status=common.determine_agent_status(),
                        task_id=None)


# One answer per message: DeskAI resends a message to its other address (home Wi-Fi <-> Tailscale) when the first
# connection fails, even after this server already took it, so the same turn_id can arrive twice. The repeat waits for
# the first run and gets its answer, instead of storing and answering the message again (docs/archive/AUDIT_2026-10-27.md R1).
_turns: Dict[str, dict] = {}
_turns_lock = threading.Lock()
TURN_KEEP = 600          # seconds a finished turn's answer is kept for a late repeat
COMMAND_TASK = re.compile(r"/task(\s|$)", re.I)
COMMAND_CANCEL = re.compile(r"/cancel(\s|$)", re.I)


def _server_error(doing: str, e: Exception) -> HTTPException:
    """A 500 that says something went wrong and where to look, not the exception's own text (paths, SQL, library
    messages; 2026-10-28 audit 2.9). The full error is in the log under the same short id."""
    ref = uuid.uuid4().hex[:8]
    state.logger.exception(f"Error {doing} [{ref}]: {e}")
    return HTTPException(status_code=500, detail=f"Something went wrong {doing} (error {ref}; the details are in the log).")


OUTAGE_REPLY = ("⚠️ The AI services aren't answering right now (busy or down). Your message is saved: try again in a "
                "few minutes and I'll pick it up.")
TURN_WAIT = 330          # how long a repeat waits for the first run (DeskAI's longest read timeout)


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
        return _chat_turn(payload, response)
    now = time.time()
    with _turns_lock:
        for old in [t for t, e in _turns.items() if e["done"].is_set() and now - e["at"] > TURN_KEEP]:
            del _turns[old]
        entry = _turns.get(payload.turn_id)
        first = entry is None
        if first:
            entry = _turns[payload.turn_id] = {"done": threading.Event(), "at": now, "result": None, "error": None}
    if not first:
        state.logger.info(f"[chat] turn {payload.turn_id[:12]} arrived again: answering it once")
        if not entry["done"].wait(TURN_WAIT):
            raise HTTPException(status_code=409, detail="This message is still being answered.")
        if entry["error"] is not None:
            raise entry["error"]
        return entry["result"]
    try:
        entry["result"] = _chat_turn(payload, response)
        return entry["result"]
    except Exception as e:
        entry["error"] = e
        raise
    finally:
        entry["at"] = time.time()
        entry["done"].set()


def _chat_turn(payload: ChatRequest, response: Response) -> ChatResponse:

    user_msg = payload.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Missing or empty message field")

    requested_model = payload.model if payload.model and payload.model != "auto" else (
        payload.engine if payload.engine and payload.engine != "auto" else "auto"
    )

    signin_reply = (_connect_google_reply(user_msg) or _google_signin_reply(user_msg) or _zoho_signin_reply(user_msg)
                    or _password_reply(user_msg))
    if signin_reply is not None:
        return signin_reply

    # 1. Built-in Agent Quick Commands
    if user_msg.lower() == "/status":
        stats = state.db.get_stats()
        status = stats.get("daemon_status", "idle")
        phase = stats.get("overall_phase", "idle")
        engine = stats.get("default_engine", "auto")
        in_prog = state.db.list_tasks(limit=1, phase="in_progress")
        active_title = in_prog[0]["title"] if in_prog else "None"
        try:
            mem = state.get_memory()
            facts_count = mem.stats()["facts_active"] if mem else 0
        except Exception:
            facts_count = 0

        reply = (
            f"Daemon is active and listening.\n"
            f"Status: {status} | Phase: {phase} | Engine: {engine}\n"
            f"Active Task: {active_title}\n"
            f"Tasks Completed: {stats.get('tasks_completed', 0)} | Memory: {facts_count} active facts."
        )
        return ChatResponse(
            success=True,
            reply=reply,
            model=requested_model,
            agent_status=common.determine_agent_status(),
            task_id=None
        )

    # The command word itself: "/tasks" was queued as the task "s" (2026-10-28 audit 2.7)
    if COMMAND_TASK.match(user_msg):
        task_prompt = user_msg[5:].strip()
        if not task_prompt:
            return ChatResponse(
                success=False,
                reply="Please provide a task prompt: `/task <instruction>`",
                model=requested_model,
                agent_status=common.determine_agent_status(),
                task_id=None
            )
        try:
            task_engine = normalize_engine(payload.engine) if payload.engine and payload.engine != "auto" else "auto"
            task_id = state.db.create_task(
                title=actions.task_title(task_prompt),
                prompt=task_prompt,
                priority="medium",
                engine=task_engine,
                metadata={"source": "DeskAI chat (/task)"}
            )
            state.db.append_log("INFO", f"[WebHUD] Chat enqueued task [{task_id}]: '{task_prompt[:50]}'")
            common.wake_daemon()

            # Record turn in assistant memory so assistant knows what task was prompted
            try:
                mem = state.get_memory()
                mem.add_message("user", user_msg)
                mem.add_message("assistant", f"Enqueued background task [{task_id}] (engine: {task_engine}): '{task_prompt}'.")
            except Exception as mem_err:
                state.logger.debug(f"[WebHUD] Memory recording skipped for /task: {mem_err}")

            return ChatResponse(
                success=True,
                reply=f"Enqueued task [{task_id}]: '{task_prompt}'",
                model=requested_model,
                agent_status=common.determine_agent_status(),
                task_id=task_id
            )
        except Exception as e:
            raise _server_error("creating a task from chat", e)

    if user_msg.lower() == "/restart":
        restart = common.schedule_restart("chat /restart")
        return ChatResponse(
            success=True,
            reply=f"🔄 {restart['message']} Back shortly after that.",
            model=requested_model,
            agent_status="idle",
            task_id=None
        )

    if COMMAND_CANCEL.match(user_msg):
        in_prog = state.db.list_tasks(limit=1, phase="in_progress")
        if in_prog:
            task_to_cancel = in_prog[0]
            tid = task_to_cancel["id"]
            worker_pid = task_to_cancel.get("worker_pid")
            if worker_pid:
                kill_process_tree(worker_pid, logger=state.logger)
            state.db.cancel_task(tid, "Cancelled via chat /cancel command")
            state.db.end_busy_status()
            try:
                from core import computer_use
                computer_use.cancel_for_task(tid, reason="Cancelled via chat /cancel command")
            except Exception as e:
                state.logger.warning(f"[WebHUD] Failed to cancel computer use requests: {e}")
            cancel_reply = f"Active task [{tid}] ('{task_to_cancel.get('title')}') was cancelled."
            try:
                mem = state.get_memory()
                mem.add_message("user", user_msg)
                mem.add_message("assistant", cancel_reply)
            except Exception as mem_err:
                state.logger.debug(f"[WebHUD] Memory recording skipped for /cancel: {mem_err}")
            return ChatResponse(
                success=True,
                reply=cancel_reply,
                model=requested_model,
                agent_status=common.determine_agent_status(),
                task_id=tid
            )
        return ChatResponse(
            success=True,
            reply="No tasks are currently running to cancel.",
            model=requested_model,
            agent_status=common.determine_agent_status(),
            task_id=None
        )

    # Images: made by the separate ImageGen tool
    image_reply = _image_reply(user_msg)
    if image_reply is not None:
        return image_reply

    # Attached documents: their text goes beside this turn's message; the history keeps a note, not the text
    attached = []
    # One SCENE_WAIT for all of them: each image waited up to 50 s in turn, so three passed DeskAI's wait (audit 2.8)
    scene_deadline = time.time() + SCENE_WAIT
    for attachment_id in payload.attachments:
        rec = documents.load(attachment_id)
        if rec is None:
            # 410, not 404: DeskAI reads a 404 from /api/chat as "the chat route isn't there" and shows setup advice
            # (2026-10-28 audit 8.1); for other codes it shows this detail
            raise HTTPException(status_code=410, detail=f"That attachment has expired (uploads are kept "
                                                        f"{documents.KEEP_DAYS} days): attach it again.")
        if rec.get("kind") == "image" and not rec.get("scene") and not rec.get("scene_error"):
            # asked right after sending it
            rec = vision.wait_for_scene(attachment_id, max(0.0, scene_deadline - time.time())) or rec
        attached.append(rec)
    if attached:
        user_msg += "\n[attached: " + ", ".join(f"{r['name']} (id {r['id']})" for r in attached) + "]"

    # 2. Conversational Turn through MemoryManager and LLM
    try:
        mem = state.get_memory()
        stats = state.db.get_stats()
        phase = stats.get("overall_phase", "idle")
        status = stats.get("daemon_status", "idle")
        engine_val = stats.get("default_engine", "auto")
        in_prog = state.db.list_tasks(limit=1, phase="in_progress")
        active_task_str = f"[{in_prog[0]['id']}] '{in_prog[0]['title']}' (running via {in_prog[0].get('engine', 'auto')})" if in_prog else "None"

        # Show the 4 most recent tasks so assistant is always aware of recent background jobs and their outcomes
        recent_tasks = state.db.list_tasks(limit=4)
        task_lines = []
        for t in recent_tasks:
            t_phase = t.get("phase", "unknown")
            t_engine = t.get("engine_used") or t.get("engine") or "auto"
            t_err = f", error: {t.get('last_error')[:50]}" if t.get("last_error") else ""
            task_lines.append(f"  • Task {t['id']} [{t_engine}]: \"{t.get('title')}\" -> {t_phase}{t_err}")
        recent_tasks_block = "\n".join(task_lines) if task_lines else "  (none)"

        # Check latest generated image from output_images
        recent_image_note = ""
        try:
            recent_imgs = sorted(state.IMAGES_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)[:2]
            if recent_imgs:
                recent_image_note = f"\n- Recent generated images on disk: {', '.join(p.name for p in recent_imgs)}"
        except Exception:
            pass

        context = (
            f"- Daemon status: {status}; project phase: {phase}; default engine: {engine_val}\n"
            f"- Active task: {active_task_str}\n"
            f"- Recent background tasks:\n{recent_tasks_block}"
            f"{recent_image_note}\n"
            f"- Background work is started with `/task <instruction>`."
        )
        try:
            from core import away_shift
            bg_line = away_shift.get_status_line(state.db)
            if bg_line:
                context += f"\n- {bg_line}"
        except Exception:
            pass
        try:
            from core import social_posts
            pins_note = social_posts.chat_note()
            if pins_note:
                context += "\n" + pins_note
        except Exception:
            pass
        if payload.conversation_id:
            context += f"\n- Client conversation ID: {payload.conversation_id}"
        if attached:
            context += "\n\n" + documents.chat_context(attached)

        tools = AssistantTools(state.db, mem, state.WORKSPACE_DIR, agentwork=state.get_agentwork(), user_text=user_msg,
                               source="DeskAI chat")
        agent_reply = None
        try:
            with maintenance.chat_turn():           # a restart waits for this reply to be saved and sent
                agent_reply = mem.respond(user_msg, system_context=context, tools=tools,
                                          cancelled=lambda: _is_cancelled(payload.turn_id))
        except Exception as e:
            state.logger.warning(f"Memory respond error: {e}")
        if _is_cancelled(payload.turn_id):
            # Nobody is waiting for it and it isn't kept (no fallback answer, no proposals either)
            return ChatResponse(success=False, reply="(cancelled)", model="none",
                                agent_status=common.determine_agent_status())

        if not agent_reply:
            # Every model failed (respond already tried them all, Gemini included). Until 2026-10-28 a second, direct
            # Gemini call answered here with no tools, persona or history, and then a canned "set your API keys"
            # (audit 2.1). DeskAI shows `reply` whatever `success` says, so the outage is said in it. Not stored: the
            # user's message is, and the next reply can answer it.
            return ChatResponse(success=False, reply=OUTAGE_REPLY, model="none",
                                agent_status=common.determine_agent_status(), task_id=None)
        proposals = common.store_proposals(tools.proposals) if agent_reply and tools.proposals else []
        question = ({"id": uuid.uuid4().hex[:10], **tools.question} if agent_reply and tools.question else None)
        # Older DeskAI builds read suggestions from this text; it goes once every client uses `proposals`
        if proposals and state.db.get_state("proposal_text_block", "on") == "on":
            proposals_text = "\n".join(f"• `/task {p['instruction']}`" for p in proposals)
            agent_reply += f"\n\nSuggested tasks:\n{proposals_text}"

        # The model that actually answered (the route list falls back when one is overloaded or out of budget)
        actual_model = requested_model
        answered_by = getattr(mem.llm, "last_route", None) if mem.llm else None
        if answered_by:
            actual_model = answered_by
        elif actual_model == "auto" and mem.llm:
            actual_model = getattr(mem.llm, "default_model", "auto")

        return ChatResponse(
            success=True,
            reply=agent_reply,
            model=actual_model,
            agent_status=common.determine_agent_status(),
            # the app shows a live card for it: a task this reply started, or a finished one with its go-live chip
            task_id=tools.started[0] if tools.started else tools.card_task,
            proposals=proposals,
            question=question,
            canvas=tools.canvas_shown if agent_reply else None,      # the app shows a card that opens the draft
        )
    except Exception as e:
        raise _server_error("answering a chat message", e)
