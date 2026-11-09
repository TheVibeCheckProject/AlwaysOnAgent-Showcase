"""
AutonomousDaemon background jobs: watches, proactive cycle, nightly backup.
(daemon.py §8)

Moved into the core.daemon package on 2026-11-04.
"""
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.memory_review import MemoryReview
    from core.proactive import Proactive
    from core.watches import Watches

try:
    from core import backup
    from core.database import DB_FILE
except ImportError:
    import backup  # type: ignore[no-redef]
    from database import DB_FILE  # type: ignore[no-redef]


class _JobsMixin:
    # Host state provided by _SetupMixin on AutonomousDaemon (core/daemon/setup.py).
    # Annotation-only: these create no runtime attributes, so they cannot shadow
    # any sibling mixin's method in the MRO.
    db: "DatabaseManager"
    db_path: Path
    logger: logging.Logger
    memory_review: "MemoryReview"
    proactive: "Proactive"
    watches: "Watches"

    def _run_watches(self) -> None:
        try:
            self.watches.run_check()
        except Exception as e:
            self.logger.warning(f"Watch check failed: {e}")

    def _run_proactive(self) -> None:
        try:
            self.proactive.run_cycle()
        except Exception as e:
            self.logger.warning(f"Proactive cycle failed: {e}")
        try:
            now = self.proactive.now()
            if self.proactive.settings()["enabled"] == "1" and not self.proactive.quiet(now) and \
                    self.memory_review.due(now):
                self.memory_review.run(now)
        except Exception as e:
            self.logger.warning(f"Memory check failed: {e}")

    def _run_backup(self) -> None:
        live = self.db_path.resolve() == DB_FILE.resolve()          # test databases never go to the mirror
        try:
            result = backup.create_backup(self.db_path, local_dir=self.db_path.parent / "backups",
                                          mirror=backup.mirror_dir() if live else None)
        except Exception as e:
            result = backup.BackupResult(ok=False, errors=[str(e)])
        backup.record_result(self.db, result)
        if result.complete:
            self.logger.info(result.summary())
        else:
            self.logger.warning(result.summary())
            if live:
                backup.notify_owner_of(f"⚠️ Memory backup problem\n{result.summary()}")

