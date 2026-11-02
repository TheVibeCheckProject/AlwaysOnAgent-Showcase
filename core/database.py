"""
SQLite WAL Database Engine for AlwaysOnAgent
=============================================
The agent's single store: tasks, system state, logs, used quotes, plus the memory and usage tables
that core/memory.py and core/llm_usage.py add.
WAL mode with a busy timeout lets the daemon, the push notifier and Web HUD (threads of supervisor.py)
and scripts read and write at the same time.
"""

import datetime
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from core import events
except ImportError:
    import events

logger = logging.getLogger("DatabaseManager")
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
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
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()


class DatabaseManager:
    """Thread-safe SQLite database manager for AlwaysOnAgent using WAL mode."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DB_FILE
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.init_database()

    def get_connection(self) -> sqlite3.Connection:
        """Returns a thread-local SQLite connection configured for concurrent WAL access."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=15.0,
                check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            # Enable WAL mode & busy timeout
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 15000;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            self._local.conn = conn
        return conn

    def init_database(self):
        """Initializes database schema and indices."""
        conn = self.get_connection()
        with conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                engine TEXT NOT NULL DEFAULT 'auto',
                phase TEXT NOT NULL DEFAULT 'backlog',
                priority INTEGER NOT NULL DEFAULT 2,
                mode TEXT NOT NULL DEFAULT 'accept-edits',
                timeout_seconds INTEGER NOT NULL DEFAULT 300,
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 2,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                exit_code INTEGER,
                engine_used TEXT,
                fallback_triggered INTEGER DEFAULT 0,
                output_summary TEXT,
                last_error TEXT,
                metadata_json TEXT,
                worker_pid INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_phase_prio 
                ON tasks (phase, priority, created_at);

            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS blockers (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                message TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'error',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_logs_id ON system_logs (id DESC);

            CREATE TABLE IF NOT EXISTS used_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote TEXT NOT NULL,
                used_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_quotes_used_at ON used_quotes (id DESC);

            -- Idempotency-Key header -> the task it created, so a client's retry can't queue a duplicate
            CREATE TABLE IF NOT EXISTS request_keys (
                key TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """)

            # Columns added after the first release
            for column in ("worker_pid INTEGER", "progress_json TEXT"):
                try:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {column};")
                except Exception:
                    pass

            # Initialize default system states if not present
            defaults = {
                "daemon_status": "idle",
                "default_engine": "auto",
                "total_heartbeats": "0",
                "last_heartbeat": "",
                "overall_phase": "idle",
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
                    (k, v, get_iso_now())
                )
            # Finished tasks used to be marked 'qa'; they are 'completed'. Old running totals are
            # gone too (counts come from the tasks table).
            conn.execute("UPDATE tasks SET phase = 'completed' WHERE phase = 'qa'")
            # The hermes and groq engines were merged into cloud
            conn.execute("UPDATE system_state SET value = 'cloud' WHERE key = 'default_engine' AND value IN ('hermes', 'groq')")
            conn.execute("UPDATE tasks SET engine = 'cloud' WHERE engine IN ('hermes', 'groq') AND phase = 'backlog'")
            conn.execute("DELETE FROM system_state WHERE key IN ('tasks_completed', 'tasks_failed')")

    # ---------------- System State Accessors ----------------

    def get_state(self, key: str, default: str = "") -> str:
        conn = self.get_connection()
        cur = conn.execute("SELECT value FROM system_state WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str):
        conn = self.get_connection()
        with conn:
            conn.execute("""
                INSERT INTO system_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (key, str(value), get_iso_now()))

    def end_busy_status(self) -> None:
        """daemon_status back to idle after a task ends or is cancelled, or at start-up, unless the owner chose standby.
        Until 2026-10-28 each of those set idle outright, so standby lasted only until the next restart or finished task
        and the tasks queued meanwhile started anyway (audit 3.2). "stopped" is what a clean shutdown writes, not a
        choice: it becomes idle."""
        if self.get_state("daemon_status", "idle") not in STANDBY_STATES:
            self.set_state("daemon_status", "idle")

    # ---------------- Task Management ----------------

    def create_task(self, task: Optional[Dict[str, Any]] = None, **kwargs) -> str:
        """Inserts a new task into the database. Accepts either a dict or keyword arguments."""
        task_data = dict(task or {})
        task_data.update(kwargs)
        task = task_data

        conn = self.get_connection()
        explicit_id = task.get("id")

        prio = task.get("priority", "medium")
        prio_int = PRIORITY_MAP.get(str(prio).lower(), 2) if isinstance(prio, str) else int(prio)
        values = (
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

        self.recalculate_overall_phase()
        # Live events (IDEAS #26): DeskAI hears of it at once, and a queued task wakes the daemon
        events.publish_task("created", task_id, events.PHASE_STATUS.get(values[3], values[3]), phase=values[3])
        return task_id

    def _next_task_id(self) -> str:
        """Returns the next sequential 'task-NNN' id based on the highest existing numeric suffix."""
        conn = self.get_connection()
        cur = conn.execute("""
            SELECT COALESCE(MAX(CAST(SUBSTR(id, 6) AS INTEGER)), 0) AS max_n
            FROM tasks WHERE id GLOB 'task-[0-9]*'
        """)
        return f"task-{cur.fetchone()['max_n'] + 1:03d}"

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def _row_to_task(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["priority"] = REVERSE_PRIORITY_MAP.get(d["priority"], "medium")
        d["fallback_triggered"] = bool(d["fallback_triggered"])
        if d.get("metadata_json"):
            try:
                d["metadata"] = json.loads(d["metadata_json"])
            except Exception:
                d["metadata"] = {}
        else:
            d["metadata"] = {}
        # Progress only means something while the task runs
        progress = d.pop("progress_json", None)
        d["progress"] = None
        if progress and d.get("phase") == "in_progress":
            try:
                d["progress"] = json.loads(progress)
            except Exception:
                pass
        d["cancelled"] = d.get("phase") == "failed" and d.get("exit_code") == CANCELLED_EXIT_CODE
        d["needs_input"] = d.get("phase") == "completed" and d.get("exit_code") == NEEDS_INPUT_EXIT_CODE
        return d

    def update_task(self, task_id: str, fields: Dict[str, Any]):
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

    def set_task_progress(self, task_id: str, progress: Optional[Dict[str, Any]]):
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

    def task_for_request_key(self, key: str, max_age_hours: int = 24) -> Optional[str]:
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

    def set_task_pid(self, task_id: str, pid: Optional[int]):
        """Records or clears the OS worker process ID for an active task."""
        conn = self.get_connection()
        with conn:
            conn.execute("UPDATE tasks SET worker_pid = ? WHERE id = ?", (pid, task_id))

    def get_active_task_pid(self) -> Optional[Tuple[str, int]]:
        """Returns (task_id, worker_pid) of currently running task if a worker process is recorded."""
        conn = self.get_connection()
        cur = conn.execute(
            "SELECT id, worker_pid FROM tasks WHERE phase = 'in_progress' AND worker_pid IS NOT NULL LIMIT 1"
        )
        row = cur.fetchone()
        if row and row["worker_pid"]:
            return row["id"], row["worker_pid"]
        return None

    def get_next_backlog_task(self) -> Optional[Dict[str, Any]]:
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

    def list_tasks(self, limit: int = 50, phase: Optional[str] = None) -> List[Dict[str, Any]]:
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

    def reconcile_orphaned_tasks(self) -> List[str]:
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
            elif (task.get("metadata") or {}).get("action") == "computer" or task.get("engine") == "computer":
                # The same rule as a failed computer task (daemon._finalize_task_completion): the screen still holds
                # the interrupted run's windows, and a new run acts on them (2026-10-28 audit 3.1)
                logger.warning(f"Orphaned computer task [{tid}] '{task['title']}': marked failed, not re-run.")
                self.update_task(tid, {
                    "phase": "failed",
                    "completed_at": now,
                    "last_error": (f"Interrupted by a restart on {now}. Not re-run on its own: the screen may still "
                                   "hold its windows. Check the PC, then run it again from the card."),
                })
            elif retries <= max_r:
                logger.warning(
                    f"Orphaned task detected [{tid}] '{task['title']}'. "
                    f"Reconciling to 'backlog' (attempt {retries}/{max_r})."
                )
                self.update_task(tid, {
                    "phase": "backlog",
                    "retry_count": retries,
                    "last_error": f"Interrupted by daemon restart on {now}"
                })
            else:
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
            recovered_ids.append(tid)

        if orphans:
            self.recalculate_overall_phase()

        return recovered_ids

    # ---------------- Blockers ----------------

    def add_blocker(self, task_id: Optional[str], message: str, severity: str = "error") -> str:
        conn = self.get_connection()
        blocker_id = f"blocker-{int(datetime.datetime.now().timestamp())}"
        with conn:
            conn.execute("""
                INSERT INTO blockers (id, task_id, message, severity, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (blocker_id, task_id, message, severity, get_iso_now()))
        self.recalculate_overall_phase()
        return blocker_id

    def clear_blockers_for_task(self, task_id: str):
        conn = self.get_connection()
        with conn:
            conn.execute("DELETE FROM blockers WHERE task_id = ?", (task_id,))
        self.recalculate_overall_phase()

    def get_active_blockers(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cur = conn.execute("SELECT * FROM blockers ORDER BY created_at DESC")
        return [dict(r) for r in cur.fetchall()]

    # ---------------- Global State & Metrics ----------------

    def recalculate_overall_phase(self) -> str:
        """Calculates overarching project phase from active tasks and blockers."""
        conn = self.get_connection()
        # Check active blockers
        b_cur = conn.execute("SELECT COUNT(*) AS cnt FROM blockers")
        if b_cur.fetchone()["cnt"] > 0:
            phase = "blocked"
            self.set_state("overall_phase", phase)
            return phase

        cur = conn.execute("SELECT DISTINCT phase FROM tasks")
        phases = {r["phase"] for r in cur.fetchall()}

        if "in_progress" in phases:
            phase = "in_progress"
        elif "backlog" in phases or "pending" in phases:
            phase = "backlog"
        elif all(p == "completed" for p in phases) and phases:
            phase = "completed"
        else:
            phase = "idle"

        self.set_state("overall_phase", phase)
        return phase

    def get_stats(self) -> Dict[str, Any]:
        """Returns comprehensive real-time statistics."""
        conn = self.get_connection()
        cur = conn.execute("""
            SELECT 
                COUNT(*) AS total_tasks,
                SUM(CASE WHEN phase = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN phase = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN phase = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
                SUM(CASE WHEN phase IN ('backlog', 'pending') THEN 1 ELSE 0 END) AS backlog
            FROM tasks
        """)
        row = cur.fetchone()

        # Engine usage counts
        e_cur = conn.execute("""
            SELECT engine_used, COUNT(*) AS cnt 
            FROM tasks 
            WHERE engine_used IS NOT NULL 
            GROUP BY engine_used
        """)
        engines = {r["engine_used"]: r["cnt"] for r in e_cur.fetchall()}

        return {
            "total_heartbeats": int(self.get_state("total_heartbeats", "0")),
            "tasks_completed": row["completed"] or 0,
            "tasks_failed": row["failed"] or 0,
            "tasks_in_progress": row["in_progress"] or 0,
            "tasks_backlog": row["backlog"] or 0,
            "total_tasks": row["total_tasks"] or 0,
            "daemon_status": self.get_state("daemon_status", "idle"),
            "overall_phase": self.get_state("overall_phase", "idle"),
            "last_heartbeat": self.get_state("last_heartbeat", ""),
            "default_engine": self.get_state("default_engine", "auto"),
            "engines_used": engines
        }

    # ---------------- System Logs Buffer ----------------

    def append_log(self, level: str, message: str, max_rows: int = 500) -> int:
        """Appends a log record and keeps the table capped at max_rows."""
        conn = self.get_connection()
        now = get_iso_now()
        with conn:
            cur = conn.execute(
                "INSERT INTO system_logs (timestamp, level, message) VALUES (?, ?, ?)",
                (now, level, message)
            )
            inserted_id = cur.lastrowid
            conn.execute(
                "DELETE FROM system_logs WHERE id <= (SELECT max(id) - ? FROM system_logs)",
                (max_rows,)
            )
            return inserted_id

    def get_recent_logs(self, limit: int = 100, since_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieves recent logs, optionally filtering for logs created after since_id."""
        conn = self.get_connection()
        if since_id is not None:
            cur = conn.execute(
                "SELECT * FROM system_logs WHERE id > ? ORDER BY id ASC LIMIT ?",
                (since_id, limit)
            )
        else:
            cur = conn.execute(
                "SELECT * FROM system_logs ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            rows = cur.fetchall()
            return [dict(r) for r in reversed(rows)]
        return [dict(r) for r in cur.fetchall()]

    # ---------------- Maintenance & WAL Management ----------------

    def checkpoint_wal(self, mode: str = "PASSIVE") -> Tuple[int, int, int]:
        """
        Executes a SQLite WAL checkpoint to flush and compact state.db-wal.
        Modes: PASSIVE, FULL, RESTART, TRUNCATE.
        Returns: (busy_flag, log_pages, checkpointed_pages)
        """
        valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
        target_mode = mode.upper() if mode.upper() in valid_modes else "PASSIVE"
        try:
            conn = self.get_connection()
            cur = conn.execute(f"PRAGMA wal_checkpoint({target_mode});")
            row = cur.fetchone()
            if row:
                busy, log, ckpt = row[0], row[1], row[2]
                logger.debug(f"WAL checkpoint ({target_mode}): busy={busy}, log={log}, ckpt={ckpt}")
                return busy, log, ckpt
        except Exception as e:
            logger.warning(f"Failed to execute WAL checkpoint ({target_mode}): {e}")
        return -1, -1, -1

    def set_daemon_status(self, status: str):
        """Sets the system daemon status (e.g., 'idle', 'paused', 'running_task')."""
        self.set_state("daemon_status", status)

    def set_default_engine(self, engine: str):
        """Stores the default engine (normalized by the caller: auto, antigravity, cloud, ollama)."""
        self.set_state("default_engine", engine.lower().strip())


