"""
Tests for memory backups: consistent snapshots of a live WAL database, the mirror copy,
pruning, the daily schedule, restore, and keeping test databases out of the mirror.
"""

import datetime
import os
import shutil
import sqlite3
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

from core import backup
from core.database import DatabaseManager

TEST_DIR = Path(tempfile.mkdtemp(prefix="backup_test_"))


def _live_db(name: str) -> DatabaseManager:
    """A database with committed rows still sitting in its WAL file (like the running agent's)."""
    db = DatabaseManager(TEST_DIR / name / "state.db")
    db.get_connection().execute("PRAGMA wal_autocheckpoint = 0")
    for i in range(50):
        db.set_state(f"memory_{i}", f"fact number {i}")
    return db


def _read_state(db_file: Path, key: str):
    conn = sqlite3.connect(str(db_file))
    try:
        row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_snapshot_includes_uncheckpointed_writes_and_mirrors():
    db = _live_db("snap")
    assert (db.db_path.with_name("state.db-wal")).stat().st_size > 0      # rows really are only in the WAL
    local, mirror = TEST_DIR / "snap" / "backups", TEST_DIR / "drive" / "AlwaysOnAgent Backups"
    result = backup.create_backup(db.db_path, local_dir=local, mirror=mirror)
    assert result.complete, result.errors
    assert [p.parent for p in result.saved_to] == [local, mirror]
    assert (local / result.name).read_bytes() == (mirror / result.name).read_bytes()
    assert not list(mirror.glob("*.partial"))

    restored = TEST_DIR / "snap" / "restored.db"
    backup.restore_backup(local / result.name, restored)
    assert _read_state(restored, "memory_49") == "fact number 49"
    print(f"  - a snapshot of a busy database holds every committed write ({result.size_bytes} bytes gzipped)")


def test_every_mirror_copy_is_restored_and_checked(monkeypatch):
    """The nightly restore drill: a Drive copy that doesn't unpack to the snapshot's messages makes the backup
    incomplete (so the owner is told)."""
    db = _live_db("drill")
    conn = db.get_connection()
    conn.execute("CREATE TABLE mem_messages (id INTEGER PRIMARY KEY, content TEXT)")
    conn.executemany("INSERT INTO mem_messages (content) VALUES (?)", [("m",)] * 7)
    conn.commit()
    mirror = TEST_DIR / "drill-drive"
    assert backup.create_backup(db.db_path, local_dir=TEST_DIR / "drill" / "backups", mirror=mirror).complete

    real_copy = backup._copy_complete

    def corrupting_copy(src, folder):
        final = real_copy(src, folder)
        if folder == mirror:
            final.write_bytes(final.read_bytes()[:200])          # a truncated upload
        return final

    monkeypatch.setattr(backup, "_copy_complete", corrupting_copy)
    result = backup.create_backup(db.db_path, local_dir=TEST_DIR / "drill" / "backups", mirror=mirror,
                                  now=datetime.datetime(2026, 10, 5, 3, 0))
    assert not result.complete and any("didn't restore cleanly" in e for e in result.errors), result.errors
    print("  - every night the Drive copy is restored and checked; a broken one is reported")


def test_keeps_only_the_newest():
    db = _live_db("prune")
    local = TEST_DIR / "prune" / "backups"
    start = datetime.datetime(2026, 9, 1, 3, 0, 0)
    for day in range(5):
        backup.create_backup(db.db_path, local_dir=local, keep=3, now=start + datetime.timedelta(days=day))
    names = [p.name for p in backup.list_backups(local)]
    assert names == ["state-20260905-030000.db.gz", "state-20260904-030000.db.gz", "state-20260903-030000.db.gz"], names
    print("  - only the newest BACKUP_KEEP snapshots are kept")


def test_missing_mirror_drive_keeps_the_local_copy():
    db = _live_db("nodrive")
    local = TEST_DIR / "nodrive" / "backups"
    result = backup.create_backup(db.db_path, local_dir=local, mirror=Path("Q:/My Drive/AlwaysOnAgent Backups"))
    assert result.ok and not result.complete
    assert result.saved_to == [local / result.name]
    assert "Google Drive running" in result.errors[0]
    print("  - if Google Drive isn't there, the local backup still happens and the problem is reported")


def test_missing_database_fails_cleanly():
    result = backup.create_backup(TEST_DIR / "nothing" / "state.db", local_dir=TEST_DIR / "nothing" / "backups")
    assert not result.ok and "doesn't exist" in result.errors[0]
    assert not (TEST_DIR / "nothing" / "state.db").exists()             # didn't create an empty database
    print("  - a missing database is reported, not silently backed up as empty")


def test_restore_refuses_to_overwrite_and_rejects_corruption():
    db = _live_db("restore")
    local = TEST_DIR / "restore" / "backups"
    result = backup.create_backup(db.db_path, local_dir=local)
    target = TEST_DIR / "restore" / "copy.db"
    backup.restore_backup(local / result.name, target)
    try:
        backup.restore_backup(local / result.name, target)
        raise AssertionError("overwrote an existing file")
    except FileExistsError:
        pass

    import gzip
    bad = local / "state-20990101-000000.db.gz"
    with gzip.open(bad, "wb") as f:
        f.write(b"SQLite format 3\x00" + b"\x00" * 200)
    try:
        backup.restore_backup(bad, TEST_DIR / "restore" / "bad.db")
        raise AssertionError("restored a corrupt snapshot")
    except Exception as e:
        assert not isinstance(e, AssertionError), e
    assert not (TEST_DIR / "restore" / "bad.db").exists()
    print("  - restore never overwrites a file and rejects a damaged snapshot")


def test_daily_schedule():
    db = DatabaseManager(TEST_DIR / "sched" / "state.db")
    at = datetime.datetime
    assert backup.backup_due(db, at(2026, 9, 23, 18, 0))                        # never backed up: now

    ok = backup.BackupResult(ok=True, name="x")
    backup.record_result(db, ok, now=at(2026, 9, 23, 18, 0))
    assert not backup.backup_due(db, at(2026, 9, 24, 2, 59))                    # next is 03:00
    assert backup.backup_due(db, at(2026, 9, 24, 3, 0))
    assert backup.backup_due(db, at(2026, 9, 25, 14, 0))                        # PC was off at 3: catch up

    backup.record_result(db, ok, now=at(2026, 9, 24, 3, 0))
    assert not backup.backup_due(db, at(2026, 9, 24, 23, 0))
    assert backup.backup_due(db, at(2026, 9, 25, 3, 0))

    failed = backup.BackupResult(ok=False, errors=["disk full"])
    backup.record_result(db, failed, now=at(2026, 9, 25, 3, 0))
    assert not backup.backup_due(db, at(2026, 9, 25, 3, 30))                    # retry hourly, not every heartbeat
    assert backup.backup_due(db, at(2026, 9, 25, 4, 0))
    print("  - daily at BACKUP_HOUR, catches up after downtime, retries failures hourly")


def test_daemon_backs_up_test_databases_locally_only():
    from core.daemon import AutonomousDaemon
    calls = []
    real = backup.create_backup

    def spy(db_path, local_dir=backup.LOCAL_BACKUP_DIR, mirror=None, **kw):
        calls.append((Path(db_path), Path(local_dir), mirror))
        return real(db_path, local_dir=local_dir, mirror=None, **kw)

    backup.create_backup = spy
    try:
        daemon = AutonomousDaemon(workspace_dir=_ROOT, db_file=str(TEST_DIR / "daemon" / "state.db"),
                                  log_file=str(TEST_DIR / "daemon" / "worker.log"))
        daemon.db.set_state("daemon_status", "paused")
        daemon.heartbeat_cycle()
        daemon._backup_thread.join(timeout=30)
        daemon.heartbeat_cycle()                                                 # already done today
    finally:
        backup.create_backup = real
    assert len(calls) == 1, calls
    _db_path, local_dir, mirror = calls[0]
    assert local_dir == TEST_DIR / "daemon" / "backups" and mirror is None
    assert backup.last_result(daemon.db)["ok"]
    for handler in list(daemon.logger.handlers):
        handler.close()
        daemon.logger.removeHandler(handler)
    print("  - the daemon backs up in standby too; test databases never reach the mirror")


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_") and callable(fn):
                print(f"[{name}]")
                fn()
        print("\nALL BACKUP TESTS PASSED")
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)
