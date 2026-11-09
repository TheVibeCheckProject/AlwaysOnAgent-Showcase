"""
AutonomousDaemon stuck-task watchdog: a task that never returns is failed.
(daemon.py §7)

Moved into the core.daemon package on 2026-11-04.
"""
import concurrent.futures
import logging
import time
from typing import TYPE_CHECKING, Any

import psutil

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core.config import notify_owner
    from core.database import get_iso_now
    from core.processes import HEADLESS_BROWSER_NAMES, kill_process_tree
except ImportError:
    from config import notify_owner  # type: ignore[no-redef]
    from database import get_iso_now  # type: ignore[no-redef]
    from processes import HEADLESS_BROWSER_NAMES, kill_process_tree  # type: ignore[no-redef]


class _WatchdogMixin:
    WATCHDOG_MIN_SECONDS = 1800      # renders legitimately take ~7 min despite a 300 s task limit

    # Host state owned by _SetupMixin (earlier in the MRO): annotation-only
    # declarations so mypy sees the attributes. No runtime values are set here,
    # so nothing is shadowed.
    logger: logging.Logger
    db: "DatabaseManager"
    executor: concurrent.futures.ThreadPoolExecutor
    active_future: concurrent.futures.Future | None
    active_task: dict[str, Any] | None
    active_task_start_time: float

    def _task_overdue(self) -> bool:
        """A backstop for true hangs: twice the task's limit, never under 30 minutes."""
        limit = max(2 * int((self.active_task or {}).get("timeout_seconds") or 300), self.WATCHDOG_MIN_SECONDS)
        return time.time() - self.active_task_start_time > limit

    def _abandon_stuck_task(self) -> None:
        """
        Fails a task that never returned, stops what can be stopped, and frees the queue. A Python
        thread can't be killed, so the stuck worker is left behind and a fresh worker takes over;
        whatever it eventually returns is ignored.
        """
        task = self.active_task or {}
        task_id = task.get("id", "?")
        elapsed = time.time() - self.active_task_start_time
        self.logger.error(f"Watchdog: task [{task_id}] has run {elapsed / 60:.0f} min without finishing; abandoning it.")
        pid_info = self.db.get_active_task_pid()
        if pid_info and pid_info[0] == task_id:
            kill_process_tree(pid_info[1], logger=self.logger)
        # In-process work (video renders) runs browsers and FFmpeg as our own child processes
        for child in psutil.Process().children(recursive=True):
            try:
                if any(name in child.name().lower() for name in HEADLESS_BROWSER_NAMES):
                    kill_process_tree(child.pid, logger=self.logger)
            except psutil.Error:
                pass
        self.db.update_task(task_id, {
            "phase": "failed", "completed_at": get_iso_now(), "exit_code": -1,
            "last_error": f"Watchdog: no result after {elapsed / 60:.0f} minutes; the task was stopped.",
        })
        self.db.set_task_pid(task_id, None)
        self.executor.shutdown(wait=False)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="TaskWorker")
        self.active_future, self.active_task, self.active_task_start_time = None, None, 0.0
        self.db.end_busy_status()
        # Resolve notify_owner from core.daemon at call time, not from this
        # module's import-time binding: tests patch core.daemon.notify_owner
        # (e.g. test_1_2_watchdog_frees_the_queue_from_a_hung_task) and the
        # patch must be what this call sees, as it was when this method lived
        # in daemon.py.
        try:
            from core.daemon import notify_owner as _notify_owner
        except ImportError:
            _notify_owner = notify_owner
        _notify_owner(f"⚠️ Task {task_id} \"{task.get('title', '')}\" ran {elapsed / 60:.0f} minutes without "
                     f"finishing, so it was stopped and marked failed. The queue continues.")

