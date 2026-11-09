"""\"Needs you\" requests for AgentComputerUse (core/computer_use/requests.py, moved from core/computer_use_requests.py §2 on 2026-11-04).

When a run needs you (a sign-in, a choice), it writes a request; the app shows it, and
your answer is checked against the request and written back for the waiting tool.

The reminder state (_REMINDER_EVENTS, _REMINDER_SENDERS, _TASK_REQUESTS) lives here
and is re-exported by core.computer_use; the alert module shares the same dicts.
COMPUTER_USE_DIR and ComputerUseError are resolved from core.computer_use at call time
via _cu(), so tests patching them on that module take effect.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("ComputerUse")

def _cu():
    """core.computer_use, resolved at call time so test patches on it take effect."""
    import core.computer_use as _m
    return _m





# ---------------------------------------------------------------------------------------- the owner's answers
# The tool saves each request under its data/requests/<id>.json (+ <id>.png) and waits for data/answers/<id>.json
# secret: a password or code typed in the app; the tool types it straight into the field (the model never sees it).
# The agent never logs, stores (beyond the answer file the tool deletes on reading) or echoes it.
_REMINDER_EVENTS: dict[str, Any] = {}     # request_id -> threading.Event
_REMINDER_SENDERS: dict[str, Any] = {}    # request_id -> send callback
_TASK_REQUESTS: dict[str, Any] = {}       # task_id -> set of request_ids


def _requests_dir():
    return _cu().COMPUTER_USE_DIR / "data" / "requests"


def _answers_dir():
    return _cu().COMPUTER_USE_DIR / "data" / "answers"


def _safe_id(request_id: str) -> str:
    rid = "".join(ch for ch in (request_id or "") if ch.isalnum())
    if not rid or len(rid) > 40:
        raise _cu().ComputerUseError("no such request")
    return rid


def get_request(request_id: str) -> dict[str, Any] | None:
    path = _requests_dir() / f"{_safe_id(request_id)}.json"
    if not path.is_file():
        return None
    try:
        req = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    req["answered"] = (_answers_dir() / f"{req['id']}.json").is_file()
    req["pending"] = not req.get("closed") and not req["answered"]
    return req


def close_request(request_id: str, reason: str = "closed", task_id: str = "",
                  send: Callable | None = None) -> bool:
    """Closes a pending request on disk, terminates its reminder thread, and notifies DeskAI to dismiss."""
    import datetime
    try:
        rid = _safe_id(request_id)
    except (_cu().ComputerUseError, TypeError):
        return False
    ev = _REMINDER_EVENTS.pop(rid, None)
    if ev:
        ev.set()
    registered_send = _REMINDER_SENDERS.pop(rid, None)
    send = send or registered_send

    path = _requests_dir() / f"{rid}.json"
    if not path.is_file():
        return False
    try:
        req = json.loads(path.read_text(encoding="utf-8"))
        if not req.get("closed"):
            req["closed"] = datetime.datetime.now().isoformat(timespec="seconds")
            req["close_reason"] = reason
            path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError, AttributeError):
        pass

    try:
        if send is None:
            try:
                from core import push
                send = push.send
            except ImportError:
                import push  # type: ignore[no-redef]
                send = push.send
        send("needs_you_cancel", "Request dismissed", "", task_id, channel="needs_you",
             extra={"cancel_request_id": rid, "request_id": rid, "reason": reason})
    except Exception as e:
        logger.debug(f"close request notification failed: {e}")
    return True


def cancel_for_task(task_id: str, reason: str = "task cancelled", send: Callable | None = None) -> None:
    """Closes all pending requests and reminder threads associated with a task."""
    if not task_id:
        return
    rids = list(_TASK_REQUESTS.pop(task_id, set()))
    for rid in rids:
        close_request(rid, reason=reason, task_id=task_id, send=send)

    # Clean up any pending request on disk if this task is no longer in progress or no tasks active
    try:
        from core.database import DatabaseManager
        db = DatabaseManager()
        t = db.get_task(task_id)
        active = db.list_tasks(limit=1, phase="in_progress")
        if (t and (t.get("phase") != "in_progress" or t.get("cancelled"))) or not active:
            for req in pending_requests():
                close_request(req["id"], reason=reason, task_id=task_id, send=send)
    except Exception as e:
        logger.debug(f"cancel-for-task cleanup skipped: {e}")


def pending_requests() -> list[dict[str, Any]]:
    """Requests the tool is waiting on, newest first."""
    folder = _requests_dir()
    if not folder.is_dir():
        return []
    out = []
    for p in folder.glob("*.json"):
        req = get_request(p.stem)
        if req and req["pending"]:
            out.append(req)
    return sorted(out, key=lambda r: r.get("created", ""), reverse=True)


def screenshot_path(request_id: str):
    req = get_request(request_id)
    if not req or not req.get("screenshot"):
        return None
    path = _requests_dir() / req["screenshot"]
    return path if path.is_file() else None


def answer(request_id: str, value: str) -> dict[str, Any]:
    """The owner's answer from the app, checked against the request, for the waiting tool."""
    req = get_request(request_id)
    if req is None:
        raise _cu().ComputerUseError("no such request")
    if not req["pending"]:
        raise _cu().ComputerUseError("that request was already answered or has closed")
    if req["kind"] == "secret":
        value = (value or "").strip("\r\n")              # exactly as typed (a password may have spaces)
        if not value or len(value) > 200:
            raise _cu().ComputerUseError("type it (up to 200 characters)")
    else:
        value = (value or "").strip()
    if req["kind"] == "approve":
        if value.lower() not in ("approve", "deny"):
            raise _cu().ComputerUseError("answer Approve or Deny")
        value = value.capitalize()
    elif req["kind"] == "choice":
        if value not in (req.get("choices") or []):
            raise _cu().ComputerUseError("pick one of the choices")
    elif req["kind"] != "secret" and (not value or len(value) > 1000):
        raise _cu().ComputerUseError("type an answer (up to 1,000 characters)")
    _write_answer(req["id"], value)
    return {"id": req["id"], "kind": req["kind"], "answer": "(sent)" if req["kind"] == "secret" else value}


def _write_answer(rid: str, value: str) -> None:
    _answers_dir().mkdir(parents=True, exist_ok=True)
    tmp = _answers_dir() / f"{rid}.json.tmp"
    tmp.write_text(json.dumps({"answer": value}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_answers_dir() / f"{rid}.json")       # the tool never reads half a file
    ev = _REMINDER_EVENTS.pop(rid, None)
    if ev:
        ev.set()
