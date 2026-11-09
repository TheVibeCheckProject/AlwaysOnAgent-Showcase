"""
Shared setup for the database engine
=====================================
§1 of core/database.py, split out on 2026-11-03: the imports, the database file's place,
the time format every record uses, and the small constants the task/phase code shares.
core/database/common.py, packaged on 2026-11-04: the imports, the database file's place,
the time format every record uses, and the small constants the task/phase code shares.
core/database/__init__.py re-exports these names for backwards compatibility.
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §1  SETUP
#     Imports, where state.db lives, and get_iso_now(): the local time with its offset, used on every record.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
import datetime
import logging
from pathlib import Path

try:
    from core import events
except ImportError:
    pass  # type: ignore[no-redef]

logger = logging.getLogger("DatabaseManager")
# One level deeper than before (core/database/common.py, not core/database_common.py),
# so the repo root is three parents up, not two.
WORKSPACE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = WORKSPACE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = DATA_DIR / "state.db"

PRIORITY_MAP = {
    "urgent": 0,
    "high": 1,
    "medium": 2,
    "low": 3
}
REVERSE_PRIORITY_MAP = {v: k for k, v in PRIORITY_MAP.items()}

# exit_code of a cancelled task (like a killed process); the API reports it as "cancelled": true
CANCELLED_EXIT_CODE = 137
# exit_code of an AgentWork job that ran but changed nothing (blocked or unsure); the task is "completed" and
# the API reports "needs_input": true, so the app shows it as needing the owner rather than as a success
NEEDS_INPUT_EXIT_CODE = 4
# daemon_status values that are the owner's standby (the HUD's standby control stores "paused")
STANDBY_STATES = ("paused", "sleep")


def get_iso_now() -> str:
    return datetime.datetime.now(datetime.UTC).astimezone().isoformat()


