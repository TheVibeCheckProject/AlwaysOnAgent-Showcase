"""
AutonomousDaemon main loop: sleep, wake, pulse, until stopped.
(daemon.py §10)

Moved into the core.daemon package on 2026-11-04.
"""
import concurrent.futures
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.away_shift import AwayShift
    from core.daemon import EngineDispatcher
    from core.database import DatabaseManager

try:
    from core import events, llm_usage
    from core import memory as memory_module
    from core.config import notify_owner
    from core.database import DB_FILE
except ImportError:
    import events  # type: ignore[no-redef]
    import llm_usage  # type: ignore[no-redef]
    import memory as memory_module  # type: ignore[no-redef]
    from config import notify_owner  # type: ignore[no-redef]
    from database import DB_FILE  # type: ignore[no-redef]


class _LoopMixin:
    # Host state owned by _SetupMixin (core/daemon/setup.py) and the dispatcher
    # (core/daemon/dispatcher.py); declared here so mypy can see what the loop
    # methods use. _LoopMixin is last in the AutonomousDaemon MRO, so these
    # annotation-only declarations never shadow a real attribute at runtime.
    running: bool
    interval_seconds: int
    logger: logging.Logger
    workspace_dir: Path
    db_path: Path
    log_path: Path
    dispatcher: "EngineDispatcher"
    db: "DatabaseManager"
    active_future: concurrent.futures.Future[Any] | None
    active_task: dict[str, Any] | None
    executor: concurrent.futures.ThreadPoolExecutor
    away_shift: "AwayShift"

    def heartbeat_cycle(self) -> None:
        """One daemon pulse; defined on _HeartbeatMixin (earlier in the MRO)."""
        raise NotImplementedError

    def _sleep_until_woken(self) -> bool:
        """
        Sleeps up to interval_seconds, but returns at once when events.DAEMON_WAKE is set (a task was queued, or
        the running task finished), so work starts and results land in about a second instead of up to 30 s
        (IDEAS #26). The timer stays as the fallback for work written by other processes. Checks self.running
        every second so shutdown stays quick. Returns True when woken early.
        """
        sleep_start = time.time()
        while self.running:
            left = self.interval_seconds - (time.time() - sleep_start)
            if left <= 0:
                return False
            if events.DAEMON_WAKE.wait(min(1.0, left)):
                events.DAEMON_WAKE.clear()
                return True
        return False

    def _log_startup_banner(self) -> None:
        """Logs the daemon startup banner (skipped when run under the supervisor)."""
        if os.environ.get("SUPERVISOR_ACTIVE") != "1":
            self.logger.info("=" * 60)
            self.logger.info("Always-On Autonomous AI Background Daemon Starting")
            self.logger.info(f"Workspace Directory : {self.workspace_dir}")
            self.logger.info(f"Database File       : {self.db_path}")
            self.logger.info(f"Log File            : {self.log_path}")
            self.logger.info(f"Heartbeat Interval  : {self.interval_seconds}s")
            self.logger.info(f"Antigravity CLI     : {self.dispatcher.antigravity.agy_path}")
            self.logger.info(f"Cloud providers     : {', '.join(p for p in self.dispatcher.llm.PROVIDERS if self.dispatcher.llm.keys.get(p)) or 'none configured'}")
            ollama_alive, ollama_models = self.dispatcher.ollama.client.is_alive()
            self.logger.info(f"Local Ollama Status : {'ONLINE' if ollama_alive else 'OFFLINE'} (Models: {ollama_models})")
            self.logger.info("=" * 60)

    def _startup_recovery(self) -> None:
        """Crash recovery: tasks left in_progress by a crash go back to the backlog."""
        reconciled = self.db.reconcile_orphaned_tasks()
        if reconciled:
            self.logger.info(f"Startup crash recovery: Reconciled {len(reconciled)} orphaned task(s): {reconciled}")

        self.db.end_busy_status()
        if self.db_path.resolve() == DB_FILE.resolve():
            llm_usage.NOTIFY = notify_owner          # daily-quota warnings (IDEAS #12); the live agent only
            memory_module.NOTIFY = notify_owner      # a memory delete the second opinion held (IDEAS #11)

    def _wait_active_task_once(self) -> None:
        """In --once mode, waits for the active background task to finish, then pulses once more."""
        if self.active_future and not self.active_future.done():
            self.logger.info("Waiting for active background task to complete (--once mode)...")
            try:
                timeout = self.active_task.get("timeout_seconds", 300) if self.active_task is not None else 300
                self.active_future.result(timeout=timeout)
                self.heartbeat_cycle()
            except Exception as ex:
                self.logger.error(f"Task wait error in --once mode: {ex}")

    def _shutdown(self) -> None:
        """Graceful shutdown: stop workers, mark stopped, checkpoint the database."""
        self.logger.info("Shutting down worker threads...")
        self.executor.shutdown(wait=False)
        self.away_shift.shutdown(wait=False)
        self.db.set_state("daemon_status", "stopped")
        self.db.checkpoint_wal("TRUNCATE")
        self.logger.info("Autonomous AI Background Daemon has exited cleanly.")

    def run(self, run_once: bool = False) -> None:
        """Main daemon loop."""
        self._log_startup_banner()
        self._startup_recovery()

        while self.running:
            try:
                self.heartbeat_cycle()
            except Exception:
                self.logger.exception("Unhandled exception during heartbeat cycle")

            if run_once:
                self._wait_active_task_once()
                self.logger.info("Single run requested (--once). Exiting daemon.")
                break

            self._sleep_until_woken()

        self._shutdown()
