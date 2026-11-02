"""
Worker tests: the daemon, the task queue and the task engines
=============================================================
Environment sanitization and the path-traversal guard, WAL concurrency, crash recovery, priority
queueing, the sandbox, engine routing and fallbacks, process-tree killing (Antigravity timeouts and
cancellation), log streaming, HUD endpoints, standby and auto-ignition,
cancel-vs-finalize, task ids and retry, secret redaction, storage maintenance, and task isolation
(task folders, the hidden GitHub login, the safety audit).
"""

import concurrent.futures
import os
import shutil
import sqlite3
import subprocess
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

import tempfile
from core.config import SENSITIVE_ENV_KEYS, get_safe_subprocess_env, redact_secrets
from core.database import DatabaseManager, PRIORITY_MAP
from core.task_engines import FileArtifactHelper
from core.sandbox import HostProcessSandbox, get_sandbox

TEST_DIR = Path(tempfile.mkdtemp(prefix="worker_test_"))

# core.config (imported above) has already loaded .env into os.environ, so this override sticks;
# The API server (interfaces/hud) opens its database at import time and must never touch data/state.db.
os.environ["STATE_DB_PATH"] = str(TEST_DIR / "hud_import_default.db")


def test_security_sanitization():
    print("[Security & Subprocess Sanitization]")

    # 1. Ensure none of the sensitive keys leak into safe env
    os.environ["TELEGRAM_BOT_TOKEN"] = "secret_bot_token_123"
    os.environ["TIKTOK_PASSWORD"] = "super_secret_pw"
    os.environ["GROQ_API_KEY"] = "gsk_test_api_key"

    safe_env = get_safe_subprocess_env({"EXTRA_VAR": "harmless_data"})
    for secret in SENSITIVE_ENV_KEYS:
        assert secret not in safe_env, f"Security Failure: {secret} leaked into safe subprocess environment!"
    assert safe_env.get("EXTRA_VAR") == "harmless_data", "Safe caller extra variable was not preserved"
    print("  - Environment variable sanitization verified: all secrets stripped.")

    # 2. Path Traversal Guard
    dummy_dir = (TEST_DIR / "nested" / "test_sandbox_traversal").resolve()
    dummy_dir.mkdir(parents=True, exist_ok=True)
    try:
        traversal_text = "### File: ../../secret_escape.txt\n```python\nprint('pwned')\n```"
        created = FileArtifactHelper.apply_file_artifacts(traversal_text, dummy_dir)
        escape_path = dummy_dir.parent.parent / "secret_escape.txt"
        assert not escape_path.exists(), "Security Failure: Path traversal escaped working directory!"
        assert len(created) == 0, f"Expected 0 created files on traversal, got {created}"
        print("  - Path traversal guard verified: relative directory escapes blocked.")
    finally:
        shutil.rmtree(dummy_dir, ignore_errors=True)


def test_sqlite_wal_engine():
    print("[SQLite WAL Mode & Multi-Thread Concurrency]")
    test_db_file = TEST_DIR / "test_concurrency.db"
    if test_db_file.exists():
        test_db_file.unlink()

    db = DatabaseManager(test_db_file)
    conn = db.get_connection()
    cur = conn.execute("PRAGMA journal_mode")
    journal_mode = cur.fetchone()[0]
    assert journal_mode.lower() == "wal", f"Expected WAL journal mode, got {journal_mode}"
    print(f"  - Verified SQLite journal mode: {journal_mode.upper()}")

    # Stress test concurrent writers and readers across 5 threads
    def worker_thread(thread_idx: int):
        thread_db = DatabaseManager(test_db_file)
        for i in range(10):
            tid = f"task-t{thread_idx}-{i}"
            thread_db.create_task({
                "id": tid,
                "title": f"Thread {thread_idx} Task {i}",
                "priority": "high",
                "prompt": "Work item"
            })
            # Immediate read
            fetched = thread_db.get_task(tid)
            assert fetched is not None and fetched["id"] == tid

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker_thread, idx) for idx in range(5)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    total_tasks = len(db.list_tasks(limit=100))
    assert total_tasks == 50, f"Expected 50 tasks created across threads, found {total_tasks}"
    print("  - 5 concurrent threads executed 50 writes and 50 reads without locking errors.")

    # Cleanup
    del db
    if test_db_file.exists():
        try:
            test_db_file.unlink()
            Path(str(test_db_file) + "-wal").unlink(missing_ok=True)
            Path(str(test_db_file) + "-shm").unlink(missing_ok=True)
        except Exception:
            pass


def test_startup_crash_reconciliation():
    print("[Startup Crash Reconciliation (Orphaned Tasks)]")
    test_db = TEST_DIR / "test_orphan.db"
    if test_db.exists():
        test_db.unlink()

    db = DatabaseManager(test_db)
    # Simulate a task stranded in 'in_progress' when daemon was killed
    db.create_task({
        "id": "crashed-task-1",
        "title": "Unfinished Task",
        "prompt": "Do something",
        "phase": "in_progress",
        "retry_count": 0,
        "max_retries": 2
    })

    # Run crash reconciliation
    recovered = db.reconcile_orphaned_tasks()
    assert "crashed-task-1" in recovered, "Orphaned task was not recovered"

    t = db.get_task("crashed-task-1")
    assert t["phase"] == "backlog", f"Expected backlog phase, got {t['phase']}"
    assert t["retry_count"] == 1, f"Expected retry count 1, got {t['retry_count']}"
    assert "Interrupted by daemon restart" in t["last_error"]
    print("  - Crash recovery verified: stranded in_progress task cleanly restored to backlog.")

    # A TikTok post interrupted mid-run is never re-run: TikTok may already have it
    db.create_task({"id": "crashed-post-1", "title": "Auto-Post to TikTok (Daily Affirmation)", "prompt": "post",
                    "phase": "in_progress", "retry_count": 0, "max_retries": 2,
                    "metadata": {"action": "tiktok"}})
    assert "crashed-post-1" in db.reconcile_orphaned_tasks()
    post = db.get_task("crashed-post-1")
    assert post["phase"] == "failed" and "check TikTok" in post["last_error"], post
    print("  - an interrupted TikTok post is marked failed, never re-queued (no double posts)")

    # Nor a computer task: the screen still holds its windows (2026-10-28 audit 3.1; computer tasks are the Mini's
    # most common kind, so a crash or a long deploy mid-run is the likeliest way to hit this)
    db.create_task({"id": "crashed-computer-1", "title": "Computer: fill in the form", "prompt": "fill it in",
                    "engine": "computer", "phase": "in_progress", "retry_count": 0, "max_retries": 2,
                    "metadata": {"action": "computer", "goal": "fill it in"}})
    assert "crashed-computer-1" in db.reconcile_orphaned_tasks()
    comp = db.get_task("crashed-computer-1")
    assert comp["phase"] == "failed" and "run it again from the card" in comp["last_error"], comp
    assert comp["retry_count"] == 0

    # Cleanup
    try:
        test_db.unlink(missing_ok=True)
        Path(str(test_db) + "-wal").unlink(missing_ok=True)
        Path(str(test_db) + "-shm").unlink(missing_ok=True)
    except Exception:
        pass


def test_priority_queueing():
    print("[Priority Queue Ordering]")
    test_db = TEST_DIR / "test_prio.db"
    if test_db.exists():
        test_db.unlink()

    db = DatabaseManager(test_db)
    db.create_task({"id": "low-1", "priority": "low", "title": "Low Task"})
    db.create_task({"id": "urgent-1", "priority": "urgent", "title": "Urgent Task"})
    db.create_task({"id": "high-1", "priority": "high", "title": "High Task"})

    next_task = db.get_next_backlog_task()
    assert next_task is not None
    assert next_task["id"] == "urgent-1", f"Expected urgent-1 first, got {next_task['id']}"
    print("  - Priority order verified: urgent prioritized ahead of high/low.")

    # Cleanup
    try:
        test_db.unlink(missing_ok=True)
        Path(str(test_db) + "-wal").unlink(missing_ok=True)
        Path(str(test_db) + "-shm").unlink(missing_ok=True)
    except Exception:
        pass


def test_sandbox_execution():
    print("[Execution Sandboxing]")
    sandbox = HostProcessSandbox(task_id="test-sb-01", workspace_dir=TEST_DIR)
    with sandbox:
        # Write a simple python file in the sandbox
        test_file = sandbox.sandbox_dir / "test_simple.py"
        test_file.write_text(
            "import unittest\n"
            "class SimpleTest(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        self.assertEqual(1 + 1, 2)\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8"
        )
        code, stdout, stderr = sandbox.execute("python test_simple.py", timeout=10)
        assert code == 0, f"Expected exit code 0, got {code} (stderr: {stderr})"
        print("  - Sandbox unit test execution passed with zero exit code.")

    # Cleanup sandbox directory
    shutil.rmtree(sandbox.sandbox_dir, ignore_errors=True)


def _close_daemon_logger():
    """Detaches the daemon's file handlers so temp log files can be deleted on Windows."""
    import logging
    daemon_logger = logging.getLogger("AlwaysOnAgent")
    for h in list(daemon_logger.handlers):
        daemon_logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass


class _StubRunner:
    """Records calls and returns a canned (exit_code, output)."""
    default_model = "stub-model"

    def __init__(self, code: int, out: str):
        self.code, self.out, self.calls = code, out, 0

    def execute_task(self, task, working_dir, *args):
        self.calls += 1
        self.working_dir = working_dir
        return self.code, self.out


def test_engine_dispatcher_and_ollama():
    print("[Multi-Engine Dispatcher Routing & Auto Fallback]")
    from core.daemon import EngineDispatcher

    dispatcher = EngineDispatcher(TEST_DIR / "dispatch_ws", task_root=TEST_DIR / "dispatch_ws" / "workspaces")
    alive, models = dispatcher.ollama.client.is_alive()
    print(f"  - Local Ollama status: {'ONLINE' if alive else 'OFFLINE'} (Models: {models})")

    def fresh_stubs(agy_code=0):
        dispatcher.antigravity = _StubRunner(agy_code, "agy-out")
        dispatcher.cloud = _StubRunner(0, "cloud-out")
        dispatcher.ollama = _StubRunner(0, "ollama-out")

    # Explicit engines route to exactly one engine; the older names hermes and groq mean cloud
    for engine, expected_out, expected_used in [
        ("antigravity", "agy-out", "antigravity"),
        ("ollama", "ollama-out", "ollama"),
        ("hermes", "cloud-out", "cloud"),
        ("groq", "cloud-out", "cloud"),
        ("cloud", "cloud-out", "cloud"),
    ]:
        fresh_stubs()
        code, out, used, fb = dispatcher.execute({"id": "t", "engine": engine, "prompt": "p"})
        assert (code, out, used, fb) == (0, expected_out, expected_used, False), f"{engine}: got {(code, out, used, fb)}"
    assert dispatcher.cloud.working_dir == TEST_DIR / "dispatch_ws" / "workspaces" / "t"
    print("  - Explicit routing verified for antigravity / cloud (and hermes, groq) / ollama, each in workspaces/<task id>.")

    # auto: Antigravity success -> no fallback
    fresh_stubs(agy_code=0)
    code, out, used, fb = dispatcher.execute({"id": "t", "engine": "auto", "prompt": "p"})
    assert (used, fb) == ("antigravity", False)
    assert dispatcher.cloud.calls == 0

    # auto: Antigravity failure -> cloud fallback flagged
    fresh_stubs(agy_code=127)
    code, out, used, fb = dispatcher.execute({"id": "t", "engine": "auto", "prompt": "p"})
    assert (code, out, used, fb) == (0, "cloud-out", "cloud_fallback", True)

    # Task engine missing -> default_engine applies
    fresh_stubs()
    _, _, used, _ = dispatcher.execute({"id": "t", "engine": None, "prompt": "p"}, default_engine="groq")
    assert used == "cloud"
    print("  - Auto fallback (antigravity -> cloud_fallback) and default_engine resolution verified.")


def test_cancelled_task_not_requeued_by_daemon():
    print("[Cancel vs. Daemon Finalization Race]")
    from core.daemon import AutonomousDaemon

    test_db_path = TEST_DIR / "test_cancel_race.db"
    daemon = AutonomousDaemon(workspace_dir=_ROOT, db_file=str(test_db_path), log_file=str(TEST_DIR / "cancel_worker.log"))
    db = daemon.db
    try:
        tid = db.create_task({"title": "Long task", "prompt": "p", "engine": "antigravity"})
        task = db.get_task(tid)
        db.update_task(tid, {"phase": "in_progress"})

        # User cancels while the worker is still running
        db.update_task(tid, {"phase": "failed", "last_error": "Cancelled manually", "exit_code": 137})

        # Killed process then returns a non-zero code; daemon must not resurrect the task
        daemon._finalize_task_completion(task, 1, "killed", "antigravity", False)
        after = db.get_task(tid)
        assert after["phase"] == "failed", f"Cancelled task was moved to '{after['phase']}'"
        assert after["last_error"] == "Cancelled manually"
        assert db.get_next_backlog_task() is None
        print("  - Cancelled task stays 'failed' after the worker finishes (no silent requeue).")

        # Normal path still retries a genuine first failure
        tid2 = db.create_task({"title": "Flaky task", "prompt": "p"})
        task2 = db.get_task(tid2)
        db.update_task(tid2, {"phase": "in_progress"})
        daemon._finalize_task_completion(task2, 1, "boom", "antigravity", False)
        assert db.get_task(tid2)["phase"] == "backlog"
        print("  - Genuine failures still requeue for retry.")

        # A failed TikTok post is never retried by itself: TikTok may have taken it after all
        tid3 = db.create_task({"title": "Post video to TikTok", "prompt": "p", "engine": "media_pipeline"})
        task3 = db.get_task(tid3)
        db.update_task(tid3, {"phase": "in_progress"})
        daemon._finalize_task_completion(task3, 1, "TikTok auto-upload failed", "social_publisher", False)
        after3 = db.get_task(tid3)
        assert after3["phase"] == "failed", f"A failed TikTok post was moved to '{after3['phase']}'"
        print("  - A failed TikTok post stays failed (no automatic second post).")

        # Nor a computer task: a second run acts on the first one's leftover windows (2026-10-10)
        tid4 = db.create_task({"title": "Computer: open Notepad", "prompt": "p", "engine": "computer"})
        task4 = db.get_task(tid4)
        db.update_task(tid4, {"phase": "in_progress"})
        daemon._finalize_task_completion(task4, 1, "double-check refused", "computer", False)
        assert db.get_task(tid4)["phase"] == "failed", "a failed computer task was requeued"
        print("  - A failed computer task stays failed (no automatic second run).")

        # The run's reason leads last_error, where list_tasks shows it (task-231, 2026-10-26: only "exit code: 1"
        # was there, and the chat invented a cause)
        tid5 = db.create_task({"title": "Computer: AI Studio", "prompt": "p", "engine": "computer"})
        task5 = db.get_task(tid5)
        db.update_task(tid5, {"phase": "in_progress"})
        daemon._finalize_task_completion(
            task5, 1, "The computer task couldn't run: JSONDecodeError: Extra data", "computer", False)
        err = db.get_task(tid5)["last_error"]
        assert err.startswith("The computer task couldn't run: JSONDecodeError"), err
        assert "exit code 1" in err
    finally:
        _close_daemon_logger()


def test_maintenance_spares_running_render():
    print("[Storage Maintenance Is Skipped While a Task Runs]")
    from core.daemon import AutonomousDaemon
    from core import tiktok_videos

    daemon = AutonomousDaemon(workspace_dir=_ROOT, db_file=str(TEST_DIR / "test_maint.db"),
                              log_file=str(TEST_DIR / "maint_worker.log"))
    calls = []
    saved = tiktok_videos.cleanup
    tiktok_videos.cleanup = lambda **kw: calls.append(("cleanup", kw)) or {}
    try:
        # Heartbeat #20 while a render is still running: no cleanup at all
        daemon.db.set_state("total_heartbeats", "19")
        daemon.active_future = concurrent.futures.Future()
        daemon.active_task = {"id": "t", "title": "render"}
        daemon.active_task_start_time = time.time()
        daemon.heartbeat_cycle()
        assert calls == [], f"Maintenance ran during an active task: {calls}"

        # Heartbeat #40 when idle: cleanup runs, but only for stale files
        daemon.active_future = None
        daemon.active_task = None
        daemon.db.set_state("total_heartbeats", "39")
        daemon.heartbeat_cycle()
        assert ("cleanup", {"older_than": 3600}) in calls, f"Unexpected maintenance calls: {calls}"
        print("  - Active render protected; idle maintenance only removes files older than 1 hour.")
    finally:
        tiktok_videos.cleanup = saved
        _close_daemon_logger()


def test_task_ids_and_manual_retry():
    print("[Task ID Generation & Unified Manual Retry]")
    db = DatabaseManager(TEST_DIR / "test_ids.db")

    # Custom IDs must not make auto IDs collide (old COUNT(*)+1 scheme did)
    db.create_task({"id": "task-002", "title": "custom", "prompt": "p"})
    a = db.create_task({"title": "a", "prompt": "p"})
    b = db.create_task({"title": "b", "prompt": "p"})
    assert a == "task-003" and b == "task-004", f"Got {a}, {b}"

    # Concurrent creators never collide
    def make(i):
        return DatabaseManager(TEST_DIR / "test_ids.db").create_task({"title": f"c{i}", "prompt": "p"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        ids = list(ex.map(make, range(24)))
    assert len(set(ids)) == 24, "Concurrent task creation produced duplicate IDs"
    print("  - Sequential IDs skip custom IDs; 24 concurrent creates produced unique IDs.")

    # Manual retry: fresh retry budget and blockers cleared
    db.update_task(a, {"phase": "failed", "retry_count": 2})
    db.add_blocker(task_id=a, message="failed")
    assert db.retry_task(a)
    t = db.get_task(a)
    assert t["phase"] == "backlog" and t["retry_count"] == 0
    assert not [bl for bl in db.get_active_blockers() if bl["task_id"] == a]
    print("  - Manual retry resets retry_count to 0 and clears the task's blockers.")


def test_secret_redaction():
    print("[Secret Redaction in Output & Logs]")
    import logging
    from core.daemon import SecretRedactionFilter

    os.environ["GROQ_API_KEY"] = "gsk_supersecretvalue_123"
    text = "calling api with gsk_supersecretvalue_123 now"
    assert "gsk_supersecretvalue_123" not in redact_secrets(text)
    assert "[REDACTED:GROQ_API_KEY]" in redact_secrets(text)

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "key=%s", ("gsk_supersecretvalue_123",), None)
    SecretRedactionFilter().filter(record)
    assert "gsk_supersecretvalue_123" not in record.getMessage()
    print("  - Secrets scrubbed from task output and log records.")


def test_any_secret_named_key_is_redacted_and_kept_from_child_processes(monkeypatch):
    """2026-10-28 audit 1.3: redaction knew only a fixed list, so LAPTOP_TAKEOVER_TOKEN (the laptop's mouse and
    keyboard) and AI_HORDE_API_KEY on the Mini went to logs and the cloud model as they were."""
    from core import config
    monkeypatch.setenv("LAPTOP_TAKEOVER_TOKEN", "lt_controls_the_laptop_42")
    monkeypatch.setitem(config._ENV_VALUES, "SOME_NEW_SERVICE_API_KEY", "nsk_brand_new_key_99")
    out = redact_secrets("t=lt_controls_the_laptop_42 k=nsk_brand_new_key_99")
    assert "lt_controls" not in out and "nsk_brand" not in out
    assert "[REDACTED:LAPTOP_TAKEOVER_TOKEN]" in out and "[REDACTED:SOME_NEW_SERVICE_API_KEY]" in out
    safe = config.get_safe_subprocess_env({"MY_TOKEN": "x" * 10, "RENDER_FPS": "30"})
    assert "MY_TOKEN" not in safe and safe["RENDER_FPS"] == "30"


def _write_hanging_cli(name: str) -> Path:
    """Creates a fake CLI executable that ignores its arguments and sleeps without output."""
    if sys.platform == "win32":
        fake = TEST_DIR / f"{name}.cmd"
        fake.write_text(f'@"{sys.executable}" -c "import time; time.sleep(60)"\r\n', encoding="utf-8")
    else:
        fake = TEST_DIR / f"{name}.sh"
        fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" -c "import time; time.sleep(60)"\n', encoding="utf-8")
        fake.chmod(0o755)
    return fake


def test_cloud_outage_falls_back_to_ollama():
    print("[Cloud engine: outage -> local Ollama (but not on QA failure)]")
    from core.llm import CloudLLM
    from core.task_engines import UNAVAILABLE_EXIT_CODE, CloudEngine

    # A real client against a closed port: a network failure is reported as "unavailable"
    llm = CloudLLM(keys={"groq": "gsk_test_unreachable"})
    llm.urls["groq"] = "http://127.0.0.1:9/openai/v1/chat/completions"
    ollama = _StubRunner(0, "ollama-out")
    engine = CloudEngine(llm, ollama)
    code, _ = engine.run_in_cloud({"id": "t", "prompt": "p", "timeout_seconds": 3}, TEST_DIR)
    assert code == UNAVAILABLE_EXIT_CODE, f"Expected unavailable code, got {code}"
    code, out = engine.execute_task({"id": "t", "prompt": "p", "timeout_seconds": 3}, TEST_DIR)
    assert (code, out) == (0, "ollama-out") and ollama.calls == 1
    print("  - Cloud outage (network error / rate limits) falls back to local Ollama.")

    ollama.calls = 0
    engine.run_in_cloud = lambda task, wd: (1, "Generated code failed sandbox verification tests")
    code, out = engine.execute_task({"id": "t", "prompt": "p"}, TEST_DIR)
    assert code == 1 and ollama.calls == 0
    print("  - A cloud answer that fails QA is reported as a failure without a local retry.")


def test_antigravity_is_cancellable():
    print("[Antigravity PID Recording & Process-Tree Cancellation]")
    from core.processes import kill_process_tree
    from core.task_engines import AntigravityEngine

    db = DatabaseManager(TEST_DIR / "test_agy_cancel.db")
    tid = db.create_task({"title": "Antigravity task", "prompt": "p", "engine": "antigravity", "phase": "in_progress"})
    engine = AntigravityEngine(custom_agy_path=str(_write_hanging_cli("fake_agy_cancel")), db=db)
    work = TEST_DIR / "agy_cancel_ws"
    work.mkdir(exist_ok=True)

    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("r", engine.execute_task({"id": tid, "prompt": "p", "timeout_seconds": 60}, work))
    )
    worker.start()

    pid = None
    deadline = time.time() + 10
    while time.time() < deadline and not pid:
        pid_info = db.get_active_task_pid()
        pid = pid_info[1] if pid_info else None
        time.sleep(0.1)
    assert pid, "Antigravity PID was never recorded for cancellation"

    start = time.time()
    kill_process_tree(pid)  # what /api/cancel does
    worker.join(timeout=15)
    assert not worker.is_alive(), "Antigravity engine did not return after its process tree was killed"
    code, _ = result["r"]
    assert code != 0, "A killed Antigravity run reported success"
    assert db.get_task(tid)["worker_pid"] is None, "worker_pid not cleared after run"
    print(f"  - Antigravity PID recorded; cancel killed it in {time.time() - start:.1f}s.")


def test_antigravity_timeout_without_output():
    print("[Antigravity Timeout on a Silent, Hung Process]")
    from core.task_engines import AntigravityEngine

    runner = AntigravityEngine(custom_agy_path=str(_write_hanging_cli("fake_agy")))
    start = time.time()
    code, out = runner.execute_task({"id": "hang", "prompt": "p", "timeout_seconds": 2}, TEST_DIR)
    elapsed = time.time() - start
    assert code == -1, f"Expected timeout exit -1, got {code}"
    assert elapsed < 15, f"Timeout not enforced promptly ({elapsed:.1f}s)"
    print(f"  - Silent hung CLI terminated after {elapsed:.1f}s (timeout=2s).")


def test_process_tree_termination():
    print("[psutil Process-Tree Recursive Termination]")
    import psutil
    from core.processes import kill_process_tree

    # Spawn parent python process that spawns a child process
    cmd = [sys.executable, "-c", "import subprocess, time, sys; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"]
    parent = subprocess.Popen(cmd)
    child_pid = None
    try:
        # Wait for the child (two Python start-ups): a fixed 1 s sleep wasn't enough under load (2026-10-28 audit 5.4)
        p_obj = psutil.Process(parent.pid)
        deadline = time.time() + 15
        while time.time() < deadline and not p_obj.children(recursive=True):
            time.sleep(0.1)
        children = p_obj.children(recursive=True)
        assert len(children) >= 1, f"Expected at least 1 child process, found {len(children)}"
        child_pid = children[0].pid
        print(f"  - Spawned process tree: Parent PID {parent.pid} -> Child PID {child_pid}")

        kill_process_tree(parent.pid, timeout=2.0)
        deadline = time.time() + 5
        while time.time() < deadline and (psutil.pid_exists(parent.pid) or psutil.pid_exists(child_pid)):
            time.sleep(0.1)

        assert not psutil.pid_exists(parent.pid), f"Parent process PID {parent.pid} still alive!"
        assert not psutil.pid_exists(child_pid), f"Child process PID {child_pid} still alive as zombie!"
    finally:
        # A failure doesn't leave the pair running for their 60 s
        if psutil.pid_exists(parent.pid) or (child_pid and psutil.pid_exists(child_pid)):
            kill_process_tree(parent.pid, timeout=2.0)
    print("  - Process-tree termination verified: parent and all child descendants cleanly killed.")


def test_media_pipeline_queue():
    print("[Unified Media Pipeline Task Queueing]")
    test_db = TEST_DIR / "test_media.db"
    if test_db.exists():
        test_db.unlink()

    db = DatabaseManager(test_db)
    task_id = db.create_task({
        "title": "Render 3D Card Flip Video",
        "prompt": "Render 3D video with quote",
        "engine": "media_pipeline",
        "priority": "high",
        "metadata": {
            "action": "video",
            "quote": "Breathe in calm, exhale worry.",
            "chat_id": 999
        }
    })

    task = db.get_task(task_id)
    assert task is not None
    assert task["engine"] == "media_pipeline"
    assert task["metadata"]["action"] == "video"
    assert task["metadata"]["chat_id"] == 999
    print("  - Unified media task verified: stored and parsed with metadata payload.")

    # Cleanup
    try:
        test_db.unlink(missing_ok=True)
        Path(str(test_db) + "-wal").unlink(missing_ok=True)
        Path(str(test_db) + "-shm").unlink(missing_ok=True)
    except Exception:
        pass


def test_sqlite_log_streaming():
    print("[SQLite Log Streaming & Buffer Capping]")
    import logging
    from core.daemon import SQLiteLogHandler

    test_db = TEST_DIR / "test_logs.db"
    if test_db.exists():
        test_db.unlink()

    db = DatabaseManager(test_db)
    handler = SQLiteLogHandler(db, max_rows=10)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # 1. Emit logs
    logger.info("Log event 1: System booted")
    logger.warning("Log event 2: High memory usage")
    logger.error("Log event 3: Connection timed out")

    logs = db.get_recent_logs(limit=10)
    assert len(logs) == 3, f"Expected 3 logs, got {len(logs)}"
    assert logs[0]["message"] == "Log event 1: System booted"
    assert logs[1]["level"] == "WARNING"
    assert logs[2]["level"] == "ERROR"
    print("  - SQLiteLogHandler verified: log levels and messages captured in database.")

    # 2. Test incremental since_id query
    second_id = logs[1]["id"]
    new_logs = db.get_recent_logs(since_id=second_id)
    assert len(new_logs) == 1
    assert new_logs[0]["id"] == logs[2]["id"]
    print("  - Incremental log streaming verified: since_id correctly filters new entries.")

    # 3. Test capping buffer at max_rows (capped at 10)
    for i in range(4, 25):
        logger.info(f"Log event {i}: Heartbeat pulse")

    capped_logs = db.get_recent_logs(limit=50)
    assert len(capped_logs) <= 15, f"Expected table to stay capped, got {len(capped_logs)}"
    print(f"  - Buffer capping verified: log table maintained bounded size ({len(capped_logs)} rows).")

    # Cleanup
    logger.removeHandler(handler)
    try:
        test_db.unlink(missing_ok=True)
        Path(str(test_db) + "-wal").unlink(missing_ok=True)
        Path(str(test_db) + "-shm").unlink(missing_ok=True)
    except Exception:
        pass


def test_api_endpoints():
    print("[Web HUD REST Endpoints, Process Cancel & SSE Disconnect]")
    from fastapi.testclient import TestClient
    from interfaces.hud import app as hud_app
    from interfaces.hud import common as hud_common, state as hud_state

    test_hud_db_file = TEST_DIR / "test_isolated_worker_hud.db"
    if test_hud_db_file.exists():
        test_hud_db_file.unlink()
        Path(str(test_hud_db_file) + "-wal").unlink(missing_ok=True)

    isolated_db = DatabaseManager(test_hud_db_file)
    original_db = hud_state.db
    hud_state.set_db(isolated_db)
    hud_db = isolated_db

    try:
        from tests._hud_client import client_from
        client = client_from(hud_app)

        # 1. The connection check DeskAI uses (the web dashboard that used to be here is gone)
        res = client.get("/")
        assert res.status_code == 200 and res.json()["status"] == "online"
        print("  - GET /: online.")

        # 2. Test POST /api/tasks
        task_payload = {
            "title": "Automated HUD Test Mission",
            "prompt": "Verify endpoint routing and queueing via REST POST",
            "priority": "high",
            "engine": "groq",
            "timeout_seconds": 120
        }
        res = client.post("/api/tasks", json=task_payload)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "success"
        task_id = data["task_id"]
        print(f"  - POST /api/tasks: Task [{task_id[:8]}] enqueued via HTTP REST.")

        # 3. Verify task exists in DB
        task = hud_db.get_task(task_id)
        assert task is not None
        assert task["title"] == "Automated HUD Test Mission"
        assert task["phase"] == "backlog"

        # 4. Test POST /api/cancel/{task_id}
        res = client.post(f"/api/cancel/{task_id}")
        assert res.status_code == 200
        task_cancelled = hud_db.get_task(task_id)
        assert task_cancelled["phase"] == "failed"
        assert "Cancelled" in (task_cancelled.get("last_error") or "")
        print(f"  - POST /api/cancel: Task [{task_id[:8]}] cancelled and marked failed.")

        # 5. Test POST /api/retry/{task_id}
        res = client.post(f"/api/retry/{task_id}")
        assert res.status_code == 200
        task_retried = hud_db.get_task(task_id)
        assert task_retried["phase"] == "backlog"
        print(f"  - POST /api/retry: Task [{task_id[:8]}] requeued back to backlog.")

        # 6. Test GET /api/videos
        res = client.get("/api/videos")
        assert res.status_code == 200
        assert "videos" in res.json()
        print("  - GET /api/videos: Video gallery index returned successfully.")

        # 7. Test SSE Disconnection handling
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        mock_request = MagicMock()
        mock_request.is_disconnected = AsyncMock(return_value=True)

        from interfaces.hud.tasks import sse_stream
        response = asyncio.run(sse_stream(mock_request))
        assert response.status_code == 200
        print("  - GET /api/stream: Verified request.is_disconnected() triggers clean stream termination without resource leak.")

    finally:
        # Restore the previous (also temporary) database and clean up isolated test database
        hud_state.set_db(original_db)
        try:
            isolated_db.get_connection().close()
        except Exception:
            pass
        del isolated_db
        if test_hud_db_file.exists():
            try:
                test_hud_db_file.unlink()
                Path(str(test_hud_db_file) + "-wal").unlink(missing_ok=True)
            except Exception:
                pass


def test_remote_ignition_and_standby():
    print("[Remote Ignition, Standby Mode & Auto-Awaken]")
    from core.daemon import AutonomousDaemon
    from core import actions
    from supervisor import get_lan_ip

    # 1. Test LAN IP detection (shown in the supervisor's start-up banner)
    lan_ip = get_lan_ip()
    assert lan_ip and len(lan_ip.split(".")) == 4, f"Invalid LAN IP format: {lan_ip}"

    test_db_path = TEST_DIR / "test_ignition.db"
    if test_db_path.exists():
        test_db_path.unlink()

    db = DatabaseManager(test_db_path)
    daemon = AutonomousDaemon(workspace_dir=_ROOT, db_file=str(test_db_path), log_file=str(TEST_DIR / "ignition_worker.log"))

    # 2. Put daemon into standby mode
    db.set_state("daemon_status", "paused")
    task_id = db.create_task({
        "title": "Task while in standby",
        "prompt": "Test standby restraint",
        "priority": "high",
        "engine": "groq"
    })

    # Run heartbeat - must NOT pick up task while paused
    daemon.heartbeat_cycle()
    assert daemon.active_future is None, "Error: Daemon picked up task while in standby mode!"
    task = db.get_task(task_id)
    assert task["phase"] == "backlog", "Error: Task phase transitioned while daemon was paused!"
    print("  - Standby verification: daemon consumes 0% CPU and refrains from dispatching while in standby mode.")

    # 3. New work wakes a sleeping daemon (DeskAI and the HUD create tasks through core.actions)
    actions.queue_instruction(db, "Build me a summary chart", source="test")
    assert db.get_state("daemon_status") == "idle", "A new task didn't wake the daemon from standby!"
    print("  - Auto-ignition verified: a new task awakened the sleeping daemon.")

    # Cleanup
    _close_daemon_logger()
    try:
        test_db_path.unlink(missing_ok=True)
        Path(str(test_db_path) + "-wal").unlink(missing_ok=True)
        Path(str(test_db_path) + "-shm").unlink(missing_ok=True)
    except Exception:
        pass


def test_task_isolation_and_safety_audit():
    print("[Task Folders, Credential Lockdown & Safety Audit]")
    import core.daemon as daemon_mod
    import core.task_engines as engines_mod
    from core.config import git_lockdown_env
    from core.daemon import EngineDispatcher
    from core.task_engines import AntigravityEngine

    # 1. Locked-down environment: no credential helper reaches git, gh sees no login
    env = get_safe_subprocess_env(git_lockdown_env())
    helpers = subprocess.run(["git", "config", "--get-all", "credential.helper"], env=env, capture_output=True, text=True)
    assert helpers.stdout.strip() == "", helpers.stdout
    if shutil.which("gh"):
        gh = subprocess.run(["gh", "auth", "status"], env=env, capture_output=True, text=True, timeout=30)
        assert "not logged" in (gh.stdout + gh.stderr).lower(), gh.stdout + gh.stderr

    # 2. Antigravity gets the rules, its folder, and the locked-down environment
    seen = {}

    def fake_popen(cmd, cwd=None, env=None, **kw):
        seen.update(cmd=cmd, cwd=cwd, env=env)
        raise FileNotFoundError("stop here")
    real_popen = engines_mod.subprocess.Popen
    engines_mod.subprocess.Popen = fake_popen
    try:
        work = TEST_DIR / "agy_task"
        work.mkdir(exist_ok=True)
        AntigravityEngine(custom_agy_path="agy").execute_task({"id": "t1", "prompt": "Build a page"}, work)
    finally:
        engines_mod.subprocess.Popen = real_popen
    prompt = seen["cmd"][seen["cmd"].index("--prompt") + 1]
    assert prompt.startswith("[Rules for this task]") and prompt.endswith("Build a page") and str(work.resolve()) in prompt
    assert seen["cmd"][seen["cmd"].index("--add-dir") + 1] == str(work.resolve()) and seen["cwd"] == str(work)
    assert seen["env"]["GIT_CONFIG_NOSYSTEM"] == "1" and "GH_CONFIG_DIR" in seen["env"]
    assert not any(k in seen["env"] for k in SENSITIVE_ENV_KEYS)

    # 3. The audit: a task that touches the agent's own files fails and alerts; one that stays home passes
    ws = TEST_DIR / "audit_ws"
    (ws / "core").mkdir(parents=True, exist_ok=True)
    (ws / "core" / "app.py").write_text("print('agent')\n")
    (ws / ".gitignore").write_text(".env\nworkspaces/\n")
    (ws / ".env").write_text("KEY=1\n")
    for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", *c], cwd=ws, check=True, capture_output=True)
    dispatcher = EngineDispatcher(ws, task_root=ws / "workspaces")
    alerts = []
    real_notify = daemon_mod.notify_owner
    daemon_mod.notify_owner = lambda text: alerts.append(text) or True

    class Writer:
        def __init__(self, target):
            self.target = target

        def execute_task(self, task, working_dir, *a):
            path = self.target(working_dir)
            path.write_text("changed\n")
            return 0, "done"
    try:
        dispatcher.antigravity = Writer(lambda wd: wd / "index.html")
        code, out, used, _ = dispatcher.execute({"id": "good-1", "engine": "antigravity", "prompt": "p"})
        assert code == 0 and not alerts and (ws / "workspaces" / "good-1" / "index.html").exists()

        dispatcher.antigravity = Writer(lambda wd: ws / "core" / "app.py")
        code, out, used, _ = dispatcher.execute({"id": "bad-1", "engine": "antigravity", "prompt": "p"})
        assert code == 1 and "[SAFETY]" in out and "core/app.py" in out and len(alerts) == 1, (code, out, alerts)

        subprocess.run(["git", "checkout", "--", "core/app.py"], cwd=ws, check=True)
        dispatcher.antigravity = Writer(lambda wd: ws / ".env")
        code, out, used, _ = dispatcher.execute({"id": "bad-2", "engine": "antigravity", "prompt": "p"})
        assert code == 1 and ".env" in out and len(alerts) == 2, (code, out)
    finally:
        daemon_mod.notify_owner = real_notify
    print("  - Tasks work in workspaces/<id>; git/gh logins hidden; Antigravity gets rules + its folder; "
          "touching the agent's files or .env fails the task and alerts the owner.")


if __name__ == "__main__":
    try:
        test_security_sanitization()
        test_sqlite_wal_engine()
        test_startup_crash_reconciliation()
        test_priority_queueing()
        test_sandbox_execution()
        test_engine_dispatcher_and_ollama()
        test_process_tree_termination()
        test_media_pipeline_queue()
        test_sqlite_log_streaming()
        test_api_endpoints()
        test_remote_ignition_and_standby()
        test_cancelled_task_not_requeued_by_daemon()
        test_task_ids_and_manual_retry()
        test_secret_redaction()
        test_antigravity_timeout_without_output()
        test_cloud_outage_falls_back_to_ollama()
        test_antigravity_is_cancellable()
        test_maintenance_spares_running_render()
        test_task_isolation_and_safety_audit()

        print("\nALL WORKER TESTS PASSED")
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)
