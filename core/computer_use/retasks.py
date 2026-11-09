"""Retasks and saved passwords for AgentComputerUse (core/computer_use/retasks.py, moved from core/computer_use_retasks.py §7 on 2026-11-04).

Retasks (the same task again, without the AI) and the saved site passwords (Windows
Credential Manager). run() and ComputerUseError are resolved from core.computer_use at
call time via _cu(), so tests patching run on that module take effect.
"""

import re
from typing import Any


def _cu():
    """core.computer_use, resolved at call time so test patches on it take effect."""
    import core.computer_use as _m
    return _m





# ---------------------------------------------------------------------------------------------------- retasks
# A clean run the tool saved, so the SAME task asked again is repeated without the AI (the tool's retasks.py).
# The owner sees them in DeskAI and can delete one.



# ------------------------------------------------------------------------------------------------ credentials
# Stored in Windows Credential Manager under the host desktop session (AgentComputerUse/credentials.py).
# Stored with DPAPI encryption; passwords are never sent in logs, task prompts, or GET API responses.


def list_retasks() -> list[dict[str, Any]]:
    return _cu().run(["retasks"], timeout=60).get("retasks") or []


def forget_retask(rid: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{12}", rid or ""):
        raise _cu().ComputerUseError("that isn't a retask id")
    return bool(_cu().run(["forget-retask", rid], timeout=60).get("forgotten"))


def list_passwords() -> list[dict[str, str]]:
    return _cu().run(["passwords"], timeout=60).get("saved") or []


def save_password(domain: str, username: str, password: str) -> dict[str, Any]:
    if not domain or not username or not password:
        raise _cu().ComputerUseError("domain, username, and password are required")
    if "\n" in password or "\r" in password:
        raise _cu().ComputerUseError("a password can't contain a line break")
    # Saved exactly as given: a leading or trailing space is part of a password (2026-10-27, no strip)
    return _cu().run(["save-password", domain.strip(), username.strip()], input_text=password + "\n", timeout=60)


def forget_password(domain: str, username: str) -> bool:
    if not domain or not username:
        raise _cu().ComputerUseError("domain and username are required")
    return bool(_cu().run(["forget-password", domain.strip(), username.strip()], timeout=60).get("forgotten"))
