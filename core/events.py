"""
Live events (IDEAS #26): a tiny in-process bus so DeskAI hears about a task the moment its status changes,
instead of asking every 2 s, and so the daemon starts a queued task at once instead of up to 30 s later.

    core/database.py   publishes a task event wherever a task's status is written (created, phase, progress)
    interfaces/hud/events_api.py   GET /api/events streams them to DeskAI as Server-Sent Events
    core/daemon.py     sleeps on DAEMON_WAKE, so new work (or a task finishing) wakes it; 30 s stays the fallback

Everything lives in one process (supervisor.py runs the API and the daemon as threads), so a lock and a few
bounded queues are enough: no broker, no extra service, a few KB of RAM. A task written by another process
(a script) is not announced here; the 30 s heartbeat and DeskAI's polling fallback still catch it.
"""

import queue
import threading
import time
from typing import Any, Dict, Optional

# Per-client backlog. A phone that stops reading (bad signal, app frozen) is dropped when its queue fills, so
# memory never grows with it; DeskAI reconnects and refreshes over plain HTTP.
MAX_QUEUE = 100
MAX_SUBSCRIBERS = 20

# phase in the tasks table -> the status DeskAI is told
PHASE_STATUS = {
    "backlog": "queued",
    "pending": "queued",
    "in_progress": "started",
    "completed": "done",
    "failed": "failed",
}

CANCELLED_EXIT_CODE = 137      # same as core/database.py (not imported: database imports this module)

# Set when there is new work for the daemon (a task queued) or a running task finished; the daemon's sleep
# waits on it (core/daemon.py AutonomousDaemon.run)
DAEMON_WAKE = threading.Event()
# Set when the app opens (no stream before, one now): the daemon runs the proactive cycle at once, so the brief and
# anything held for Alex arrive as he shows up instead of up to 30 minutes later (2026-10-27: timing by presence,
# not the clock; the 2026-10-27 background audit B5)
OWNER_ARRIVED = threading.Event()


def wake_daemon() -> None:
    DAEMON_WAKE.set()


class Subscription:
    """One listener: a bounded queue, and whether the bus dropped it for falling behind."""

    def __init__(self, maxsize: int):
        self.queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=maxsize)
        self.dropped = False

    def get(self, timeout: float) -> Optional[Dict[str, Any]]:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None


class EventBus:
    """Thread-safe publish/subscribe. publish() never blocks the writer (the daemon or a request)."""

    def __init__(self, max_queue: int = MAX_QUEUE, max_subscribers: int = MAX_SUBSCRIBERS):
        self.max_queue = max_queue
        self.max_subscribers = max_subscribers
        self._lock = threading.Lock()
        self._subs: list = []

    def subscribe(self) -> Optional[Subscription]:
        """A new listener, or None when max_subscribers are open (a cap so a buggy client can't pile up streams)."""
        with self._lock:
            if len(self._subs) >= self.max_subscribers:
                return None
            sub = Subscription(self.max_queue)
            self._subs.append(sub)
            return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, event: Dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            try:
                sub.queue.put_nowait(event)
            except queue.Full:
                # Too slow: drop it rather than hold events for it. Its stream ends and the app reconnects.
                sub.dropped = True
                self.unsubscribe(sub)


BUS = EventBus()


class Presence:
    """Whether DeskAI is open on the phone, from its live event streams (docs/archive/PROACTIVE_RESEARCH.md 1j).

    DeskAI opens GET /api/events in MainActivity.onStart and closes it in onStop, so a connected stream means the
    app is on screen. Requests don't count: the push service fetches each new message in the background. While no
    stream is open, away_seconds() grows from the moment the last one closed; that moment is saved (by
    interfaces/hud/events_api.py) and restore()d after a restart, so a restart doesn't reset the clock."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._streams = 0
        self._last_seen = clock()        # until restore(): the start of this process, never "away for days"

    def stream_opened(self) -> None:
        with self._lock:
            arrived = self._streams == 0
            self._streams += 1
            self._last_seen = self._clock()
        if arrived:
            OWNER_ARRIVED.set()
            DAEMON_WAKE.set()

    def stream_closed(self) -> None:
        with self._lock:
            self._streams = max(0, self._streams - 1)
            self._last_seen = self._clock()

    def is_open(self) -> bool:
        with self._lock:
            return self._streams > 0

    def last_seen(self) -> float:
        with self._lock:
            return self._clock() if self._streams else self._last_seen

    def away_seconds(self) -> float:
        """0 while the app is open; otherwise how long since it was last open."""
        with self._lock:
            return 0.0 if self._streams else max(0.0, self._clock() - self._last_seen)

    def restore(self, epoch: Optional[float]) -> None:
        """The saved last-seen time after a restart (ignored while a stream is open or when it's missing)."""
        if not epoch:
            return
        with self._lock:
            if not self._streams:
                self._last_seen = float(epoch)


PRESENCE = Presence()


class Activity:
    """What the agent is doing in the background right now (the away shift, docs/archive/PROACTIVE_RESEARCH.md 1m): DeskAI
    shows a pulsing icon while `active`. GET /api/activity returns snapshot(); each change is also published on the
    live stream as `event: activity`, which older DeskAI builds ignore (they act only on `event: task`)."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {"active": False, "label": "", "step": "", "started_at": None,
                                       "finished_at": None, "found": 0}

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def update(self, state: str, label: str = "", step: str = "", found: Optional[int] = None) -> Dict[str, Any]:
        """state: started | step | finished. Returns the new snapshot."""
        now = self._clock()
        with self._lock:
            s = self._state
            if state == "started":
                s.update(active=True, label=label or "Working in the background", step=step, started_at=now,
                         finished_at=None, found=0)
            elif state == "step":
                s.update(active=True, step=step or s["step"])
                if label:
                    s["label"] = label
            elif state == "finished":
                s.update(active=False, step="", finished_at=now)
                if found is not None:
                    s["found"] = int(found)
            else:
                raise ValueError(f"unknown activity state {state!r}")
            return dict(s)


ACTIVITY = Activity()


def publish_activity(state: str, label: str = "", step: str = "", found: Optional[int] = None) -> None:
    """Records and announces a change in background work. Never raises: the work must not fail because of the bus."""
    try:
        snap = ACTIVITY.update(state, label=label, step=step, found=found)
        BUS.publish({"_event": "activity", "state": state, **snap, "ts": time.time()})
    except Exception:
        pass


def task_event(kind: str, task_id: Any, status: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    event = {"type": kind, "task_id": str(task_id), "status": status or kind, "ts": time.time()}
    event.update({k: v for k, v in extra.items() if v is not None})
    return event


def publish_task(kind: str, task_id: Any, status: Optional[str] = None, **extra: Any) -> None:
    """Announces a task status change. Never raises: a status write must not fail because of the bus."""
    try:
        BUS.publish(task_event(kind, task_id, status, **extra))
        if kind in ("created", "queued"):
            # new work: the daemon picks it up now instead of at its next 30 s heartbeat
            wake_daemon()
    except Exception:
        pass


def publish_phase(task_id: Any, phase: Optional[str], exit_code: Optional[int] = None) -> None:
    """A task's phase was written: tell listeners in DeskAI's words (queued / started / done / failed)."""
    status = PHASE_STATUS.get(str(phase or ""))
    if not status:
        return
    publish_task(status, task_id, status, phase=phase, exit_code=exit_code,
                 cancelled=True if exit_code == CANCELLED_EXIT_CODE and phase == "failed" else None)
