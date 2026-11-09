"""
AutonomousDaemon heartbeat: one pulse — tasks, upkeep, background jobs.
(daemon.py §6)

Moved into the core.daemon package on 2026-11-04.
"""
import concurrent.futures
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.away_shift import AwayShift
    from core.daemon import EngineDispatcher
    from core.database import DatabaseManager
    from core.proactive import Proactive
    from core.watches import Watches

try:
    from core import backup, connectors, events, heartbeat_ping, maintenance
    from core.database import DB_FILE, get_iso_now
    from core.housekeeping import LOG_MAX_AGE_DAYS, prune_old_logs
except ImportError:
    import backup  # type: ignore[no-redef]
    import connectors  # type: ignore[no-redef]
    import events  # type: ignore[no-redef]
    import heartbeat_ping  # type: ignore[no-redef]
    import maintenance  # type: ignore[no-redef]
    from database import DB_FILE, get_iso_now  # type: ignore[no-redef]
    from housekeeping import LOG_MAX_AGE_DAYS, prune_old_logs  # type: ignore[no-redef]


# Repeating status lines (idle, standby, still running) are logged when they change and then every
# this many heartbeats (20 x 30 s = 10 minutes); logging them every pulse was ~90% of worker.log.
ROUTINE_LOG_EVERY = 20


class _HeartbeatMixin:
    # Host state owned by _SetupMixin (earlier in the MRO): annotation-only
    # declarations so mypy sees the attributes. No runtime values are set here,
    # so nothing is shadowed.
    db: "DatabaseManager"
    db_path: Path
    log_path: Path
    logger: logging.Logger
    proactive: "Proactive"
    watches: "Watches"
    away_shift: "AwayShift"
    executor: concurrent.futures.ThreadPoolExecutor
    dispatcher: "EngineDispatcher"
    active_future: concurrent.futures.Future[Any] | None
    active_task: dict[str, Any] | None
    active_task_start_time: float
    _offline_checked: bool
    _backup_thread: threading.Thread | None
    _mail_thread: threading.Thread | None
    _proactive_thread: threading.Thread | None
    _watch_thread: threading.Thread | None
    _tiktok_stats_thread: threading.Thread | None
    _pins_topup_thread: threading.Thread | None
    _last_routine: str | None
    _last_routine_hb: int

    if TYPE_CHECKING:
        # Methods defined on _WatchdogMixin, _JobsMixin and _CompletionMixin
        # (all later than _HeartbeatMixin in the AutonomousDaemon MRO):
        # type-only declarations — a runtime stub here would shadow the real
        # method, so these exist for mypy only.
        def _run_backup(self) -> None: ...
        def _run_proactive(self) -> None: ...
        def _run_watches(self) -> None: ...
        def _task_overdue(self) -> bool: ...
        def _abandon_stuck_task(self) -> None: ...
        def _finalize_task_completion(
            self, task: dict[str, Any], exit_code: int, output_text: str,
            engine_used: str, fallback_triggered: bool
        ) -> None: ...
        def _with_memory_briefing(self, task: dict[str, Any]) -> dict[str, Any]: ...

    def _update_heartbeat_state(self, iso_now: str) -> int:
        """Bumps the heartbeat counter and, on the first pulse, notes a long gap as the agent having been off."""
        curr_hb = int(self.db.get_state("total_heartbeats", "0")) + 1
        self.db.set_state("total_heartbeats", str(curr_hb))
        if not self._offline_checked:
            # Before last_heartbeat is overwritten: a long gap means the agent (or the Mini) was off. 2026-10-17 to 22
            # it was off 5 days after a power cut, 10 posts were missed, and nothing said so (AUDIT_BACKGROUND B1)
            self._offline_checked = True
            if self.db_path.resolve() == DB_FILE.resolve():
                try:
                    self.proactive.offline_notice(self.db.get_state("last_heartbeat", ""))
                except Exception as e:
                    self.logger.warning(f"offline check failed: {e}")
        self.db.set_state("last_heartbeat", iso_now)
        return curr_hb

    def _backfill_thumbnails(self) -> None:
        """Thumbnails for finished video tasks that lack one (older tasks, a failed grab), taken before
        the cleanup below can prune their videos; at most a few frames per pass."""
        try:
            from core import task_thumbs
            if self.db:
                made = task_thumbs.backfill(self.db, limit=10)
                if made:
                    self.logger.info(f"Saved {made} task thumbnail(s)")
        except Exception as e:
            self.logger.debug(f"Task thumbnail backfill: {e}")

    def _cleanup_tiktok_files(self) -> None:
        """Only stale leftovers: a render running elsewhere (e.g. a preview) may be using fresh files."""
        try:
            try:
                from core import tiktok_videos
            except ImportError:
                import tiktok_videos  # type: ignore[no-redef]
            tiktok_videos.cleanup(older_than=3600)
        except Exception as e:
            self.logger.debug(f"Storage maintenance check: {e}")

    def _prune_logs_and_workspaces(self) -> None:
        """Prunes old log files and abandoned workspaces."""
        removed = prune_old_logs(self.log_path.parent)
        if removed:
            self.logger.info(f"Removed log files unused for {LOG_MAX_AGE_DAYS}+ days: {', '.join(removed)}")
        try:
            from core.config import AGENT_WORK_DIR
            from core.housekeeping import prune_old_workspaces
            removed_ws = prune_old_workspaces(Path(AGENT_WORK_DIR) / "workspaces", max_age_days=7)
            if removed_ws:
                self.logger.info(f"Storage maintenance: Pruned {len(removed_ws)} abandoned workspace(s) ({', '.join(removed_ws[:5])})")
        except Exception as e:
            self.logger.debug(f"Workspace cleanup check: {e}")

    def _prune_uploads_and_share(self) -> None:
        """Prunes old chat attachments, leftover share downloads, and old chat thumbnails."""
        try:
            from core import documents
            old_uploads = documents.prune()
            if old_uploads:
                self.logger.info(f"Removed {old_uploads} chat attachment(s) older than {documents.KEEP_DAYS} days")
        except Exception as e:
            self.logger.debug(f"Attachment cleanup: {e}")
        try:
            # Shared repo downloads left by a failed or interrupted send, and the chat's video thumbnails
            # (one per video ever shown): nothing else removes them (2026-10-28 audit 4.5)
            from core.config import VIDEOS_DIR
            from core.housekeeping import prune_old_files
            left = prune_old_files(self.db_path.parent / "tmp" / "share", 3600)
            thumbs = prune_old_files(Path(VIDEOS_DIR).parent / ".file_thumbnails", 30 * 86400)
            if left or thumbs:
                self.logger.info(f"Removed {left} leftover share download(s), {thumbs} old chat thumbnail(s)")
        except Exception as e:
            self.logger.debug(f"Share/thumbnail cleanup: {e}")

    def _run_storage_maintenance(self, curr_hb: int) -> None:
        """Periodic WAL compaction and storage maintenance every 20 heartbeats."""
        if curr_hb % 20 == 0:
            self.db.checkpoint_wal("PASSIVE")
            task_running = self.active_future is not None and not self.active_future.done()
            if task_running:
                # A render writes/reads its temp frames for minutes; cleaning now would truncate its video
                self.logger.info("Skipping storage maintenance while a task is running.")
            else:
                self._backfill_thumbnails()
                self._cleanup_tiktok_files()
                self._prune_logs_and_workspaces()
                self._prune_uploads_and_share()
                # Memory upkeep (consolidate) moved to AwayShift (docs/PROACTIVE_RESEARCH.md 1a, 1l)

    def _start_backup_thread(self) -> None:
        """Daily memory backup (also while in standby), on its own thread."""
        if (self._backup_thread is None or not self._backup_thread.is_alive()) and backup.backup_due(self.db):
            # not a daemon thread: shutdown waits for it (~a second) instead of killing it mid-copy
            self._backup_thread = threading.Thread(target=self._run_backup, name="Backup")
            self._backup_thread.start()

    def _start_mail_sync(self, live: bool) -> None:
        """Gmail into the subscription database every connectors.MAIL_SYNC_MINUTES (live agent only), on its own
        thread: the first sync reads months of mail."""
        if live and (self._mail_thread is None or not self._mail_thread.is_alive()) and connectors.mail_sync_due(self.db):
            self._mail_thread = threading.Thread(target=connectors.run_mail_sync, args=(self.db, backup.notify_owner_of),
                                                 name="MailSync", daemon=True)
            self._mail_thread.start()

    def _start_proactive_thread(self, live: bool) -> None:
        """The agent reaching out first (brief, notices, questions) every PROACTIVE_EVERY_MINUTES (live agent only)
        ...and at once when Alex opens the app (events.OWNER_ARRIVED): the brief and held messages arrive as he does."""
        arrived = events.OWNER_ARRIVED.is_set()
        if live and (self._proactive_thread is None or not self._proactive_thread.is_alive()) and \
                (arrived or self.proactive.due()):
            events.OWNER_ARRIVED.clear()
            self._proactive_thread = threading.Thread(target=self._run_proactive, name="Proactive", daemon=True)
            self._proactive_thread.start()

    def _start_tiktok_stats(self, live: bool) -> None:
        """The TikTok numbers once a day for the quote writer's "what resonates" (live agent only; not during a task:
        a render is using the machine)."""
        if live and self.active_future is None and \
                (self._tiktok_stats_thread is None or not self._tiktok_stats_thread.is_alive()):
            try:
                from core import tiktok_videos as _tv
            except ImportError:
                import tiktok_videos as _tv  # type: ignore[no-redef]
            if _tv.stats_due(self.db):
                self._tiktok_stats_thread = threading.Thread(target=_tv.run_daily_stats, args=(self.db, self.logger),
                                                             name="TikTokStats", daemon=True)
                self._tiktok_stats_thread.start()

    def _start_pins_topup(self, live: bool) -> None:
        """Pinterest never runs dry: once a day, when few pins wait, new drafts and their preview go to the owner
        (core/social_posts.py run_topup; live agent only, not during a task)."""
        if live and self.active_future is None and \
                (self._pins_topup_thread is None or not self._pins_topup_thread.is_alive()):
            try:
                from core import social_posts as _sp
                from core.config import IMAGES_DIR as _images
            except ImportError:
                import social_posts as _sp  # type: ignore[no-redef]
                from config import IMAGES_DIR as _images  # type: ignore[no-redef]
            if _sp.topup_due(self.db):
                self._pins_topup_thread = threading.Thread(
                    target=_sp.run_topup, args=(self.db, self.proactive, self.logger, _images),
                    name="PinsTopUp", daemon=True)
                self._pins_topup_thread.start()

    def _start_watches_and_away(self, live: bool) -> None:
        """Mail watches (plain searches, no AI) and the away shift (background memory upkeep, open loops research,
        interests, patterns, and opportunity scans when the app has been closed for 2 hours)."""
        if live and (self._watch_thread is None or not self._watch_thread.is_alive()) and self.watches.due():
            self._watch_thread = threading.Thread(target=self._run_watches, name="Watches", daemon=True)
            self._watch_thread.start()
        # Away shift: background memory upkeep, open loops research, interests, patterns, and opportunity scans
        # when the app has been closed for 2 hours (docs/PROACTIVE_RESEARCH.md 1a, 1l)
        if live and not self.away_shift.is_running() and self.away_shift.due():
            self.away_shift.start()

    def _abandon_overdue_task(self) -> None:
        """Abandons the active task if it is overdue."""
        if self.active_future is not None and not self.active_future.done() and self._task_overdue():
            self._abandon_stuck_task()

    def _complete_active_task(self) -> None:
        """Collects a finished active task's result and finalizes it, then clears the active slot."""
        future = self.active_future
        task = self.active_task
        if future is None or task is None:
            return  # nothing running: a future is only ever set alongside its task
        try:
            exit_code, output_text, engine_used, fallback_triggered = future.result()
        except Exception as e:
            self.logger.exception("Unhandled exception in task worker thread")
            exit_code, output_text, engine_used, fallback_triggered = 1, str(e), "unknown", False

        self._finalize_task_completion(
            task, exit_code, output_text, engine_used, fallback_triggered
        )
        self.active_future = None
        self.active_task = None
        self.active_task_start_time = 0.0

    def _handle_standby(self, curr_hb: int) -> bool:
        """Handles a daemon placed in Standby/Sleep mode via DeskAI / the Web HUD. True when it handled the pulse."""
        status = self.db.get_state("daemon_status", "idle")

        # Check if daemon was placed in Standby/Sleep mode via DeskAI / the Web HUD
        if status in ("paused", "stopped", "sleep"):
            self._abandon_overdue_task()
            if self.active_future is not None and self.active_task is not None:
                if not self.active_future.done():
                    elapsed = time.time() - self.active_task_start_time
                    self._log_routine(curr_hb, f"standby-running:{self.active_task['id']}",
                                      f"Heartbeat #{curr_hb}: standby, finishing task [{self.active_task['id']}] "
                                      f"({elapsed:.0f}s elapsed).")
                    return True
                # Finalize active task that just completed
                self._complete_active_task()

            self._log_routine(curr_hb, "standby", f"Heartbeat #{curr_hb}: standby (0% CPU/GPU). "
                                                  "Send /start_daemon or any task to wake it.")
            return True
        return False

    def _check_active_task(self, curr_hb: int) -> bool:
        """Checks on the currently running background task (if any). True when it handled the pulse."""
        self._abandon_overdue_task()
        if self.active_future is not None and self.active_task is not None:
            if not self.active_future.done():
                elapsed = time.time() - self.active_task_start_time
                self._log_routine(curr_hb, f"running:{self.active_task['id']}",
                                  f"Heartbeat #{curr_hb}: task [{self.active_task['id']}] running ({elapsed:.0f}s elapsed).")
                return True

            # Active future is done! Process completion
            self._complete_active_task()
        return False

    def _dispatch_next_task(self, iso_now: str, curr_hb: int) -> None:
        """Checks for the next pending task in the queue and dispatches it asynchronously (none while a restart
        waits for the queue to drain)."""
        if maintenance.holding():
            self._log_routine(curr_hb, "held", f"Heartbeat #{curr_hb}: a restart is waiting; not starting new tasks.")
            return
        task = self.db.get_next_backlog_task()
        if not task:
            self.db.set_state("daemon_status", "idle")
            self._log_routine(curr_hb, "idle", f"Heartbeat #{curr_hb}: idle, no tasks waiting.")
            return

        # 3. Dispatch new task asynchronously
        task_id = task["id"]
        task_title = task["title"]
        self.logger.info(f"Picked up pending task: [{task_id}] '{task_title}'")

        # Mark in_progress in DB
        self.db.update_task(task_id, {
            "phase": "in_progress",
            "started_at": iso_now
        })
        self.db.set_state("daemon_status", "running_task")

        default_engine = self.db.get_state("default_engine", "auto")
        self.active_task = task
        self.active_task_start_time = time.time()

        # Submit to non-blocking worker thread
        self.active_future = self.executor.submit(
            self.dispatcher.execute, self._with_memory_briefing(task), default_engine
        )
        # The finished task is recorded (and "done" announced) on the next heartbeat: wake it now, not in 30 s
        self.active_future.add_done_callback(lambda _f: events.wake_daemon())
        self.logger.info(f"Task [{task_id}] dispatched to asynchronous execution worker.")

    def heartbeat_cycle(self) -> None:
        """Executes a non-blocking heartbeat pulse."""
        iso_now = get_iso_now()
        curr_hb = self._update_heartbeat_state(iso_now)
        self._run_storage_maintenance(curr_hb)
        self._start_backup_thread()
        live = self.db_path.resolve() == DB_FILE.resolve()
        self._start_mail_sync(live)
        if live:
            heartbeat_ping.maybe_ping(self.logger, db=self.db)
        self._start_proactive_thread(live)
        self._start_tiktok_stats(live)
        self._start_pins_topup(live)
        self._start_watches_and_away(live)
        if self._handle_standby(curr_hb):
            return
        if self._check_active_task(curr_hb):
            return
        self._dispatch_next_task(iso_now, curr_hb)

    def _log_routine(self, heartbeat: int, kind: str, message: str) -> None:
        """Logs a repeating status line when it changes, and otherwise every ROUTINE_LOG_EVERY heartbeats."""
        if kind != self._last_routine or heartbeat - self._last_routine_hb >= ROUTINE_LOG_EVERY:
            self.logger.info(message)
            self._last_routine, self._last_routine_hb = kind, heartbeat
