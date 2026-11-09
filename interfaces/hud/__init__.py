"""
The API server DeskAI talks to, one module per area:

    server.py       builds the app: media folders, access control, and every module's routes
    state.py        shared state (database, memory, AgentWork, folders, token); tests swap these
    auth.py         access control: loopback, or the HUD_AUTH_TOKEN
    models.py       request and response bodies
    common.py       helpers the modules share (task formatting, request de-duplication, suggestions, restart)
    companion.py    health / status, suggested tasks, AgentWork, push, backup, cleanup, restart
    tasks.py        the task queue, video jobs, the daemon's state, the live stream, the log
    events_api.py   /api/events: task status changes pushed the moment they happen (core/events.py)
    media.py        videos and images for the DeskAI gallery
    memory_api.py   the assistant's memory
    schedules_api.py  scheduled videos, reminders and tasks
    watches_api.py  mail watches (DeskAI's Watches tab)
    chat.py         the chat (router, Stop button, image jobs, POST /api/chat); the work is split across
                    chat_secrets.py (§3 sign-ins/passwords), chat_refine.py (§4), chat_images.py (§5),
                    chat_attachments.py (§6), chat_signin.py (§7), chat_turn.py (§8, the turn itself)
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from interfaces.hud.server import app, run_hud

__all__ = ["app", "run_hud"]
