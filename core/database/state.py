"""
System state for the database engine
=====================================
§3 of core/database.py, split out on 2026-11-03, packaged as core/database/state.py on 2026-11-04:
the key-value store for small settings
and markers, and the daemon's status (idle, running a task, standby).
"""
try:
    from .common import STANDBY_STATES, get_iso_now
except ImportError:
    from core.database.common import STANDBY_STATES, get_iso_now

import sqlite3


class _StateMixin:
    # Interface this mixin requires from the host class (DatabaseManager):
    # these are defined on sibling mixins / the manager, declared here so mypy can see them.
    def get_connection(self) -> sqlite3.Connection:
        raise NotImplementedError

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
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

    def set_daemon_status(self, status: str):
        """Sets the system daemon status (e.g., 'idle', 'paused', 'running_task')."""
        self.set_state("daemon_status", status)

    def set_default_engine(self, engine: str):
        """Stores the default engine (normalized by the caller: auto, antigravity, cloud, ollama)."""
        self.set_state("default_engine", engine.lower().strip())
