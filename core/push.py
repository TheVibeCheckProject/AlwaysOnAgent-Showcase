"""
Phone push notifications (Firebase Cloud Messaging)
===================================================
Sends short alerts to the DeskAI app, which registers its FCM token with POST /api/push/register.

- Setup: a Firebase project (free Spark plan), the DeskAI Android app added to it, and a service
  account key saved on this PC at FCM_SERVICE_ACCOUNT (default data/secrets/fcm-service-account.json).
  The key never leaves this PC; nothing is sent until it exists.
- Messages are data-only and high priority, so the app builds the notification itself (channel,
  tap target). Fields, all strings: type (task_completed | task_failed | alert | test), task_id,
  title, body, channel (tasks | alerts).
- Text passes through Google's servers, so it is kept short: a task's title and status, or a
  one-line alert. Never memory or chat content.
- Registered devices are kept in data/push_devices.json. A token FCM reports as no longer valid
  is removed.
- TaskPushWatcher sends a push when a task finishes or fails.
"""

import datetime
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from core.config import WORKSPACE_DIR, get_config
except ImportError:
    from config import WORKSPACE_DIR, get_config

logger = logging.getLogger("Push")

DEVICES_FILE = WORKSPACE_DIR / "data" / "push_devices.json"
DEFAULT_KEY_FILE = WORKSPACE_DIR / "data" / "secrets" / "fcm-service-account.json"
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
MAX_TITLE, MAX_BODY = 80, 200
MESSAGE_TYPES = ("task_completed", "task_failed", "alert", "test")

_lock = threading.Lock()
_credentials = None        # cached google.oauth2 service-account credentials


def key_file() -> Path:
    custom = get_config("FCM_SERVICE_ACCOUNT", "").strip()
    return Path(custom) if custom else DEFAULT_KEY_FILE


def is_configured() -> bool:
    return key_file().is_file()


# ---------------------------------------------------------------------------- devices
def _load_devices() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_devices(devices: Dict[str, Dict[str, Any]]):
    DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEVICES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(devices, indent=2), encoding="utf-8")
    tmp.replace(DEVICES_FILE)


def register_device(token: str, device_name: str = "") -> None:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        devices = _load_devices()
        entry = devices.get(token, {"registered_at": now})
        entry.update(device_name=(device_name or entry.get("device_name") or "")[:80], last_seen=now)
        devices[token] = entry
        _save_devices(devices)


def unregister_device(token: str) -> bool:
    with _lock:
        devices = _load_devices()
        if devices.pop(token, None) is None:
            return False
        _save_devices(devices)
        return True


def device_count() -> int:
    return len(_load_devices())


# ---------------------------------------------------------------------------- sending
def _access_token() -> Optional[str]:
    """A fresh OAuth token from the service account (cached until close to expiry)."""
    global _credentials
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    with _lock:
        if _credentials is None:
            _credentials = service_account.Credentials.from_service_account_file(str(key_file()), scopes=[FCM_SCOPE])
        if not _credentials.valid:
            _credentials.refresh(Request())
        return _credentials.token


def _project_id() -> str:
    return json.loads(key_file().read_text(encoding="utf-8")).get("project_id", "")


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def send(kind: str, title: str, body: str, task_id: str = "", channel: str = "tasks",
         extra: Optional[Dict[str, str]] = None) -> int:
    """Pushes to every registered device. Returns how many accepted it (0 if push isn't set up).
    extra: more data fields (strings), e.g. an agent_message's message_id."""
    devices = _load_devices()
    if not devices or not is_configured():
        return 0
    import requests
    try:
        url = f"https://fcm.googleapis.com/v1/projects/{_project_id()}/messages:send"
        headers = {"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"}
    except Exception as e:
        logger.warning(f"Push not sent (service account problem): {e}")
        return 0
    data = {"type": kind, "task_id": task_id or "", "title": _clip(title, MAX_TITLE),
            "body": _clip(body, MAX_BODY), "channel": channel,
            **{str(k): str(v) for k, v in (extra or {}).items()}}
    sent, stale = 0, []
    for token in devices:
        message = {"message": {"token": token, "data": data, "android": {"priority": "high"}}}
        try:
            r = requests.post(url, headers=headers, json=message, timeout=15)
        except Exception as e:
            logger.warning(f"Push to a device failed: {e}")
            continue
        if r.ok:
            sent += 1
        elif r.status_code == 404 or "UNREGISTERED" in r.text or "registration token" in r.text.lower():
            stale.append(token)              # the app was uninstalled or its token rotated
        else:
            logger.warning(f"Push rejected ({r.status_code}): {r.text[:200]}")
    for token in stale:
        unregister_device(token)
    if stale:
        logger.info(f"Removed {len(stale)} push device(s) that are no longer registered.")
    return sent


def send_alert(text: str) -> int:
    """A problem that needs attention (the same alerts notify_owner sends)."""
    return send("alert", "⚠️ AlwaysOnAgent", text, channel="alerts")


# ---------------------------------------------------------------------------- task watcher
class TaskPushWatcher:
    """Pushes a notification when a task finishes or fails. Tasks already finished when the
    agent starts are not announced again."""

    def __init__(self, db, interval: float = 3.0):
        self.db = db
        self.interval = interval
        self.running = True
        self.announced: set = set()
        self._seed()

    def _seed(self):
        for t in self.db.list_tasks(limit=100):
            if t.get("phase") in ("completed", "failed"):
                self.announced.add(t["id"])

    def check_once(self) -> int:
        """One pass; returns how many notifications went out."""
        pushed = 0
        for t in self.db.list_tasks(limit=25):
            tid, phase = t.get("id"), t.get("phase")
            if phase in ("backlog", "pending", "in_progress"):
                self.announced.discard(tid)          # a retried task may notify again
            elif phase in ("completed", "failed") and tid not in self.announced:
                self.announced.add(tid)
                if phase == "completed" and (t.get("metadata") or {}).get("announced"):
                    continue                          # it sent its own message (the weekly review): one push, not two
                title = t.get("title") or "Task"
                if phase == "completed" and t.get("needs_input"):
                    # the app's own task_completed type (it opens the task card), with a different heading
                    pushed += send("task_completed", "⚠️ Needs your input", title, task_id=tid)
                elif phase == "completed":
                    pushed += send("task_completed", "✅ Task finished", title, task_id=tid)
                elif t.get("cancelled") or "cancelled" in str(t.get("last_error") or "").lower():
                    continue                          # the user cancelled it; no need to tell them
                else:
                    pushed += send("task_failed", "❌ Task failed", title, task_id=tid)
        return pushed

    def run(self):
        while self.running:
            try:
                self.check_once()
            except Exception as e:
                logger.error(f"Task push watcher error: {e}")
            time.sleep(self.interval)
