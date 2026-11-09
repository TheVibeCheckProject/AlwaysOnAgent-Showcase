"""
Tasks for the database engine
=============================
§4 of core/database.py, split out on 2026-11-03, packaged as core/database/tasks.py on 2026-11-04:
everything about the task queue — making
a task (with its source), reading and updating it, its live progress, cancelling and
retrying, the worker's process id, picking the next one by priority, and tasks left running
by a crash. Also Idempotency-Keys, so the app's retries don't make a task twice.
"""
import datetime
import json
import sqlite3
from typing import TYPE_CHECKING, Any

try:
    from .common import (
        CANCELLED_EXIT_CODE,
        NEEDS_INPUT_EXIT_CODE,
        PRIORITY_MAP,
        REVERSE_PRIORITY_MAP,
        events,
        get_iso_now,
        logger,
    )
except ImportError:
    from core.database.common import (
        CANCELLED_EXIT_CODE,
        NEEDS_INPUT_EXIT_CODE,
        PRIORITY_MAP,
        REVERSE_PRIORITY_MAP,
        events,
        get_iso_now,
        logger,
    )


class _TasksMixin:
    # Interface this mixin requires from the host class (DatabaseManager):
    # these are defined on sibling mixins / the manager, declared here so mypy can see them.
    def get_connection(self) -> sqlite3.Connection:
        raise NotImplementedError

    def get_state(self, key: str, default: str = "") -> str:
        raise NotImplementedError

    # These three live on _BlockersMixin, which comes AFTER this mixin in
    # DatabaseManager's MRO — a real stub here would shadow them at runtime.
    # Declared type-only so mypy sees them without shadowing.
    if TYPE_CHECKING:
        def add_blocker(self, task_id: str | None, message: str, severity: str = "error") -> str: ...
        def clear_blockers_for_task(self, task_id: str): ...
        def recalculate_overall_phase(self) -> str: ...

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
    def create_task(self, task: dict[str, Any] | None = None, **kwargs) -> str:
        """Inserts a new task into the database. Accepts either a dict or keyword arguments."""
        task_data = dict(task or {})
        task_data.update(kwargs)
        task = task_data

        conn = self.get_connection()
        explicit_id = task.get("id")
        values = self._task_values(task)
        task_id = self._insert_task(conn, explicit_id, values)

        self.recalculate_overall_phase()
        # Live events (IDEAS #26): DeskAI hears of it at once, and a queued task wakes the daemon
        events.publish_task("created", task_id, events.PHASE_STATUS.get(values[3], values[3]), phase=values[3])
        return task_id

    def _task_values(self, task: dict[str, Any]) -> tuple:
        """Builds the INSERT values tuple for a task dict (priority resolved to an int)."""
        prio = task.get("priority", "medium")
        prio_int = PRIORITY_MAP.get(str(prio).lower(), 2) if isinstance(prio, str) else int(prio)
        return (
            task.get("title", task.get("prompt", "Untitled Task")[:60]),
            task.get("prompt", ""),
            task.get("engine", self.get_state("default_engine", "auto")),
            task.get("phase", "backlog"),
            prio_int,
            task.get("mode", "accept-edits"),
            task.get("timeout_seconds", 300),
            task.get("retry_count", 0),
            task.get("max_retries", 2),
            task.get("created_at") or get_iso_now(),
            task.get("started_at"),
            task.get("completed_at"),
            task.get("exit_code"),
            task.get("engine_used"),
            1 if task.get("fallback_triggered") else 0,
            task.get("output_summary"),
            task.get("last_error"),
            json.dumps(task.get("metadata") or {})
        )

    def _insert_task(self, conn: sqlite3.Connection, explicit_id: str | None, values: tuple) -> str:
        """Inserts the task, retrying with the next free id on collision; returns the id used."""
        # Auto-generated IDs can collide when two requests enqueue at the same moment,
        # so retry with the next free number on conflict.
        attempts = 1 if explicit_id else 20
        for attempt in range(attempts):
            task_id = explicit_id or self._next_task_id()
            try:
                with conn:
                    conn.execute("""
                        INSERT INTO tasks (
                            id, title, prompt, engine, phase, priority, mode,
                            timeout_seconds, retry_count, max_retries, created_at,
                            started_at, completed_at, exit_code, engine_used,
                            fallback_triggered, output_summary, last_error, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (task_id, *values))
                break
            except sqlite3.IntegrityError:
                if explicit_id or attempt == attempts - 1:
                    raise
        return task_id

    def _next_task_id(self) -> str:
        """Returns the next sequential 'task-NNN' id based on the highest existing numeric suffix."""
        conn = self.get_connection()
        cur = conn.execute("""
            SELECT COALESCE(MAX(CAST(SUBSTR(id, 6) AS INTEGER)), 0) AS max_n
            FROM tasks WHERE id GLOB 'task-[0-9]*'
        """)
        return f"task-{cur.fetchone()['max_n'] + 1:03d}"

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        conn = self.get_connection()
        cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def _row_to_task(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["priority"] = REVERSE_PRIORITY_MAP.get(d["priority"], "medium")
        d["fallback_triggered"] = bool(d["fallback_triggered"])
        if d.get("metadata_json"):
            try:
                d["metadata"] = json.loads(d["metadata_json"])
            except (ValueError, TypeError) as e:
                logger.warning(f"Task {d.get('id')}: metadata_json is not valid JSON, using empty metadata: {e}")
                d["metadata"] = {}
        else:
            d["metadata"] = {}
        # Progress only means something while the task runs
        progress = d.pop("progress_json", None)
        d["progress"] = None
        if progress and d.get("phase") == "in_progress":
            try:
                d["progress"] = json.loads(progress)
            except (ValueError, TypeError) as e:
                logger.warning(f"Task {d.get('id')}: progress_json is not valid JSON, leaving progress empty: {e}")
        d["cancelled"] = d.get("phase") == "failed" and d.get("exit_code") == CANCELLED_EXIT_CODE
        d["needs_input"] = d.get("phase") == "completed" and d.get("exit_code") == NEEDS_INPUT_EXIT_CODE
        return d

    def update_task(self, task_id: str, fields: dict[str, Any]):
        """Updates specific fields of an existing task."""
        if not fields:
            return

        conn = self.get_connection()
        set_clauses = []
        params = []

        for k, v in fields.items():
            if k == "priority" and isinstance(v, str):
                v = PRIORITY_MAP.get(v.lower(), 2)
            elif k == "metadata":
                k = "metadata_json"
                v = json.dumps(v)
            elif k == "fallback_triggered":
                v = 1 if v else 0

            set_clauses.append(f"{k} = ?")
            params.append(v)

        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = ?"

        with conn:
            conn.execute(sql, params)

        if "phase" in fields:
            self.recalculate_overall_phase()
            events.publish_phase(task_id, fields["phase"], fields.get("exit_code"))

    def set_task_progress(self, task_id: str, progress: dict[str, Any] | None):
        """Stores a running task's progress (stage, percent, time left) for the HUD and DeskAI; None clears it."""
        conn = self.get_connection()
        with conn:
            conn.execute("UPDATE tasks SET progress_json = ? WHERE id = ?",
                         (json.dumps(progress) if progress is not None else None, task_id))
        if isinstance(progress, dict):
            events.publish_task("progress", task_id, "progress", percent=progress.get("percent"),
                                label=progress.get("label"), eta_seconds=progress.get("eta_seconds"))

    def cancel_task(self, task_id: str, reason: str) -> bool:
        """
        Marks a waiting or running task cancelled (phase 'failed', exit code 137). Killing its processes is
        the caller's job. Returns False when the task has already finished, so a late cancel can't
        overwrite a result.
        """
        task = self.get_task(task_id)
        if not task or task.get("phase") not in ("backlog", "pending", "in_progress"):
            return False
        self.update_task(task_id, {
            "phase": "failed",
            "last_error": reason,
            "completed_at": get_iso_now(),
            "exit_code": CANCELLED_EXIT_CODE,
        })
        return True

    def task_for_request_key(self, key: str, max_age_hours: int = 24) -> str | None:
        """The task an Idempotency-Key already created, if it's recent; old keys are forgotten."""
        conn = self.get_connection()
        cutoff = (datetime.datetime.now().astimezone() - datetime.timedelta(hours=max_age_hours)).isoformat()
        with conn:
            conn.execute("DELETE FROM request_keys WHERE created_at < ?", (cutoff,))
        row = conn.execute("SELECT task_id FROM request_keys WHERE key = ?", (key,)).fetchone()
        return row["task_id"] if row else None

    def remember_request_key(self, key: str, task_id: str):
        conn = self.get_connection()
        with conn:
            conn.execute("INSERT OR REPLACE INTO request_keys (key, task_id, created_at) VALUES (?, ?, ?)",
                         (key, task_id, get_iso_now()))

    def retry_task(self, task_id: str) -> bool:
        """
        Manually requeues a task to backlog with a fresh retry budget and clears its blockers.
        Shared by every surface so they behave identically.
        """
        task = self.get_task(task_id)
        if not task or task.get("phase") == "in_progress":
            return False        # still running: requeueing now would run it twice at once
        self.update_task(task_id, {
            "phase": "backlog",
            "retry_count": 0,
            "started_at": None,
            "completed_at": None,
            "last_error": None,
            "exit_code": None,
            "worker_pid": None,
            "progress_json": None
        })
        self.clear_blockers_for_task(task_id)
        return True

    def set_task_pid(self, task_id: str, pid: int | None):
        """Records or clears the OS worker process ID for an active task."""
        conn = self.get_connection()
        with conn:
            conn.execute("UPDATE tasks SET worker_pid = ? WHERE id = ?", (pid, task_id))

    def get_active_task_pid(self) -> tuple[str, int] | None:
        """Returns (task_id, worker_pid) of currently running task if a worker process is recorded."""
        conn = self.get_connection()
        cur = conn.execute(
            "SELECT id, worker_pid FROM tasks WHERE phase = 'in_progress' AND worker_pid IS NOT NULL LIMIT 1"
        )
        row = cur.fetchone()
        if row and row["worker_pid"]:
            return row["id"], row["worker_pid"]
        return None

    def get_next_backlog_task(self) -> dict[str, Any] | None:
        """Selects the highest-priority pending task in the backlog (lowest prio number first)."""
        conn = self.get_connection()
        cur = conn.execute("""
            SELECT * FROM tasks 
            WHERE phase IN ('backlog', 'pending')
            ORDER BY priority ASC, created_at ASC
            LIMIT 1
        """)
        row = cur.fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self, limit: int = 50, phase: str | None = None) -> list[dict[str, Any]]:
        conn = self.get_connection()
        if phase:
            cur = conn.execute(
                "SELECT * FROM tasks WHERE phase = ? ORDER BY created_at DESC LIMIT ?",
                (phase, limit)
            )
        else:
            cur = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        return [self._row_to_task(r) for r in cur.fetchall()]

    def reconcile_orphaned_tasks(self) -> list[str]:
        """
        Scans for tasks stuck in 'in_progress' from a previous crash/reboot.
        Resets them to 'backlog' (with incremented retry count) or 'failed' if max retries exceeded.
        """
        conn = self.get_connection()
        cur = conn.execute("SELECT * FROM tasks WHERE phase = 'in_progress'")
        orphans = [self._row_to_task(r) for r in cur.fetchall()]
        recovered_ids = []

        for task in orphans:
            tid = task["id"]
            retries = task.get("retry_count", 0) + 1
            max_r = task.get("max_retries", 2)
            now = get_iso_now()

            if (task.get("metadata") or {}).get("action") == "tiktok":
                self._fail_tiktok_orphan(tid, task, now)
            elif (task.get("metadata") or {}).get("action") == "computer" or task.get("engine") == "computer":
                self._fail_computer_orphan(tid, task, now)
            elif retries <= max_r:
                self._requeue_orphan(tid, task, now, retries, max_r)
            else:
                self._fail_exhausted_orphan(tid, task, now, retries)
            recovered_ids.append(tid)

        if orphans:
            self.recalculate_overall_phase()

        return recovered_ids

    def _fail_tiktok_orphan(self, tid: str, task: dict[str, Any], now: str) -> None:
        # Interrupted while it may have been posting: if TikTok took the video before the restart, running
        # it again posts it twice (found 2026-10-04: restarts re-queued posts, against the no-retry rule)
        logger.warning(f"Orphaned TikTok post [{tid}] '{task['title']}': marked failed, not re-run.")
        self.update_task(tid, {
            "phase": "failed",
            "completed_at": now,
            "last_error": (f"Interrupted by a restart on {now} while it may have been posting to TikTok. "
                           "Not re-run, so nothing is posted twice: check TikTok, then post from the card "
                           "if it isn't there."),
        })

    def _fail_computer_orphan(self, tid: str, task: dict[str, Any], now: str) -> None:
        # The same rule as a failed computer task (daemon._finalize_task_completion): the screen still holds
        # the interrupted run's windows, and a new run acts on them (2026-10-28 audit 3.1)
        logger.warning(f"Orphaned computer task [{tid}] '{task['title']}': marked failed, not re-run.")
        self.update_task(tid, {
            "phase": "failed",
            "completed_at": now,
            "last_error": (f"Interrupted by a restart on {now}. Not re-run on its own: the screen may still "
                           "hold its windows. Check the PC, then run it again from the card."),
        })

    def _requeue_orphan(self, tid: str, task: dict[str, Any], now: str, retries: int, max_r: int) -> None:
        logger.warning(
            f"Orphaned task detected [{tid}] '{task['title']}'. "
            f"Reconciling to 'backlog' (attempt {retries}/{max_r})."
        )
        self.update_task(tid, {
            "phase": "backlog",
            "retry_count": retries,
            "last_error": f"Interrupted by daemon restart on {now}"
        })

    def _fail_exhausted_orphan(self, tid: str, task: dict[str, Any], now: str, retries: int) -> None:
        logger.error(
            f"Orphaned task [{tid}] '{task['title']}' exceeded max retries. Marking failed."
        )
        self.update_task(tid, {
            "phase": "failed",
            "retry_count": retries,
            "completed_at": now,
            "last_error": f"Failed after crash/interruption on {now}"
        })
        self.add_blocker(
            task_id=tid,
            message=f"Task [{tid}] failed due to unexpected daemon interruption."
        )
