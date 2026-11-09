"""Standby holds (2026-10-28 audit 3.2): until then a finished task, the watchdog, /cancel and every start-up set the
daemon back to idle, so the tasks queued while the owner had chosen standby started anyway."""
import pytest

from core.database import DatabaseManager


@pytest.mark.parametrize("before, after", [("paused", "paused"), ("sleep", "sleep"), ("running_task", "idle"),
                                           ("stopped", "idle"), ("idle", "idle")])
def test_only_the_owners_standby_survives_the_end_of_a_task(tmp_path, before, after):
    db = DatabaseManager(tmp_path / "state.db")
    db.set_state("daemon_status", before)
    db.end_busy_status()
    assert db.get_state("daemon_status") == after      # "stopped" is a clean shutdown, not the owner's choice


def test_a_task_finishing_in_standby_leaves_it_in_standby(tmp_path):
    from core.daemon import AutonomousDaemon
    daemon = AutonomousDaemon(workspace_dir=tmp_path / "ws", db_file=str(tmp_path / "ws" / "state.db"),
                              log_file=str(tmp_path / "ws" / "worker.log"))
    try:
        tid = daemon.db.create_task(title="Render", prompt="render it")
        daemon.db.update_task(tid, {"phase": "in_progress"})
        daemon.db.set_state("daemon_status", "paused")        # the owner chose standby while it ran
        daemon._finalize_task_completion(daemon.db.get_task(tid), 0, "done", "media_pipeline", False)
        assert daemon.db.get_task(tid)["phase"] == "completed"
        assert daemon.db.get_state("daemon_status") == "paused"
    finally:
        for h in list(daemon.logger.handlers):
            h.close()
            daemon.logger.removeHandler(h)
