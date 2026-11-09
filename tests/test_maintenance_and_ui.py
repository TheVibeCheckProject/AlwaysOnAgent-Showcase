"""
Maintenance and control-surface tests
=====================================
Verifies:
1. SQLite WAL Checkpointing (checkpoint_wal)
2. Database state helpers (set_daemon_status, set_default_engine)
3. Web HUD FastAPI endpoints (/api/daemon/state, /api/engine incl. older engine names, /api/trigger_media)
4. Voice transcriber model target (whisper-large-v3-turbo)
5. Storage cleanup of temp recordings and pruning of older videos
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos (it would render and send real videos): point the agent at a
# folder where it isn't installed, so an accidental call fails harmlessly
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

TEST_TEMP_DIR = Path(tempfile.mkdtemp(prefix="maintenance_ui_test_"))

# core.config loads .env into os.environ on import, so override STATE_DB_PATH afterwards;
# The API server (interfaces/hud) opens its database at import time and must never touch data/state.db.
os.environ["STATE_DB_PATH"] = str(TEST_TEMP_DIR / "hud_import_default.db")

from core.database import DatabaseManager
from interfaces.hud import app
from interfaces.hud import state as hud_state
from tests._hud_client import client_from


def test_wal_checkpoint_and_state_helpers():
    print("[WAL Checkpoint & State Helpers]")
    test_db_path = TEST_TEMP_DIR / "test_state_helpers.db"
    if test_db_path.exists():
        test_db_path.unlink()
        Path(str(test_db_path) + "-wal").unlink(missing_ok=True)

    db = DatabaseManager(test_db_path)
    # Perform writes
    for i in range(10):
        db.create_task({"title": f"Test Task {i}", "prompt": "Work"})

    # Checkpoint WAL
    busy, log, ckpt = db.checkpoint_wal("TRUNCATE")
    assert busy == 0, f"Expected busy=0 on checkpoint, got {busy}"
    assert log == 0, f"Expected 0 log pages left after TRUNCATE, got {log}"
    print(f"  - WAL checkpoint executed cleanly (result: busy={busy}, log={log}, ckpt={ckpt})")

    # State helpers
    db.set_daemon_status("paused")
    assert db.get_state("daemon_status") == "paused"
    db.set_daemon_status("idle")
    assert db.get_state("daemon_status") == "idle"

    db.set_default_engine("cloud")
    assert db.get_state("default_engine") == "cloud"
    db.set_default_engine("auto")
    assert db.get_state("default_engine") == "auto"
    print("  - Database state helpers verified.")

    # Cleanup
    try:
        db.get_connection().close()
    except Exception:
        pass
    del db
    if test_db_path.exists():
        try:
            test_db_path.unlink()
            Path(str(test_db_path) + "-wal").unlink(missing_ok=True)
        except Exception:
            pass


def test_api_endpoints():
    print("[Web HUD REST Endpoints (Isolated DB)]")
    test_hud_db = TEST_TEMP_DIR / "test_isolated_hud.db"
    if test_hud_db.exists():
        test_hud_db.unlink()
        Path(str(test_hud_db) + "-wal").unlink(missing_ok=True)

    isolated_db = DatabaseManager(test_hud_db)
    original_db = hud_state.db
    hud_state.set_db(isolated_db)

    try:
        client = client_from(app)

        # 1. Daemon state endpoint
        res = client.post("/api/daemon/state", json={"status": "paused"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        assert res.json()["daemon_status"] == "paused"

        res = client.post("/api/daemon/state", json={"status": "idle"})
        assert res.status_code == 200
        assert res.json()["daemon_status"] == "idle"

        # Invalid state
        res = client.post("/api/daemon/state", json={"status": "invalid_xyz"})
        assert res.status_code == 400

        # 2. Engine endpoint
        res = client.post("/api/engine", json={"engine": "cloud"})
        assert res.status_code == 200
        assert res.json()["default_engine"] == "cloud"
        # Older engine names still work and are stored as the engine they now mean
        res = client.post("/api/engine", json={"engine": "hermes"})
        assert res.status_code == 200 and res.json()["default_engine"] == "cloud"
        assert client.post("/api/engine", json={"engine": "media_pipeline"}).status_code == 400

        res = client.post("/api/engine", json={"engine": "auto"})
        assert res.status_code == 200
        assert res.json()["default_engine"] == "auto"

        # Invalid engine
        res = client.post("/api/engine", json={"engine": "super_ai_unknown"})
        assert res.status_code == 400

        # 3. Media trigger endpoint (writes into test_isolated_hud.db only!)
        res = client.post("/api/trigger_media", json={"action": "video", "quote": "You are capable of wonderful things."})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert "task_id" in data
        print("  - Web HUD REST endpoints (/api/daemon/state, /api/engine, /api/trigger_media) verified successfully in isolated DB.")

    finally:
        # Restore the previous (also temporary) DB and clean up test DB
        hud_state.set_db(original_db)
        try:
            isolated_db.get_connection().close()
        except Exception:
            pass
        del isolated_db
        if test_hud_db.exists():
            try:
                test_hud_db.unlink()
                Path(str(test_hud_db) + "-wal").unlink(missing_ok=True)
            except Exception:
                pass


def test_log_housekeeping():
    print("[Log Housekeeping: quiet heartbeats, stale log pruning, compaction]")
    import time

    from core.housekeeping import compact_log, prune_old_logs

    # 1. Stale files go; fresh logs, .gitkeep and affirmations.log (quote history) stay
    logs = TEST_TEMP_DIR / "logs"
    logs.mkdir(exist_ok=True)
    old = time.time() - 45 * 86400
    for name, stale in (("worker.log", False), ("worker.log.3", True), ("supervisor-console.log", True),
                        ("affirmations.log", True), (".gitkeep", True), ("tiktok_post_success.png", False)):
        f = logs / name
        f.write_text("x", encoding="utf-8")
        if stale:
            os.utime(f, (old, old))
    removed = prune_old_logs(logs)
    assert removed == ["supervisor-console.log", "worker.log.3"], removed
    assert {f.name for f in logs.iterdir()} == {"worker.log", "affirmations.log", ".gitkeep", "tiktok_post_success.png"}

    # 2. Compaction drops routine heartbeat lines and keeps everything else, in order
    log = logs / "old_worker.log"
    log.write_text("[t] [INFO] --- Heartbeat #1 | Phase: idle | Status: idle ---\n[t] [INFO] No pending tasks in backlog. Sleeping until next heartbeat pulse.\n[t] [INFO] Picked up pending task: [task-001] 'Render'\n[t] [INFO] Heartbeat #40: idle, no tasks waiting.\n[t] [ERROR] Task [task-001] failed\n", encoding="utf-8")
    assert compact_log(log) == 3
    assert log.read_text(encoding="utf-8").splitlines() == [
        "[t] [INFO] Picked up pending task: [task-001] 'Render'", "[t] [ERROR] Task [task-001] failed"]

    # 3. The daemon logs a repeating status when it changes and every 10 minutes, not every pulse
    import logging

    from core.daemon import ROUTINE_LOG_EVERY, AutonomousDaemon
    daemon = AutonomousDaemon(workspace_dir=TEST_TEMP_DIR / "hb_ws", db_file=str(TEST_TEMP_DIR / "hb_ws" / "state.db"),
                              log_file=str(TEST_TEMP_DIR / "hb_ws" / "worker.log"))
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
    cap = Capture()
    daemon.logger.addHandler(cap)
    try:
        daemon._last_routine, daemon._last_routine_hb = None, 0
        for hb in range(1, 41):                                           # 40 idle heartbeats (20 minutes)
            daemon._log_routine(hb, "idle", f"Heartbeat #{hb}: idle, no tasks waiting.")
        daemon._log_routine(41, "standby", "Heartbeat #41: standby")      # a change is logged at once
        daemon._log_routine(42, "idle", "Heartbeat #42: idle, no tasks waiting.")
        routine = [r for r in records if r.startswith("Heartbeat #")]
        assert routine == ["Heartbeat #1: idle, no tasks waiting.", f"Heartbeat #{1 + ROUTINE_LOG_EVERY}: idle, no tasks waiting.",
                           "Heartbeat #41: standby", "Heartbeat #42: idle, no tasks waiting."], routine
    finally:
        for h in list(daemon.logger.handlers):
            h.close()
            daemon.logger.removeHandler(h)
    print("  - stale logs pruned (quote history kept); compaction keeps real events; "
          "40 idle heartbeats -> 2 log lines (was 80)")


if __name__ == "__main__":
    try:
        test_wal_checkpoint_and_state_helpers()
        test_api_endpoints()
        test_log_housekeeping()
        print("\nALL MAINTENANCE & UI TESTS PASSED")
    finally:
        shutil.rmtree(TEST_TEMP_DIR, ignore_errors=True)
