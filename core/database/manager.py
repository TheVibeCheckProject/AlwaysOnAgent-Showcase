"""
The connection and tables for the database engine
==================================================
§2 of core/database.py, split out on 2026-11-03, packaged as core/database/manager.py
on 2026-11-04: one connection per thread (WAL), and the core tables. DatabaseManager
inherits the section mixins; core/database/__init__.py re-exports it.
"""

import sqlite3
import threading
from pathlib import Path

try:
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
except ImportError:
    from core.database.common import (
        DB_FILE,
        get_iso_now,
    )

try:
    from .blockers import _BlockersMixin
    from .state import _StateMixin
    from .stats import _StatsMixin
    from .tasks import _TasksMixin
except ImportError:
    from core.database.blockers import _BlockersMixin
    from core.database.state import _StateMixin
    from core.database.stats import _StatsMixin
    from core.database.tasks import _TasksMixin

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §2  THE CONNECTION AND TABLES
#     DatabaseManager: a connection per thread in WAL mode (so the daemon, the API server and scripts can read
#     and write at once), and the tasks, state, logs and quotes tables. Memory and usage add their own tables.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
class DatabaseManager(_StateMixin, _TasksMixin, _BlockersMixin, _StatsMixin):
    """Thread-safe SQLite database manager for AlwaysOnAgent using WAL mode."""

    def __init__(self, db_path: Path | None = None):
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
            _create_tasks_schema(conn)
            _create_state_schema(conn)
            _create_log_schema(conn)
            _migrate_columns(conn)
            _seed_defaults(conn)
            _apply_legacy_cleanups(conn)


def _create_tasks_schema(conn: sqlite3.Connection) -> None:
    """The tasks table and its phase/priority index."""
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
    """)


def _create_state_schema(conn: sqlite3.Connection) -> None:
    """The system_state and blockers tables."""
    conn.executescript("""
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
    """)


def _create_log_schema(conn: sqlite3.Connection) -> None:
    """The system_logs, used_quotes and request_keys tables and their indexes."""
    conn.executescript("""
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


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Columns added after the first release."""
    for column in ("worker_pid INTEGER", "progress_json TEXT"):
        try:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column};")
        except sqlite3.OperationalError:
            pass  # column already exists: the migration ran before


def _seed_defaults(conn: sqlite3.Connection) -> None:
    """Initialize default system states if not present."""
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


def _apply_legacy_cleanups(conn: sqlite3.Connection) -> None:
    """One-off cleanups for data written by older versions."""
    # Finished tasks used to be marked 'qa'; they are 'completed'. Old running totals are
    # gone too (counts come from the tasks table).
    conn.execute("UPDATE tasks SET phase = 'completed' WHERE phase = 'qa'")
    # The hermes and groq engines were merged into cloud
    conn.execute("UPDATE system_state SET value = 'cloud' WHERE key = 'default_engine' AND value IN ('hermes', 'groq')")
    conn.execute("UPDATE tasks SET engine = 'cloud' WHERE engine IN ('hermes', 'groq') AND phase = 'backlog'")
    conn.execute("DELETE FROM system_state WHERE key IN ('tasks_completed', 'tasks_failed')")
