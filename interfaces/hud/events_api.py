"""
Live events (IDEAS #26): GET /api/events, a Server-Sent Events stream of task status changes for DeskAI.

Each change is one `event: task` with a JSON line: {"type", "task_id", "status", "phase", "exit_code", ...}
(see docs/API.md). The app refreshes that one task with GET /api/tasks/{id} when it hears of it, and falls back
to its old polling when the stream drops. Auth is HUDAuthMiddleware's like every other endpoint (it is pure
ASGI, so streaming is untouched). The events come from core/events.py's in-process bus.
"""

import asyncio
import json
import time
from typing import AsyncIterator, Callable, Awaitable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from core import events
from interfaces.hud import state

router = APIRouter()

# A comment line this often keeps phones, Wi-Fi routers and Tailscale from closing a quiet connection
HEARTBEAT_SECONDS = 15.0
# How often the stream looks for new events; a plain non-blocking check, so no thread per client
POLL_SECONDS = 0.25
RETRY_MS = 5000          # EventSource clients wait this long before reconnecting


def format_event(event: dict) -> str:
    """`event: task` for task changes (byte-for-byte as before); background work (`_event: activity`) gets its own
    event name, which older DeskAI builds ignore."""
    name = event.get("_event", "task")
    if name != "task":
        event = {k: v for k, v in event.items() if k != "_event"}
    return f"event: {name}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"


async def event_stream(sub: "events.Subscription", is_disconnected: Callable[[], Awaitable[bool]],
                       heartbeat: float = HEARTBEAT_SECONDS, poll: float = POLL_SECONDS) -> AsyncIterator[str]:
    """Yields SSE text for one subscriber until the client leaves or the bus drops it for falling behind."""
    yield f"retry: {RETRY_MS}\n: connected\n\n"
    last_beat = time.monotonic()
    last_check = last_beat
    opened = False
    try:
        # DeskAI holds this stream exactly while it's on screen: the agent's sign that the owner has the app open
        # (docs/archive/PROACTIVE_RESEARCH.md 1j). Counted inside the try, so every open is matched by the close below.
        events.PRESENCE.stream_opened()
        opened = True
        _save_last_seen()
        while True:
            sent = False
            while True:
                try:
                    event = sub.queue.get_nowait()
                except Exception:
                    break
                yield format_event(event)
                sent = True
            if sub.dropped:
                break                       # too slow: the app reconnects and refreshes over plain HTTP
            now = time.monotonic()
            if sent:
                last_beat = now
            elif now - last_beat >= heartbeat:
                yield ": ping\n\n"
                last_beat = now
            if now - last_check >= 1.0:
                last_check = now
                if await is_disconnected():
                    break
            await asyncio.sleep(poll)
    except (asyncio.CancelledError, GeneratorExit):
        pass
    finally:
        events.BUS.unsubscribe(sub)
        if opened:
            events.PRESENCE.stream_closed()
            _save_last_seen()


APP_LAST_SEEN = "app_last_seen"     # system_state: epoch seconds the app was last open (restored after a restart)


def _save_last_seen() -> None:
    """Never raises: a stream must not fail because the database is busy."""
    try:
        state.db.set_state(APP_LAST_SEEN, f"{events.PRESENCE.last_seen():.0f}")
    except Exception:
        pass


@router.get("/api/activity")
def background_activity():
    """Background work now (the away shift): {"active", "label", "step", "started_at", "finished_at", "found"}, so a
    freshly opened app knows whether to show its pulsing icon without waiting for an `event: activity`."""
    return events.ACTIVITY.snapshot()


@router.get("/api/events")
async def live_events(request: Request):
    """Task status changes the moment they happen (created, queued, started, progress, done, failed)."""
    sub = events.BUS.subscribe()
    if sub is None:
        raise HTTPException(status_code=503, detail="Too many live event streams open; poll instead")
    state.logger.debug("DeskAI connected to /api/events")
    return StreamingResponse(
        event_stream(sub, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
