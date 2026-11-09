"""
Calling AgentComputerUse
========================
AgentComputerUse (COMPUTER_USE_DIR, a separate tool) uses the computer like a person: it opens its own Edge,
reads pages and apps through Windows' accessibility list plus OCR, and acts only with the real mouse and
keyboard. The agent runs it as its own program from inside the desktop session (the agent's own session),
reads the JSON lines it prints (progress to the task card, the last line is the result), and a cancel ends
the program. It never submits, sends, pays or posts on its own: those stop for the owner.

MAP OF THIS PACKAGE
-------------------
§1 lives here (setup, the error type, is it installed). The sections moved into this
package on 2026-11-04, one file per section — every name is re-exported here, so
`from core import computer_use` and `computer_use.<name>` keep working exactly as before.
The old flat files (core/computer_use.py, core/computer_use_<name>.py) were thin shims
re-exporting from this package; they were removed 2026-11-04.

  §1   Setup                         this file: imports, the tool's folder, the error type, is it installed
  §2   "Needs you" requests          core/computer_use/requests.py
  §3   Take over                     core/computer_use/takeover.py
  §4   The "Needs you" alert         core/computer_use/alert.py
  §5   Running the tool              core/computer_use/run.py
  §6   Runs and screenshots          core/computer_use/screenshots.py
  §7   Retasks and saved passwords   core/computer_use/retasks.py
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §1  SETUP
#     Imports, where AgentComputerUse lives, and whether it's installed.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
import json
import logging
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

try:
    from core.config import COMPUTER_USE_DIR, get_safe_subprocess_env
except ImportError:
    from config import COMPUTER_USE_DIR, get_safe_subprocess_env  # type: ignore[no-redef]

logger = logging.getLogger("ComputerUse")
# It needs to find Edge and run in the owner's profile; it reads the model keys from the agent's .env itself
DESKTOP_ENV = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles", "ProgramData",
               "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "USERNAME", "USERDOMAIN", "USERPROFILE",
               "LOCALAPPDATA", "APPDATA", "SystemRoot", "windir", "TEMP", "TMP")


class ComputerUseError(Exception):
    """The tool reported an error (its message is written for the owner)."""


def installed() -> bool:
    return (COMPUTER_USE_DIR / "agentcomputeruse.py").is_file()


ANSWER_KINDS = ("approve", "choice", "text", "secret")

# Re-exports: every name the sections used to define here, so existing imports keep working.
from .alert import REMINDERS, alert_owner, alert_text
from .requests import (
    _REMINDER_EVENTS,
    _REMINDER_SENDERS,
    _TASK_REQUESTS,
    _answers_dir,
    _requests_dir,
    _safe_id,
    _write_answer,
    answer,
    cancel_for_task,
    close_request,
    get_request,
    pending_requests,
    screenshot_path,
)
from .retasks import (
    forget_password,
    forget_retask,
    list_passwords,
    list_retasks,
    save_password,
)
from .run import describe, run
from .screenshots import (
    RUN_ID,
    SHOT_NAME,
    _status_words,
    latest_runs,
    run_for_task,
    run_screenshot_path,
    run_screenshots,
)
from .takeover import (
    _TAKEOVER,
    HANDED_BACK,
    MACHINES,
    _laptop,
    _machine,
    _takeover,
    takeover_activity,
    takeover_frame,
    takeover_input,
    takeover_machines,
    takeover_start,
    takeover_status,
    takeover_stop,
)

__all__ = [
    "ANSWER_KINDS",
    "COMPUTER_USE_DIR",
    "DESKTOP_ENV",
    "HANDED_BACK",
    "MACHINES",
    "REMINDERS",
    "RUN_ID",
    "SHOT_NAME",
    "_REMINDER_EVENTS",
    "_REMINDER_SENDERS",
    "_TAKEOVER",
    "_TASK_REQUESTS",
    "ComputerUseError",
    "_answers_dir",
    "_laptop",
    "_machine",
    "_requests_dir",
    "_safe_id",
    "_status_words",
    "_takeover",
    "_write_answer",
    "alert_owner",
    "alert_text",
    "answer",
    "cancel_for_task",
    "close_request",
    "describe",
    "forget_password",
    "forget_retask",
    "get_request",
    "installed",
    "latest_runs",
    "list_passwords",
    "list_retasks",
    "pending_requests",
    "run",
    "run_for_task",
    "run_screenshot_path",
    "run_screenshots",
    "save_password",
    "screenshot_path",
    "takeover_activity",
    "takeover_frame",
    "takeover_input",
    "takeover_machines",
    "takeover_start",
    "takeover_status",
    "takeover_stop",
]
