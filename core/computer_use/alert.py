"""The "Needs you" alert for AgentComputerUse (core/computer_use/alert.py, moved from core/computer_use_alert.py §4 on 2026-11-04).

The alert on its own DeskAI channel, repeated until you answer or the run gives up.
REMINDERS lives here and is re-exported by core.computer_use.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from .requests import (
    _REMINDER_EVENTS,
    _REMINDER_SENDERS,
    _TASK_REQUESTS,
    close_request,
    get_request,
)

logger = logging.getLogger("ComputerUse")



REMINDERS = (60, 60, 60, 60, 60, 600, 600)     # seconds between alerts; the tool pauses after 30 minutes anyway


def alert_text(req: dict[str, Any]) -> tuple:
    site = ""
    if req.get("url"):
        site = req["url"].split("//", 1)[-1].split("/", 1)[0]
    where = f" ({site})" if site else ""
    q = (req.get("question") or "").strip()
    if req.get("kind") == "approve":
        return f"Approve?{where}", q
    if req.get("kind") == "secret":
        return f"Type it in DeskAI{where}", f"{q}. Open DeskAI to type it; it goes straight into the box, not to the AI."
    if req.get("kind") == "choice":
        ql = q.lower()
        if "phone" in ql or "sign" in ql or "google" in ql or "password" in ql:
            return f"Sign-in verification needed{where}", q
        return f"A choice is needed{where}", q
    return f"The computer task needs you{where}", q


def _remind_loop(rid, task_id, send, sleep, custom_sleep, stop_event, title, body, extra, db) -> None:
    """Send the "Needs you" reminder on the REMINDERS schedule until the owner answers, the request closes,
    or its task goes inactive. Runs on its own daemon thread; always cleans up the reminder registries."""
    try:
        for n, wait in enumerate(REMINDERS, 1):
            if custom_sleep:
                sleep(wait)
                if stop_event.is_set():
                    return
            else:
                if stop_event.wait(wait):
                    return
            current = get_request(rid)
            if not current or not current.get("pending"):
                return
            if task_id:
                try:
                    database = db
                    if database is None:
                        from core.database import DatabaseManager
                        database = DatabaseManager()
                    t = database.get_task(task_id)
                    if t and t.get("engine") == "computer" and (t.get("phase") in ("completed", "failed", "cancelled") or t.get("cancelled")):
                        close_request(rid, reason="task inactive", task_id=task_id, send=send)
                        return
                except Exception as e:
                    logger.debug(f"needs-you task check failed: {e}")
            send("needs_you", "Reminder: " + title, body, task_id, channel="needs_you", extra={**extra, "reminder": str(n)})
    finally:
        if rid:
            _REMINDER_EVENTS.pop(rid, None)
            _REMINDER_SENDERS.pop(rid, None)
            if task_id and task_id in _TASK_REQUESTS:
                _TASK_REQUESTS[task_id].discard(rid)


def alert_owner(req: dict[str, Any], task_id: str = "", send: Callable | None = None,
                sleep: Callable[[float], None] | None = None, db: Any = None) -> None:
    """The loud "Needs you" alert (its own DeskAI channel) with the request's screenshot, then reminders until
    the owner answers or the request closes. The screenshot is fetched by the app from
    /api/computer/requests/<id>/screenshot (the push itself carries only text)."""
    import threading
    import time as _time
    if send is None:
        try:
            from core import push
        except ImportError:
            import push  # type: ignore[no-redef]
        send = push.send
    custom_sleep = sleep is not None
    sleep = sleep or _time.sleep
    title, body = alert_text(req)
    rid = req.get("id")
    extra = {"request_id": rid or "", "request_kind": req.get("kind", ""),
             "choices": json.dumps(req.get("choices") or [], ensure_ascii=False),
             "screenshot": f"/api/computer/requests/{rid}/screenshot" if req.get("screenshot") else "",
             "url": req.get("url") or ""}
    send("needs_you", title, body, task_id, channel="needs_you", extra=extra)

    stop_event = threading.Event()
    if rid:
        _REMINDER_EVENTS[rid] = stop_event
        if send:
            _REMINDER_SENDERS[rid] = send
    if task_id and rid:
        _TASK_REQUESTS.setdefault(task_id, set()).add(rid)

    threading.Thread(target=_remind_loop, args=(rid, task_id, send, sleep, custom_sleep, stop_event, title, body, extra, db),
                     daemon=True, name=f"needs-you-{rid}").start()
