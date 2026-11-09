"""
Stats and logs for the database engine
=======================================
§6 of core/database.py, split out on 2026-11-03, packaged as core/database/stats.py on 2026-11-04:
the numbers the status screens show,
the capped log table, and the WAL checkpoint.
"""
import sqlite3
from typing import Any

try:
    from .common import get_iso_now, logger
except ImportError:
    from core.database.common import get_iso_now, logger


class _StatsMixin:
    # Interface this mixin requires from the host class (DatabaseManager / sibling
    # mixins): the methods below are defined elsewhere and called on self here.
    def get_connection(self) -> sqlite3.Connection:
        raise NotImplementedError

    def get_state(self, key: str, default: str = "") -> str:
        raise NotImplementedError

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
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
            # lastrowid is int after a successful INSERT; -1 only if it were None.
            return inserted_id if inserted_id is not None else -1

    def get_recent_logs(self, limit: int = 100, since_id: int | None = None) -> list[dict[str, Any]]:
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

    def checkpoint_wal(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
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
