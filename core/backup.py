"""
Memory backups
==============
Snapshots data/state.db (memory, conversation, tasks, usage) while the agent is running,
using SQLite's online backup API, so a snapshot is always consistent even mid-write.

- Each snapshot is integrity-checked, gzipped and saved as state-YYYYMMDD-HHMMSS.db.gz in
  data/backups/ and, when BACKUP_MIRROR_DIR is set, copied there too (e.g. a Google Drive
  for desktop folder, which uploads it).
- Copies are written under a temporary name and renamed when complete, so a sync client
  never uploads half a file.
- The newest BACKUP_KEEP snapshots (default 14) are kept in each place.
- The daemon takes one a day at BACKUP_HOUR (default 3, local time), or on the next heartbeat
  after that if the PC was off; DeskAI's Backup button (POST /api/backup) takes one on demand.

Secrets (.env) are never backed up. Restore with scripts/restore_backup.py.
"""

import datetime
import gzip
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from core.config import get_config, notify_owner
except ImportError:
    from config import get_config, notify_owner

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
LOCAL_BACKUP_DIR = WORKSPACE_DIR / "data" / "backups"
NAME_PREFIX, NAME_SUFFIX = "state-", ".db.gz"
STATE_KEY = "last_backup"        # system_state: JSON of the last attempt


@dataclass
class BackupResult:
    ok: bool                               # the local snapshot exists (the mirror may still have failed)
    name: str = ""
    size_bytes: int = 0
    saved_to: List[Path] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.ok and not self.errors

    def summary(self) -> str:
        if not self.ok:
            return "Backup failed: " + "; ".join(self.errors)
        where = ", ".join(str(p) for p in self.saved_to)
        text = f"Backup {self.name} ({self.size_bytes / 1e6:.1f} MB) saved to {where}"
        return text + (" | problems: " + "; ".join(self.errors) if self.errors else "")


def mirror_dir() -> Optional[Path]:
    value = get_config("BACKUP_MIRROR_DIR", "").strip().strip('"')
    return Path(value) if value else None


def keep_count() -> int:
    try:
        return max(1, int(get_config("BACKUP_KEEP", "14")))
    except ValueError:
        return 14


def backup_hour() -> int:
    try:
        return min(23, max(0, int(get_config("BACKUP_HOUR", "3"))))
    except ValueError:
        return 3


def list_backups(folder: Path) -> List[Path]:
    """Snapshots in a folder, newest first."""
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"{NAME_PREFIX}*{NAME_SUFFIX}"), key=lambda p: p.name, reverse=True)


def _prune(folder: Path, keep: int) -> None:
    for old in list_backups(folder)[keep:]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass            # e.g. Google Drive has it open while syncing; it's removed on a later run


def _integrity_ok(db_file: Path) -> Tuple[bool, str]:
    conn = sqlite3.connect(str(db_file))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return result == "ok", result
    finally:
        conn.close()


def _copy_complete(src: Path, folder: Path) -> Path:
    """Copies src into folder under a temporary name, then renames it into place."""
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / src.name
    partial = folder / (src.name + ".partial")
    shutil.copyfile(src, partial)
    partial.replace(final)
    return final


def create_backup(db_path: Path, local_dir: Path = LOCAL_BACKUP_DIR, mirror: Optional[Path] = None,
                  keep: Optional[int] = None, now: Optional[datetime.datetime] = None) -> BackupResult:
    """Takes one snapshot into local_dir and, when given, the mirror folder (see mirror_dir())."""
    keep = keep or keep_count()
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
    name = f"{NAME_PREFIX}{stamp}{NAME_SUFFIX}"
    result = BackupResult(ok=False, name=name)

    with tempfile.TemporaryDirectory(prefix="aoa-backup-") as tmp:
        raw = Path(tmp) / "state.db"
        packed = Path(tmp) / name
        try:
            if not Path(db_path).is_file():
                raise FileNotFoundError(f"{db_path} doesn't exist")
            src = sqlite3.connect(str(db_path), timeout=15)
            dst = sqlite3.connect(str(raw))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            good, detail = _integrity_ok(raw)
            if not good:
                result.errors.append(f"snapshot failed its integrity check ({detail})")
                return result
            with open(raw, "rb") as f_in, gzip.open(packed, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out)
            result.size_bytes = packed.stat().st_size
        except Exception as e:
            result.errors.append(f"couldn't snapshot the database: {e}")
            return result

        try:
            result.saved_to.append(_copy_complete(packed, Path(local_dir)))
            _prune(Path(local_dir), keep)
            result.ok = True
        except Exception as e:
            result.errors.append(f"this PC ({local_dir}): {e}")

        if mirror is not None:
            try:
                if not Path(mirror.anchor or ".").exists():
                    raise FileNotFoundError(f"{mirror.anchor} isn't available (is Google Drive running?)")
                copied = _copy_complete(packed, mirror)
                result.saved_to.append(copied)
                _prune(mirror, keep)
                result.ok = True
                # A restore drill every night (docs/IDEAS.md #13): the copy on Drive must unpack and hold what
                # the snapshot holds, or the backup counts as incomplete and the owner is told
                problem = _restore_check(copied, raw, Path(tmp) / "drill.db")
                if problem:
                    result.errors.append(f"mirror copy didn't restore cleanly: {problem}")
            except Exception as e:
                result.errors.append(f"mirror ({mirror}): {e}")
    return result


def _message_count(db_file: Path) -> Optional[int]:
    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM mem_messages").fetchone()[0]
    except sqlite3.OperationalError:
        return None                                     # no memory tables (a bare test database)
    finally:
        conn.close()


def _restore_check(archive: Path, snapshot: Path, scratch: Path) -> str:
    """'' when archive restores to a sound database with the snapshot's messages; otherwise what's wrong."""
    try:
        restore_backup(archive, scratch)
        restored, expected = _message_count(scratch), _message_count(snapshot)
        if restored != expected:
            return f"{restored} messages instead of {expected}"
        return ""
    except Exception as e:
        return str(e)
    finally:
        scratch.unlink(missing_ok=True)


def restore_backup(archive: Path, target: Path) -> None:
    """Unpacks a snapshot to target and checks it. Refuses to overwrite an existing file."""
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    with gzip.open(archive, "rb") as f_in, open(partial, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    good, detail = _integrity_ok(partial)
    if not good:
        partial.unlink(missing_ok=True)
        raise ValueError(f"{archive.name} failed its integrity check ({detail})")
    partial.replace(target)


# ---------------------------------------------------------------------- scheduling & status
def record_result(db, result: BackupResult, now: Optional[datetime.datetime] = None) -> None:
    db.set_state(STATE_KEY, json.dumps({
        "at": (now or datetime.datetime.now()).isoformat(timespec="seconds"),
        "ok": result.ok, "complete": result.complete, "name": result.name,
        "size_bytes": result.size_bytes, "saved_to": [str(p) for p in result.saved_to],
        "errors": result.errors,
    }))


def last_result(db) -> Optional[dict]:
    try:
        return json.loads(db.get_state(STATE_KEY, "") or "null")
    except ValueError:
        return None


def backup_due(db, now: Optional[datetime.datetime] = None) -> bool:
    """True once the daily backup time has passed since the last successful snapshot."""
    now = now or datetime.datetime.now()
    last = last_result(db)
    if not last or not last.get("ok"):
        # never taken, or the last try failed: retry, but not more than once an hour
        if last and last.get("at"):
            return now - datetime.datetime.fromisoformat(last["at"]) >= datetime.timedelta(hours=1)
        return True
    last_at = datetime.datetime.fromisoformat(last["at"])
    due = last_at.replace(hour=backup_hour(), minute=0, second=0, microsecond=0)
    if due <= last_at:
        due += datetime.timedelta(days=1)
    return now >= due


def notify_owner_of(text: str) -> bool:
    """Tells the owner (a push notification), e.g. about a failed backup."""
    return notify_owner(text)
