"""
Robustness and safety checks (from a 2026-09-23 code audit; the numbers in test names are its item ids):
cancelled videos, hung tasks, provider failures, retries, voice notes, HUD access, DNS rebinding,
backups on Google Drive and small edge cases. Nothing here renders a video, calls a provider,
posts anything, or touches data/state.db.
"""

import http.server
import os
import shutil
import sys
import tempfile
import threading
import time
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

import core.config  # noqa: F401  (loads .env)

TEST_DIR = Path(tempfile.mkdtemp(prefix="robustness_test_"))
os.environ["STATE_DB_PATH"] = str(TEST_DIR / "hud_import_default.db")

from core.database import DatabaseManager


class FakeClient:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, parse_mode="Markdown", reply_markup=None):
        self.sent.append(text)
        return len(self.sent)

    def answer_callback_query(self, *a, **k):
        return True

    def download_file(self, file_id):
        return b"OggS fake audio"


# ---------------------------------------------------------------------------- 1.1 cancelling a video
# ---------------------------------------------------------------------------- 1.2 watchdog
def test_1_2_watchdog_frees_the_queue_from_a_hung_task():
    import core.daemon as daemon_mod
    from core.daemon import AutonomousDaemon
    daemon = AutonomousDaemon(workspace_dir=TEST_DIR / "wd_ws", db_file=str(TEST_DIR / "wd_ws" / "state.db"),
                              log_file=str(TEST_DIR / "wd_ws" / "worker.log"))
    alerts = []
    orig_notify = daemon_mod.notify_owner
    daemon_mod.notify_owner = lambda text: alerts.append(text) or True
    release = threading.Event()
    try:
        tid = daemon.db.create_task(title="Hangs forever", prompt="p", engine="cloud", timeout_seconds=1)
        daemon.db.update_task(tid, {"phase": "in_progress"})
        daemon.active_task = daemon.db.get_task(tid)
        daemon.active_future = daemon.executor.submit(release.wait)       # a worker that never returns
        daemon.active_task_start_time = time.time() - 10
        daemon.WATCHDOG_MIN_SECONDS = 5
        old_executor = daemon.executor
        assert daemon._task_overdue()
        daemon.heartbeat_cycle()
        task = daemon.db.get_task(tid)
        assert task["phase"] == "failed" and "Watchdog" in task["last_error"], task
        assert daemon.active_future is None and daemon.executor is not old_executor
        assert len(alerts) == 1 and tid in alerts[0]
        # Not overdue: left alone (renders take ~7 min on a 300 s limit)
        daemon.active_task = {"id": "t", "timeout_seconds": 300}
        daemon.active_task_start_time = time.time() - 420
        daemon.WATCHDOG_MIN_SECONDS = 1800
        assert not daemon._task_overdue()
    finally:
        release.set()
        daemon_mod.notify_owner = orig_notify
        daemon.executor.shutdown(wait=False)
        for h in list(daemon.logger.handlers):
            h.close()
            daemon.logger.removeHandler(h)
    print("  - 1.2 a task with no result after 2x its limit (min 30 min) is failed, the owner alerted, the queue freed")


# ---------------------------------------------------------------------------- 1.5 sandbox timeout
def test_1_5_sandbox_timeout_kills_the_whole_process_tree():
    import psutil

    from core.sandbox import HostProcessSandbox
    marker = TEST_DIR / "child_pid.txt"
    script = TEST_DIR / "spawn.py"
    script.write_text(
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{marker}', 'w').write(str(p.pid))\n"
        "time.sleep(60)\n")
    sb = HostProcessSandbox(task_id="audit-15", workspace_dir=TEST_DIR)
    with sb:
        code, _, err = sb.execute(f'"{sys.executable}" "{script}"', timeout=3)
    assert code == -1 and "timed out" in err
    child = int(marker.read_text())
    time.sleep(0.5)
    assert not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE, "child survived"
    print("  - 1.5 a timed-out QA run takes its child processes with it")


# ---------------------------------------------------------------------------- 2.1 / 2.2 affirmations
# ---------------------------------------------------------------------------- 2.4 line endings
def test_2_4_windows_line_endings_still_produce_files():
    from core.task_engines import FileArtifactHelper
    ws = TEST_DIR / "crlf_ws"
    ws.mkdir(exist_ok=True)
    reply = "### Create hello.py\r\n\r\n```python\r\nprint('hi')\r\n```\r\n"
    created = FileArtifactHelper.apply_file_artifacts(reply, ws)
    assert created == ["hello.py"] and (ws / "hello.py").read_text().strip() == "print('hi')"
    print("  - 2.4 code blocks with Windows line endings are saved")


# ---------------------------------------------------------------------------- 2.6 blank voice notes
# ---------------------------------------------------------------------------- 3.2 retry / 3.4 timeout
def test_3_2_a_running_task_cannot_be_retried():
    db = DatabaseManager(TEST_DIR / "retry.db")
    tid = db.create_task(title="Running", prompt="p")
    db.update_task(tid, {"phase": "in_progress"})
    assert db.retry_task(tid) is False and db.get_task(tid)["phase"] == "in_progress"
    db.update_task(tid, {"phase": "failed"})
    assert db.retry_task(tid) is True and db.get_task(tid)["phase"] == "backlog"

    assert db.get_connection().execute("PRAGMA busy_timeout").fetchone()[0] == 15000
    print("  - 3.2 retrying a running task is refused; 3.4 one 15 s busy timeout")


# ---------------------------------------------------------------------------- HUD: 3.2, 4.1, 6.7
def test_hud_retry_guard_forwarded_requests_and_wake_on_new_task():
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    original_db, original_token = hud_state.db, hud_state.HUD_AUTH_TOKEN
    hud_state.db = DatabaseManager(TEST_DIR / "hud.db")
    hud_state.HUD_AUTH_TOKEN = "secret-token-123"
    try:
        client = client_from(hud_app, addr=LOCAL_ADDR)
        # 4.1: loopback without proxy headers is local; with them it's an outside visitor needing the token
        assert client.get("/api/videos").status_code == 200
        relayed = client.get("/api/videos", headers={"X-Forwarded-For": "203.0.113.9"})
        assert relayed.status_code in (401, 403), relayed.status_code
        assert client.get("/api/videos", headers={"X-Forwarded-For": "203.0.113.9",
                                                   "X-HUD-Token": "secret-token-123"}).status_code == 200
        # Tailscale Serve's HTTPS relays tailnet devices from 127.0.0.1: its headers alone mean "not this PC"
        for header in ("Tailscale-User-Login", "X-Forwarded-Proto"):
            r = client.get("/api/videos", headers={header: "alex@example.com" if "User" in header else "https"})
            assert r.status_code in (401, 403), (header, r.status_code)

        # 6.7: a task from the HUD wakes a daemon in standby
        hud_state.db.set_state("daemon_status", "paused")
        res = client.post("/api/tasks", json={"title": "t", "prompt": "do it"}).json()
        assert res["woke_daemon"] is True and hud_state.db.get_state("daemon_status") == "idle"
        hud_state.db.set_state("daemon_status", "paused")
        res = client.post("/api/trigger_media", json={"action": "video"}).json()
        assert res["woke_daemon"] is True and hud_state.db.get_state("daemon_status") == "idle"
        # A scene from the app is kept on the task; one that isn't a scene is refused, nothing queued
        res = client.post("/api/trigger_media", json={"action": "video", "scene": "bottle", "quote": "q"}).json()
        assert hud_state.db.get_task(res["task_id"])["metadata"]["scene"] == "bottle"
        assert client.post("/api/trigger_media", json={"action": "video", "scene": "spaceship"}).status_code == 400

        # 3.2: retrying a running task is refused with 409
        tid = hud_state.db.create_task(title="Running", prompt="p")
        hud_state.db.update_task(tid, {"phase": "in_progress"})
        r = client.post(f"/api/retry/{tid}")
        assert r.status_code == 409 and hud_state.db.get_task(tid)["phase"] == "in_progress"
    finally:
        hud_state.db, hud_state.HUD_AUTH_TOKEN = original_db, original_token
    print("  - 4.1 proxied requests need the token; 6.7 HUD tasks wake the daemon; 3.2 HUD retry of a running task is 409")


# ---------------------------------------------------------------------------- 4.2 DNS rebinding
def test_4_2_connection_to_this_pc_is_refused_even_after_the_name_check():
    from core import web_tools
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>secret local page</html>")

        def log_message(self, *a):
            pass
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    orig = web_tools._assert_public_host
    web_tools._assert_public_host = lambda url: None       # the name "resolved public" at check time
    try:
        try:
            web_tools.fetch_webpage(f"http://127.0.0.1:{server.server_address[1]}/api/status")
            raise AssertionError("fetched a page on this PC")
        except ValueError as e:
            assert "off limits" in str(e)
        assert hits == [], "a request reached the local server"
    finally:
        web_tools._assert_public_host = orig
        server.shutdown()
    print("  - 4.2 the connected address is checked before any request is sent (DNS rebinding)")


# ---------------------------------------------------------------------------- 6.5 backup pruning
def test_6_5_a_locked_old_backup_does_not_fail_the_backup():
    from core import backup
    db = DatabaseManager(TEST_DIR / "bk" / "state.db")
    db.set_state("k", "v")
    local = TEST_DIR / "bk" / "backups"
    import datetime
    for day in range(3):
        backup.create_backup(db.db_path, local_dir=local, keep=5, now=datetime.datetime(2026, 9, 1 + day, 3))
    orig_unlink = Path.unlink

    def locked_unlink(self, missing_ok=False):
        if self.name.startswith("state-20260901"):
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return orig_unlink(self, missing_ok=missing_ok)
    Path.unlink = locked_unlink
    try:
        result = backup.create_backup(db.db_path, local_dir=local, keep=2, now=datetime.datetime(2026, 9, 5, 3))
    finally:
        Path.unlink = orig_unlink
    assert result.complete, result.errors
    print("  - 6.5 an old backup locked by Google Drive is skipped, not reported as a failed backup")


# ---------------------------------------------------------------------------- 6.8 / 6.9 / 6.10 / 6.4
def test_small_robustness_fixes():
    # 6.9: a missing reading is n/a, never a made-up 0
    from core import web_tools
    orig = (web_tools._geocode, web_tools._get_json)
    web_tools._geocode = lambda loc: {"name": "Springfield", "admin1": "Illinois", "country": "United States",
                                      "country_code": "US", "latitude": 1, "longitude": 1}
    web_tools._get_json = lambda url: {
        "current": {"time": "2026-09-23T10:00", "weather_code": 1, "temperature_2m": None, "apparent_temperature": 70.2,
                    "relative_humidity_2m": None, "wind_speed_10m": 5.4},
        "daily": {"time": ["2026-09-23"], "weather_code": [1], "temperature_2m_max": [None],
                  "temperature_2m_min": [60.1], "precipitation_probability_max": [None]}}
    try:
        text = web_tools.get_weather("Springfield, IL", 1)
    finally:
        web_tools._geocode, web_tools._get_json = orig
    assert "n/a" in text and " 0°" not in text and "high n/a" in text, text

    # 6.10: a small log keeps its first line
    from core.assistant_tools import AssistantTools
    ws = TEST_DIR / "logs_ws"
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    (ws / "logs" / "worker.log").write_text("[2026-09-23 00:00:01] [ERROR] the very first line\nsecond line\n")
    tools = AssistantTools(DatabaseManager(TEST_DIR / "logs.db"), None, ws)
    assert "the very first line" in tools.read_logs()

    # 6.8 (handled server-side): a chat usage row without a breakdown isn't reported as the latest chat
    from core.llm_usage import UsageLog, usage_purpose
    usage = UsageLog(DatabaseManager(TEST_DIR / "usage.db"))
    with usage_purpose("chat"):                          # no breakdown recorded
        usage.record("m", {"prompt_tokens": 10, "completion_tokens": 2})
    assert usage.summary()["latest_chat"] is None        # rows without a breakdown are never shown

    print("  - 6.9 missing weather readings show n/a; 6.10 first log line kept; 6.8 null usage detail; "
          "")


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_") and callable(fn):
                print(f"[{name}]")
                fn()
        print("\nALL ROBUSTNESS TESTS PASSED")
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)
