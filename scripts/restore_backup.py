"""
Restore a memory backup
=======================
  python scripts/restore_backup.py --list                 show the snapshots on this PC and on Google Drive
  python scripts/restore_backup.py --latest               unpack the newest into data/restored/ to inspect
  python scripts/restore_backup.py state-20260924-030000.db.gz
  python scripts/restore_backup.py --latest --replace-live

--replace-live puts the snapshot in place of data/state.db. Stop the agent first (it refuses
while the agent is running); the current database is kept as data/state.db.before-restore-<time>.
"""

import argparse
import datetime
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import psutil

from core import backup
from core.database import DB_FILE

AGENT_SCRIPTS = ("supervisor.py", "daemon.py", r"hud\server.py", "hud/server.py", "interfaces.hud")


def _folders():
    folders = [backup.LOCAL_BACKUP_DIR]
    mirror = backup.mirror_dir()
    if mirror is not None:
        folders.append(mirror)
    return folders


def _all_backups():
    """Every snapshot by name (newest first); a name in both places resolves to the local copy."""
    found = {}
    for folder in _folders():
        for path in backup.list_backups(folder):
            found.setdefault(path.name, path)
    return [found[name] for name in sorted(found, reverse=True)]


def _agent_running() -> bool:
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
        except (psutil.Error, TypeError):
            continue
        if any(script in cmd for script in AGENT_SCRIPTS):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore an AlwaysOnAgent memory backup")
    parser.add_argument("snapshot", nargs="?", help="snapshot file name or path")
    parser.add_argument("--list", action="store_true", help="list the available snapshots")
    parser.add_argument("--latest", action="store_true", help="use the newest snapshot")
    parser.add_argument("--replace-live", action="store_true", help="replace data/state.db (agent must be stopped)")
    args = parser.parse_args()

    snapshots = _all_backups()
    if args.list or not (args.snapshot or args.latest):
        if not snapshots:
            print("No backups found in: " + ", ".join(str(f) for f in _folders()))
        for path in snapshots:
            print(f"{path.name}  {path.stat().st_size / 1e6:6.1f} MB  {path.parent}")
        return 0

    if args.latest:
        if not snapshots:
            print("No backups found.")
            return 1
        archive = snapshots[0]
    else:
        archive = Path(args.snapshot)
        if not archive.is_file():
            archive = next((p for p in snapshots if p.name == args.snapshot), None)
            if archive is None:
                print(f"Snapshot not found: {args.snapshot}")
                return 1

    if not args.replace_live:
        target = DB_FILE.parent / "restored" / archive.name.replace(backup.NAME_SUFFIX, ".db")
        backup.restore_backup(archive, target)
        print(f"Restored {archive.name} to {target} (integrity check passed).")
        print("The live database was not touched. Use --replace-live to swap it in.")
        return 0

    if _agent_running():
        print("The agent is running. Stop it first, then run this again.")
        return 1
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    staged = DB_FILE.with_name(f"state.db.restoring-{stamp}")
    backup.restore_backup(archive, staged)              # unpack and check before touching anything
    for suffix in ("", "-wal", "-shm"):
        current = DB_FILE.with_name(DB_FILE.name + suffix)
        if current.exists():
            current.rename(current.with_name(f"{current.name}.before-restore-{stamp}"))
    staged.replace(DB_FILE)
    print(f"Restored {archive.name} as the live database.")
    print(f"The previous database was kept as {DB_FILE.name}.before-restore-{stamp}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
