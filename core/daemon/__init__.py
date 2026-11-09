#!/usr/bin/env python3
"""
Always-On Autonomous AI Background Worker Daemon
=================================================
Runs one background task at a time from the SQLite queue (data/state.db), each in its own folder,
on the engine it asks for (core/task_engines/: Antigravity, the free cloud models, local Ollama)
or the media pipeline, then records the outcome. Also runs memory upkeep, daily backups and the
stuck-task watchdog.

How it runs: run() (§10) wakes every DAEMON_INTERVAL seconds, or at once when a task is queued, and calls
heartbeat_cycle() (§6). That starts the next queued task through EngineDispatcher (§2, §3) on a thread, runs
the background jobs that are due (§8), and watches for stuck tasks (§7). A finished task is recorded by §9.

MAP OF THIS FILE
----------------
Each section starts with a banner like "# §2  ...". Search for the § number to jump straight to it.

  §1   Setup                       imports and re-exports (constants now live with their sections)
  §2   Running one task            core/daemon/dispatcher.py: _DispatcherMixin
  §3   Special engines             core/daemon/special.py: _SpecialEnginesMixin
  §4   Logs in the database        core/daemon/logs.py: SQLiteLogHandler
  §5   The daemon: setup           core/daemon/setup.py: _SetupMixin
  §6   The heartbeat               core/daemon/heartbeat.py: _HeartbeatMixin
  §7   Stuck tasks                 core/daemon/watchdog.py: _WatchdogMixin
  §8   Background jobs             core/daemon/jobs.py: _JobsMixin
  §9   Before and after a task     core/daemon/completion.py: _CompletionMixin
  §10  The main loop               core/daemon/loop.py: _LoopMixin

The sections live in this package (one file per section). The old flat
core/daemon_<name>.py files and core/daemon.py were thin shims re-exporting
from here; they were removed 2026-11-04. Run it with
python -m core.daemon (see core/daemon/__main__.py).
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.proactive import Proactive

from .completion import REPORT_SUMMARY_MAX, _CompletionMixin
from .dispatcher import TASK_ROOT, _DispatcherMixin
from .heartbeat import ROUTINE_LOG_EVERY, _HeartbeatMixin
from .jobs import _JobsMixin
from .logs import SQLiteLogHandler
from .loop import _LoopMixin
from .setup import SecretRedactionFilter, _SetupMixin
from .special import ACTIVITY_LOG_KEEP, _SpecialEnginesMixin
from .watchdog import _WatchdogMixin

try:
    from core.config import notify_owner
    from core.processes import kill_process_tree
except ImportError:
    from config import notify_owner  # type: ignore[no-redef]
    from processes import kill_process_tree  # type: ignore[no-redef]


__all__ = [
    "ACTIVITY_LOG_KEEP",
    "REPORT_SUMMARY_MAX",
    "ROUTINE_LOG_EVERY",
    "TASK_ROOT",
    "AutonomousDaemon",
    "EngineDispatcher",
    "SQLiteLogHandler",
    "SecretRedactionFilter",
    "kill_process_tree",
    "notify_owner",
]


class EngineDispatcher(_DispatcherMixin, _SpecialEnginesMixin):
    """Runs a task on its engine (auto, antigravity, cloud, ollama, agentwork) or the media pipeline, in the task's folder."""
    # Set by _SetupMixin after construction (core/daemon/setup.py): the
    # proactive stack the special engines use (the weekly report review reads
    # it via getattr). Annotation-only: creates no runtime attribute.
    proactive: "Proactive"


class AutonomousDaemon(_SetupMixin, _HeartbeatMixin, _WatchdogMixin, _JobsMixin, _CompletionMixin, _LoopMixin):
    """The heartbeat pulse daemon with asynchronous execution pool and SQLite WAL backend."""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Always-On Autonomous AI Background Worker Daemon",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("DAEMON_INTERVAL", "30")),
        help="Heartbeat loop interval in seconds"
    )
    parser.add_argument(
        "--db-file",
        type=str,
        default="data/state.db",
        help="Path to SQLite database file relative to workspace"
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="logs/worker.log",
        help="Path to log file relative to workspace"
    )
    parser.add_argument(
        "--agy-path",
        type=str,
        default=None,
        help="Explicit path to Antigravity CLI binary (agy / agy.exe)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute a single heartbeat cycle and exit"
    )
    return parser.parse_args()
