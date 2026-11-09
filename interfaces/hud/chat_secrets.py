"""
Chat §3 — Sign-ins and passwords.

A Google sign-in address, Zoho's Self Client details, or something that looks like a password is caught
here, never sent to a model, never stored as typed. (Split out of interfaces/hud/chat.py; chat.py
re-exports the names other code uses.)
"""

import re
import sqlite3

from interfaces.hud import common, state
from interfaces.hud.models import ChatResponse

# The address the owner's phone lands on after approving Google (connect_google): it carries a one-time code, so it
# goes straight to Connectors and never to an AI model, the chat history or memory.
GOOGLE_SIGNIN = re.compile(r"^\s*(https?://)?(localhost|127\.0\.0\.1)[:/][^\s]*[?&](code|error)=", re.IGNORECASE)

# Zoho's Self Client details (connect_zoho): the Client ID and the one-time code are distinctive. The message holds
# the client secret, so it never reaches an AI model, the chat history or memory either. Either piece alone is
# caught too, and no word boundaries: the owner's first paste was the three run together (2026-10-06), and the
# boundaries let it through to the model.
ZOHO_CODE = re.compile(r"1000\.([A-Z0-9]{30}|[0-9a-f]{32}\.[0-9a-f]{32})")


def _holds_signin(text: str) -> bool:
    """A Google sign-in address or Zoho Self Client details anywhere in the text (a draft can hold more than the
    paste). Same rule as the chat: these never reach a model (GOOGLE_SIGNIN, ZOHO_CODE below)."""
    text = text or ""
    return bool(GOOGLE_SIGNIN.match(text) or ZOHO_CODE.search(text) or holds_password(text)
                or re.search(r"(localhost|127\.0\.0\.1)[:/]\S*[?&](code|error)=", text, re.IGNORECASE))


# A password typed into the chat (2026-10-26: "The password is <it>" went to the cloud model, whose free tier may
# use prompts for training). Caught before any model, like the sign-ins: never sent, never stored. A word after
# "password", "pw", "pin"... counts only if it looks like a secret (AgentComputerUse's activity.secret_like), or, after
# ":" or "=", is 6+ characters: "the password is going to be typed in" is a sentence, not a password.
PASSWORD_WORD = re.compile(r"\b(?:pass(?:word|wd|code|phrase)?|pw|pwd|pin)\b\s*(?P<sep>is\b|was\b|[:=])?\s*"
                           r"[\"'`“‘]?(?P<value>[^\s\"'`”’]+)", re.IGNORECASE)


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


def _password_reply(user_msg: str) -> ChatResponse | None:
    if not holds_password(user_msg):
        return None
    try:
        mem = state.get_memory()
        mem.add_message("user", "(a message holding a password, hidden)")
        mem.add_message("assistant", PASSWORD_REPLY)
    except (OSError, sqlite3.Error):
        pass
    return ChatResponse(success=True, reply=PASSWORD_REPLY, model="system",
                        agent_status=common.determine_agent_status(), task_id=None)
