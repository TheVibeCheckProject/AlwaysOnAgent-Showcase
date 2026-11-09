"""
Blockers and the project phase for the database engine
=======================================================
§5 of core/database.py, split out on 2026-11-03, packaged as core/database/blockers.py on 2026-11-04:
blockers on a task and the project's
overall phase worked out from them.
"""
import datetime
import sqlite3
from typing import Any

try:
    from .common import get_iso_now
except ImportError:
    from core.database.common import get_iso_now


class _BlockersMixin:
    # Interface this mixin requires from the host class (DatabaseManager):
    # these are defined on sibling mixins / the manager, declared here so mypy can see them.
    def get_connection(self) -> sqlite3.Connection:
        raise NotImplementedError

    def set_state(self, key: str, value: str):
        raise NotImplementedError

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
    def add_blocker(self, task_id: str | None, message: str, severity: str = "error") -> str:
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

    def get_active_blockers(self) -> list[dict[str, Any]]:
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
