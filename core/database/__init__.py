"""
SQLite WAL Database Engine for AlwaysOnAgent
=============================================
The agent's single store: tasks, system state, logs, used quotes, plus the memory and usage tables
that core/memory/ and core/llm_usage.py add.
WAL mode with a busy timeout lets the daemon, the push notifier and Web HUD (threads of supervisor.py)
and scripts read and write at the same time.

MAP OF THIS PACKAGE
-------------------
Packaged on 2026-11-04 from the flat split files (2026-11-03); each section lives in its own
module in this package. The old core/database.py and core/database_<name>.py
paths were thin shims re-exporting from here; they were removed 2026-11-04, so import
from this package. Search for the § number to jump straight to it.

  §1   Setup                           common.py — imports, the database file's place,
                                        the time format every record uses, shared constants
  §2   The connection and tables       manager.py — one connection per thread (WAL), and the core tables
  §3   System state                    state.py — small named values (get_state /
                                        set_state); daemon status; default engine
  §4   Tasks                           tasks.py — create, read, update, progress, cancel,
                                        retry, the next to run, crash recovery
  §5   Blockers and the project phase  blockers.py — what's blocking a task; the overall
                                        phase from tasks and blockers
  §6   Stats and logs                  stats.py — status numbers, the log table,
                                        compacting the WAL file
"""

from .blockers import _BlockersMixin
from .common import (
    CANCELLED_EXIT_CODE,
    DATA_DIR,
    DB_FILE,
    NEEDS_INPUT_EXIT_CODE,
    PRIORITY_MAP,
    REVERSE_PRIORITY_MAP,
    STANDBY_STATES,
    WORKSPACE_DIR,
    events,
    get_iso_now,
    logger,
)
from .manager import DatabaseManager
from .state import _StateMixin
from .stats import _StatsMixin
from .tasks import _TasksMixin

# Re-exported for backwards compatibility: every name anything imported from core.database.
__all__ = [
    "CANCELLED_EXIT_CODE",
    "DATA_DIR",
    "DB_FILE",
    "NEEDS_INPUT_EXIT_CODE",
    "PRIORITY_MAP",
    "REVERSE_PRIORITY_MAP",
    "STANDBY_STATES",
    "WORKSPACE_DIR",
    "DatabaseManager",
    "events",
    "get_iso_now",
    "logger",
]
