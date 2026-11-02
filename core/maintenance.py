"""
Drain before a restart (docs/IDEAS.md #23)
==========================================
A restart stops whatever task is running, and a schedule can start one in the minute between "the queue is idle"
and the restart (a deploy's test run). So every restart goes through here: new work is held (no queued task
starts, no schedule fires), the running task finishes, then the agent exits with code 3 and run_forever.ps1
starts it again. Schedules that came due while held fire after the restart (missed-run rules: within 2 hours
they still run). The hold lives in memory only, so the restart itself releases it.
"""

import threading
import time
from typing import Any, Callable, Dict, List

DRAIN_MAX = 30 * 60        # longest wait for a running task; after that the restart goes ahead anyway
POLL_SECONDS = 5
RESPONSE_GRACE = 0.5       # lets the HTTP response that asked for the restart go out first

HOLD = threading.Event()
_waiter: threading.Thread = None


def holding() -> bool:
    """True while a restart waits: the daemon starts no queued task and the schedule runner fires nothing."""
    return HOLD.is_set()


def running_titles(db) -> List[str]:
    return [t.get("title") or t.get("id") for t in (db.list_tasks(phase="in_progress") or [])]


# Chat replies being written. A restart waits for them too (CHAT_DRAIN_MAX at most): 2026-10-06 a deploy restarted
# the agent 30 s after a reply was ready but before it was saved and sent, and the owner never got it.
CHAT_DRAIN_MAX = 120
_chats = 0
_chats_lock = threading.Lock()


class chat_turn:
    """`with chat_turn():` around writing one chat reply."""

    def __enter__(self):
        global _chats
        with _chats_lock:
            _chats += 1
        return self

    def __exit__(self, *exc):
        global _chats
        with _chats_lock:
            _chats -= 1
        return False


def chats_in_flight() -> int:
    with _chats_lock:
        return _chats


def restart_when_idle(db, reason: str, now: bool = False, exit_fn: Callable[[], None] = None) -> Dict[str, Any]:
    """Holds new work and restarts once nothing is running (at once with now=True). Returns what it's doing."""
    global _waiter
    exit_fn = exit_fn or _exit_for_relaunch
    HOLD.set()                                  # before looking, so nothing starts between the look and the exit
    waiting = [] if now else running_titles(db)
    if _waiter is not None and _waiter.is_alive() and not now:
        return _answer(waiting)                 # a restart is already waiting; this one joins it
    if waiting:
        db.append_log("WARNING", f"Restart requested ({reason}); waiting for {', '.join(waiting)} to finish. "
                                 "New tasks and schedules are held until then.")
    else:
        db.append_log("WARNING", f"Restart requested ({reason}).")

    def wait_then_exit():
        time.sleep(RESPONSE_GRACE)
        deadline = time.time() + DRAIN_MAX
        while not now and running_titles(db) and time.time() < deadline:
            time.sleep(POLL_SECONDS)
        left = [] if now else running_titles(db)
        if left:
            db.append_log("WARNING", f"Restarting anyway: {', '.join(left)} still running after "
                                     f"{DRAIN_MAX // 60} minutes.")
        chat_deadline = time.time() + CHAT_DRAIN_MAX
        while not now and chats_in_flight() and time.time() < chat_deadline:
            time.sleep(1)                       # a reply being written is saved and sent first
        exit_fn()

    _waiter = threading.Thread(target=wait_then_exit, name="RestartDrain", daemon=True)
    _waiter.start()
    return _answer(waiting)


def _answer(waiting: List[str]) -> Dict[str, Any]:
    if not waiting:
        return {"status": "restarting", "message": "Restarting now."}
    return {"status": "draining", "waiting_for": waiting,
            "message": f"Restarting once {', '.join(waiting)} finishes. Nothing new starts until then."}


def _exit_for_relaunch() -> None:
    import os
    os._exit(3)                                 # run_forever.ps1 relaunches on 3
