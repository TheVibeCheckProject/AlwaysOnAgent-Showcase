"""
Chat §8 — The chat endpoint's turn, plus §2's Stop button state.

chat_endpoint() (in interfaces/hud/chat.py) handles a message from the app; _chat_turn() here builds the
live status and runs the turn (core/memory.py respond()), then returns the reply with any cards, questions
or started task. (Split out of interfaces/hud/chat.py; chat.py re-exports the names other code uses.)
"""

import re
import sqlite3
import threading
import time
import uuid

from fastapi import HTTPException

from core import actions, documents, maintenance, vision
from core.assistant_tools import AssistantTools
from core.daemon import kill_process_tree
from core.task_engines import normalize_engine
from interfaces.hud import common, state
from interfaces.hud.chat_images import SCENE_WAIT, _image_reply
from interfaces.hud.chat_secrets import _password_reply
from interfaces.hud.chat_signin import _connect_google_reply, _google_signin_reply, _zoho_signin_reply
from interfaces.hud.models import ChatRequest, ChatResponse, ProposalOut, QuestionOut

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
# §2  THE STOP BUTTON
#     chat_cancel marks a turn stopped; the turn checks it and neither stores nor sends the reply.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
# Turns the user stopped in the app: their reply is dropped, not kept in memory (2026-10-05: a cancelled reply
# about a misspelt search still landed in the history). turn_id -> when it was cancelled.
_cancelled: dict[str, float] = {}
_cancelled_lock = threading.Lock()
CANCEL_KEEP = 3600     # seconds a cancellation is remembered (a turn never runs that long)


def _is_cancelled(turn_id: str | None) -> bool:
    if not turn_id:
        return False
    with _cancelled_lock:
        return turn_id in _cancelled


def _server_error(doing: str, e: Exception) -> HTTPException:
    """A 500 that says something went wrong and where to look, not the exception's own text (paths, SQL, library
    messages; 2026-10-28 audit 2.9). The full error is in the log under the same short id."""
    ref = uuid.uuid4().hex[:8]
    state.logger.exception(f"Error {doing} [{ref}]: {e}")
    return HTTPException(status_code=500, detail=f"Something went wrong {doing} (error {ref}; the details are in the log).")


OUTAGE_REPLY = ("⚠️ The AI services aren't answering right now (busy or down). Your message is saved: try again in a "
                "few minutes and I'll pick it up.")

# One answer per message: DeskAI resends a message to its other address (home Wi-Fi <-> Tailscale) when the first
# connection fails, even after this server already took it, so the same turn_id can arrive twice. The repeat waits for
# the first run and gets its answer, instead of storing and answering the message again (docs/AUDIT_2026-10-27.md R1).
_turns: dict[str, dict] = {}
_turns_lock = threading.Lock()
TURN_KEEP = 600          # seconds a finished turn's answer is kept for a late repeat
TURN_WAIT = 330          # how long a repeat waits for the first run (DeskAI's longest read timeout)
COMMAND_TASK = re.compile(r"/task(\s|$)", re.IGNORECASE)
COMMAND_CANCEL = re.compile(r"/cancel(\s|$)", re.IGNORECASE)


def _requested_model(payload: ChatRequest) -> str:
    return payload.model if payload.model and payload.model != "auto" else (
        payload.engine if payload.engine and payload.engine != "auto" else "auto"
    )


def _status_reply(user_msg: str, requested_model: str) -> ChatResponse | None:
    """The /status quick command: daemon, phase, engine, active task, completed count, memory facts."""
    if user_msg.lower() != "/status":
        return None
    stats = state.db.get_stats()
    status = stats.get("daemon_status", "idle")
    phase = stats.get("overall_phase", "idle")
    engine = stats.get("default_engine", "auto")
    in_prog = state.db.list_tasks(limit=1, phase="in_progress")
    active_title = in_prog[0]["title"] if in_prog else "None"
    try:
        mem = state.get_memory()
        facts_count = mem.stats()["facts_active"] if mem else 0
    except (OSError, sqlite3.Error):
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


def _task_command_reply(user_msg: str, payload: ChatRequest, requested_model: str) -> ChatResponse | None:
    """The /task quick command: enqueues a background task, or asks for the prompt when there is none."""
    if not COMMAND_TASK.match(user_msg):
        return None
    # The command word itself: "/tasks" was queued as the task "s" (2026-10-28 audit 2.7)
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


def _restart_reply(user_msg: str, requested_model: str) -> ChatResponse | None:
    """The /restart quick command: schedules a restart of the agent."""
    if user_msg.lower() != "/restart":
        return None
    restart = common.schedule_restart("chat /restart")
    return ChatResponse(
        success=True,
        reply=f"🔄 {restart['message']} Back shortly after that.",
        model=requested_model,
        agent_status="idle",
        task_id=None
    )


def _cancel_command_reply(user_msg: str, requested_model: str) -> ChatResponse | None:
    """The /cancel quick command: kills the running task's process tree and cancels it."""
    if not COMMAND_CANCEL.match(user_msg):
        return None
    in_prog = state.db.list_tasks(limit=1, phase="in_progress")
    if not in_prog:
        return ChatResponse(
            success=True,
            reply="No tasks are currently running to cancel.",
            model=requested_model,
            agent_status=common.determine_agent_status(),
            task_id=None
        )
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


def _self_check_reply(user_msg: str) -> ChatResponse | None:
    """The self-check's drafted fix (core/self_check.py): only the owner's own "start the fix" / "skip the fix"
    acts on it, caught here so no model ever starts a change to the agent by itself."""
    from core import self_check
    fix = self_check.answer(state.db, user_msg, source="DeskAI chat")
    if fix is None:
        return None
    try:
        mem = state.get_memory()
        mem.add_message("user", user_msg)
        mem.add_message("assistant", fix["reply"])
    except Exception as mem_err:
        state.logger.debug(f"[WebHUD] Memory recording skipped for the self-check fix: {mem_err}")
    return ChatResponse(success=True, reply=fix["reply"], model="system",
                        agent_status=common.determine_agent_status(), task_id=fix["task_id"])


def _load_attachments(payload: ChatRequest, scene_wait: float) -> list[dict]:
    """Attached documents: their text goes beside this turn's message; the history keeps a note, not the text."""
    attached = []
    # One SCENE_WAIT for all of them: each image waited up to 50 s in turn, so three passed DeskAI's wait (audit 2.8)
    scene_deadline = time.time() + scene_wait
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
    return attached


def _recent_tasks_block() -> str:
    """The 4 most recent tasks, so the assistant is always aware of recent background jobs and their outcomes."""
    recent_tasks = state.db.list_tasks(limit=4)
    task_lines = []
    for t in recent_tasks:
        t_phase = t.get("phase", "unknown")
        t_engine = t.get("engine_used") or t.get("engine") or "auto"
        t_err_text = t.get("last_error")
        t_err = f", error: {t_err_text[:50]}" if t_err_text else ""
        task_lines.append(f"  • Task {t['id']} [{t_engine}]: \"{t.get('title')}\" -> {t_phase}{t_err}")
    return "\n".join(task_lines) if task_lines else "  (none)"


def _recent_image_note() -> str:
    """The latest generated images on disk, so the assistant knows what was just made."""
    try:
        recent_imgs = sorted(state.IMAGES_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)[:2]
        if recent_imgs:
            return f"\n- Recent generated images on disk: {', '.join(p.name for p in recent_imgs)}"
    except OSError:
        pass
    return ""


def _optional_context_notes() -> str:
    """Extra live lines for the context: away-shift status, pinned social posts, a drafted self-check fix."""
    notes = ""
    try:
        from core import away_shift
        bg_line = away_shift.get_status_line(state.db)
        if bg_line:
            notes += f"\n- {bg_line}"
    except (ImportError, OSError, sqlite3.Error):
        pass
    try:
        from core import social_posts
        pins_note = social_posts.chat_note()
        if pins_note:
            notes += "\n" + pins_note
    except ImportError:
        pass
    try:
        from core import self_check
        fix_note = self_check.chat_note(state.db)
        if fix_note:
            notes += "\n" + fix_note
    except (OSError, sqlite3.Error, KeyError, TypeError):
        pass
    return notes


def _build_context(payload: ChatRequest, attached: list[dict]) -> str:
    """The live status the assistant sees with every turn: daemon, tasks, images, notes, attachments."""
    stats = state.db.get_stats()
    phase = stats.get("overall_phase", "idle")
    status = stats.get("daemon_status", "idle")
    engine_val = stats.get("default_engine", "auto")
    in_prog = state.db.list_tasks(limit=1, phase="in_progress")
    active_task_str = (f"[{in_prog[0]['id']}] '{in_prog[0]['title']}' (running via {in_prog[0].get('engine', 'auto')})"
                       if in_prog else "None")

    context = (
        f"- Daemon status: {status}; project phase: {phase}; default engine: {engine_val}\n"
        f"- Active task: {active_task_str}\n"
        f"- Recent background tasks:\n{_recent_tasks_block()}"
        f"{_recent_image_note()}\n"
        f"- Background work is started with `/task <instruction>`."
    )
    context += _optional_context_notes()
    if payload.conversation_id:
        context += f"\n- Client conversation ID: {payload.conversation_id}"
    if attached:
        context += "\n\n" + documents.chat_context(attached)
    else:
        earlier = documents.earlier_attachments(state.db)
        if earlier:
            context += "\n\n" + documents.chat_context(earlier, earlier=True)
    return context


def _respond(mem, user_msg: str, context: str, tools: AssistantTools, turn_id: str | None) -> str | None:
    """One turn through the assistant; None when every model failed."""
    agent_reply = None
    try:
        with maintenance.chat_turn():           # a restart waits for this reply to be saved and sent
            agent_reply = mem.respond(user_msg, system_context=context, tools=tools,
                                      cancelled=lambda: _is_cancelled(turn_id))
    except Exception as e:
        state.logger.warning(f"Memory respond error: {e}")
    return agent_reply


def _finalize_reply(agent_reply: str, tools: AssistantTools, mem, requested_model: str) -> ChatResponse:
    """Proposals, the clarifying question, the model that actually answered, and the response."""
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
        proposals=[ProposalOut(**p) for p in proposals],
        question=QuestionOut(**question) if question else None,
        canvas=tools.canvas_shown if agent_reply else None,      # the app shows a card that opens the draft
    )


def _run_assistant_turn(payload: ChatRequest, user_msg: str, requested_model: str,
                        attached: list[dict]) -> ChatResponse:
    """The conversational turn: live status as context, then core/memory.py respond() with the tools."""
    try:
        mem = state.get_memory()
        context = _build_context(payload, attached)
        tools = AssistantTools(state.db, mem, state.WORKSPACE_DIR, agentwork=state.get_agentwork(),
                               user_text=user_msg, source="DeskAI chat")
        agent_reply = _respond(mem, user_msg, context, tools, payload.turn_id)
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
        return _finalize_reply(agent_reply, tools, mem, requested_model)
    except Exception as e:
        raise _server_error("answering a chat message", e)


def _chat_turn(payload: ChatRequest, response, scene_wait: float = SCENE_WAIT) -> ChatResponse:
    """One turn: anything that must never reach a model is caught first (sign-ins, passwords: §3, §7), then the
    quick commands, /image (§5), the self-check's "start the fix", attachments (§6) -> the live status, then
    core/memory.py respond() with the tools.

    `scene_wait` is the chat module's SCENE_WAIT (passed by chat_endpoint, so tests patching
    interfaces.hud.chat.SCENE_WAIT control it); the default is the same value from chat_images.
    """
    user_msg = payload.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Missing or empty message field")

    requested_model = _requested_model(payload)

    signin_reply = (_connect_google_reply(user_msg) or _google_signin_reply(user_msg) or _zoho_signin_reply(user_msg)
                    or _password_reply(user_msg))
    if signin_reply is not None:
        return signin_reply

    # 1. Built-in Agent Quick Commands (in order: the first one that answers wins, like the original's
    # sequential ifs — the steps must not all run, e.g. self_check.answer must not see a /task message)
    reply = _status_reply(user_msg, requested_model)
    if reply is not None:
        return reply
    reply = _task_command_reply(user_msg, payload, requested_model)
    if reply is not None:
        return reply
    reply = _restart_reply(user_msg, requested_model)
    if reply is not None:
        return reply
    reply = _cancel_command_reply(user_msg, requested_model)
    if reply is not None:
        return reply
    reply = _self_check_reply(user_msg)
    if reply is not None:
        return reply

    # Images: made by the separate ImageGen tool
    image_reply = _image_reply(user_msg)
    if image_reply is not None:
        return image_reply

    # Attached documents: their text goes beside this turn's message; the history keeps a note, not the text
    attached = _load_attachments(payload, scene_wait)
    if attached:
        user_msg += "\n[attached: " + ", ".join(f"{r['name']} (id {r['id']})" for r in attached) + "]"

    # 2. Conversational Turn through MemoryManager and LLM
    return _run_assistant_turn(payload, user_msg, requested_model, attached)
