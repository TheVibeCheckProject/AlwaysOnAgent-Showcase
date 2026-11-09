"""
SQLiteLogHandler: recent log lines kept in state.db.
(daemon.py §4)

Moved into the core.daemon package on 2026-11-04.
"""
import logging

try:
    from core.database import DatabaseManager
except ImportError:
    from database import DatabaseManager  # type: ignore[no-redef]


class SQLiteLogHandler(logging.Handler):
    """Writes log events directly into SQLite system_logs table (capped at ~500 rows)."""
    def __init__(self, db: DatabaseManager, max_rows: int = 500):
        super().__init__()
        self.db = db
        self.max_rows = max_rows

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.db.append_log(record.levelname, msg, max_rows=self.max_rows)
        except Exception:
            self.handleError(record)

