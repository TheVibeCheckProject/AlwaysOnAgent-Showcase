"""
Chat §7 — Sign-in replies.

The replies for §3's catches (interfaces/hud/chat_secrets.py): finishing the Google or Zoho connection
without a model. (Split out of interfaces/hud/chat.py; chat.py re-exports the names other code uses.)
"""

import re
import sqlite3

from core import connectors
from interfaces.hud import common, state
from interfaces.hud.chat_secrets import GOOGLE_SIGNIN, ZOHO_CODE
from interfaces.hud.models import ChatResponse

# "connect google" is answered here with the real link, never by a model (2026-10-11: a model made up a sign-in
# link with the wrong permissions instead of calling connect_google, so pasting the address back failed)
CONNECT_GOOGLE = re.compile(r"^\s*(please\s+)?(re-?)?connect\s+(to\s+)?(my\s+)?google(\s+account)?(\s+again)?\s*[.!]*\s*$", re.IGNORECASE)


def _connect_google_reply(user_msg: str) -> ChatResponse | None:
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
    except (OSError, sqlite3.Error):
        pass
    return ChatResponse(success=ok, reply=reply, model="system", agent_status=common.determine_agent_status(),
                        task_id=None)


def _zoho_signin_reply(user_msg: str) -> ChatResponse | None:
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
    except (OSError, sqlite3.Error):
        pass
    return ChatResponse(success=ok, reply=reply, model="system", agent_status=common.determine_agent_status(),
                        task_id=None)


def _google_signin_reply(user_msg: str) -> ChatResponse | None:
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
    except (OSError, sqlite3.Error):
        pass
    return ChatResponse(success=ok, reply=reply, model="system", agent_status=common.determine_agent_status(),
                        task_id=None)
