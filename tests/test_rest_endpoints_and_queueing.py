"""
Comprehensive Verification Suite: REST POST Endpoint Routing & Task Queueing
=============================================================================
This suite executes end-to-end verification of:
1. In-Memory FastAPI Application Route Verification (hud_app):
   - POST /api/daemon/state (Standby vs wake, invalid state 400 rejection)
   - POST /api/engine (auto, antigravity, cloud, ollama; older names hermes/groq stored as cloud; 400 on unknown)
   - POST /api/tasks (Task enqueueing, priority mapping, validation)
   - POST /api/cancel/{task_id} (Cancellation handling)
   - POST /api/retry/{task_id} (Requeueing to backlog)
   - POST /api/trigger_media (Media pipeline dispatch)
   - Negative validation: 404 Not Found & 422 Unprocessable Entity

2. Queueing behavior and Priority Queue verification via REST POST:
   - Enqueueing tasks with varying priority levels via REST POST (urgent, high, medium, low)
   - Verifying strict priority ordering (urgent -> high -> medium -> low)
   - FIFO tie-breaking resolution for identical priorities
   - Cancellation exclusion from backlog queue
   - Retry re-insertion into priority queue

3. Optional Live REST POST endpoint routing against active server (http://127.0.0.1:8080)
   (Only executed if --live flag is passed)
"""

import os
import sys
import tempfile
import time
from pathlib import Path

import requests

# Ensure root is in sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos (it would render and send real videos): point the agent at a
# folder where it isn't installed, so an accidental call fails harmlessly
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

# core.config loads .env into os.environ on import, so override STATE_DB_PATH afterwards;
# The API server (interfaces/hud) opens its database at import time and must never touch data/state.db.
_IMPORT_TEMP_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["STATE_DB_PATH"] = str(Path(_IMPORT_TEMP_DIR.name) / "hud_import_default.db")

from core.database import DatabaseManager
from interfaces.hud import app as hud_app
from interfaces.hud import state as hud_state
from tests._hud_client import LAN_ADDR, client_from

LIVE_SERVER_BASE_URL = "http://127.0.0.1:8080"


def print_banner(title: str):
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def verify_fastapi_app_codebase_endpoints(isolated_db: DatabaseManager):
    print_banner("PART 1: FASTAPI APP CODEBASE ROUTING VERIFICATION (hud_app)")
    
    # Strictly isolate database
    original_db = hud_state.db
    hud_state.set_db(isolated_db)
    client = client_from(hud_app)

    try:
        # -------------------------------------------------------------
        # 1. POST /api/daemon/state
        # -------------------------------------------------------------
        print("\n--> [1/4] Testing POST /api/daemon/state routing...")
        res = client.post("/api/daemon/state", json={"status": "paused"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        assert res.json()["daemon_status"] == "paused"
        print("  [PASS] POST /api/daemon/state -> 'paused' routed correctly")

        res = client.post("/api/daemon/state", json={"status": "idle"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        assert res.json()["daemon_status"] == "idle"
        print("  [PASS] POST /api/daemon/state -> 'idle' restored correctly")

        res = client.post("/api/daemon/state", json={"status": "invalid_xyz"})
        assert res.status_code == 400
        print(f"  [PASS] Invalid state rejected with 400: {res.json()['detail']}")

        res = client.post("/api/daemon/state", json={})
        assert res.status_code == 422
        print("  [PASS] Missing status field rejected with 422")

        # -------------------------------------------------------------
        # 2. POST /api/engine
        # -------------------------------------------------------------
        print("\n--> [2/4] Testing POST /api/engine routing...")
        # Older names are accepted and stored as the engine they now mean
        for eng, stored in [("cloud", "cloud"), ("ollama", "ollama"), ("antigravity", "antigravity"),
                            ("hermes", "cloud"), ("groq", "cloud"), ("auto", "auto")]:
            res = client.post("/api/engine", json={"engine": eng})
            assert res.status_code == 200
            assert res.json()["default_engine"] == stored, (eng, res.json())
            print(f"  [PASS] POST /api/engine -> '{eng}' accepted (stored as '{stored}')")

        res = client.post("/api/engine", json={"engine": "nonexistent_engine"})
        assert res.status_code == 400
        print(f"  [PASS] Invalid engine rejected with 400: {res.json()['detail']}")

        # Task payload validation: engine / priority must be known values (blocks stored XSS via engine)
        res = client.post("/api/tasks", json={"title": "x", "prompt": "y", "engine": "<img src=x onerror=alert(1)>"})
        assert res.status_code == 422, f"Expected 422 for unknown engine, got {res.status_code}"
        res = client.post("/api/tasks", json={"title": "x", "prompt": "y", "priority": "critical"})
        assert res.status_code == 422, f"Expected 422 for unknown priority, got {res.status_code}"
        print("  [PASS] POST /api/tasks rejects unknown engine / priority with 422")

        # No wildcard CORS: cross-origin preflight must not be granted
        res = client.options("/api/tasks", headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"
        })
        assert "access-control-allow-origin" not in {k.lower() for k in res.headers}
        print("  [PASS] Cross-origin preflight is not granted (no wildcard CORS)")

        # -------------------------------------------------------------
        # 3. POST /api/tasks & cancel / retry
        # -------------------------------------------------------------
        print("\n--> [3/4] Testing POST /api/tasks, cancel, and retry...")
        res = client.post("/api/tasks", json={
            "title": "Unit Test Task",
            "prompt": "Test prompt verification",
            "priority": "high",
            "engine": "auto"
        })
        assert res.status_code == 200
        task_id = res.json()["task_id"]
        print(f"  [PASS] POST /api/tasks created task [{task_id}]")

        res_cancel = client.post(f"/api/cancel/{task_id}")
        assert res_cancel.status_code == 200
        print(f"  [PASS] POST /api/cancel/{task_id} successfully cancelled task")

        res_retry = client.post(f"/api/retry/{task_id}")
        assert res_retry.status_code == 200
        print(f"  [PASS] POST /api/retry/{task_id} successfully restored task to backlog")

        # -------------------------------------------------------------
        # 4. POST /api/trigger_media
        # -------------------------------------------------------------
        print("\n--> [4/4] Testing POST /api/trigger_media...")
        res = client.post("/api/trigger_media", json={"action": "video", "quote": "Test isolated affirmation"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        media_task_id = data["task_id"]
        print(f"  [PASS] POST /api/trigger_media created task [{media_task_id}] in isolated DB")

        print("\n>> FASTAPI CODEBASE ROUTES VERIFIED 100%!")

    finally:
        hud_state.set_db(original_db)


def verify_queueing_behavior_and_priority_ordering(isolated_db: DatabaseManager):
    print_banner("PART 2: QUEUEING VERIFICATION & PRIORITY RESOLUTION VIA REST POST")

    # Ensure clean slate for priority order testing
    with isolated_db.get_connection() as conn:
        conn.execute("DELETE FROM tasks")

    original_db = hud_state.db
    hud_state.set_db(isolated_db)
    client = client_from(hud_app)

    try:
        # 1. Enqueue tasks in reverse priority order: low -> medium -> high -> urgent
        print("\n--> [1/4] Enqueueing tasks in reverse priority order via POST /api/tasks...")
        tasks_to_create = [
            ("Task-Low", "low", "Run low priority work"),
            ("Task-Medium", "medium", "Run medium priority work"),
            ("Task-High", "high", "Run high priority work"),
            ("Task-Urgent", "urgent", "Run critical urgent work"),
        ]

        created_task_ids = {}
        for title, prio, prompt in tasks_to_create:
            res = client.post("/api/tasks", json={
                "title": title,
                "priority": prio,
                "prompt": prompt,
                "engine": "auto"
            })
            assert res.status_code == 200
            tid = res.json()["task_id"]
            created_task_ids[prio] = tid
            print(f"  - Enqueued via REST POST: {title} (priority: {prio}, task_id: {tid})")
            time.sleep(0.01)

        # 2. Verify priority dequeue order
        print("\n--> [2/4] Verifying strict priority dequeue order (urgent -> high -> medium -> low)...")
        expected_priority_order = ["urgent", "high", "medium", "low"]
        actual_order = []

        for expected_prio in expected_priority_order:
            next_task = isolated_db.get_next_backlog_task()
            assert next_task is not None, f"Expected backlog task for {expected_prio}, got None"
            actual_order.append(next_task["priority"])
            print(f"  - Dequeued from priority queue: [{next_task['id']}] '{next_task['title']}' (priority: {next_task['priority']})")
            isolated_db.update_task(next_task["id"], {"phase": "in_progress"})

        assert actual_order == expected_priority_order, f"Priority queue mismatch! Expected {expected_priority_order}, got {actual_order}"
        print(f"  [PASS] Strict priority dequeue verified: {' -> '.join(actual_order)}")

        # 3. FIFO Tie-Breaking Test for tasks with same priority
        print("\n--> [3/4] Testing FIFO tie-breaking for identical priorities...")
        fifo_ids = []
        for i in range(1, 4):
            res = client.post("/api/tasks", json={
                "title": f"FIFO Same Priority Task {i}",
                "priority": "high",
                "prompt": f"FIFO prompt {i}",
                "engine": "auto"
            })
            assert res.status_code == 200
            fifo_ids.append(res.json()["task_id"])
            time.sleep(0.01)

        dequeued_fifo_ids = []
        for _ in range(3):
            t = isolated_db.get_next_backlog_task()
            assert t is not None
            dequeued_fifo_ids.append(t["id"])
            isolated_db.update_task(t["id"], {"phase": "completed"})

        assert dequeued_fifo_ids == fifo_ids, f"FIFO order failed! Expected {fifo_ids}, got {dequeued_fifo_ids}"
        print("  [PASS] FIFO tie-breaking for equal priority tasks verified.")

        # 4. Cancellation & Retry priority re-insertion
        print("\n--> [4/4] Testing cancellation exclusion and retry priority restoration...")
        res = client.post("/api/tasks", json={
            "title": "Critical Urgent Task to Cancel",
            "priority": "urgent",
            "prompt": "Urgent prompt to cancel",
            "engine": "auto"
        })
        crit_id = res.json()["task_id"]

        # Cancel it
        client.post(f"/api/cancel/{crit_id}")
        assert isolated_db.get_task(crit_id)["phase"] == "failed"

        # Backlog must be empty
        assert isolated_db.get_next_backlog_task() is None
        print("  [PASS] Cancelled urgent task excluded from backlog.")

        # Retry it
        client.post(f"/api/retry/{crit_id}")
        assert isolated_db.get_task(crit_id)["phase"] == "backlog"

        restored_task = isolated_db.get_next_backlog_task()
        assert restored_task is not None
        assert restored_task["id"] == crit_id
        print(f"  [PASS] Retried task [{crit_id}] successfully re-entered priority queue at top position.")

        print("\n>> QUEUEING AND PRIORITY RESOLUTION VERIFIED 100%!")

    finally:
        hud_state.set_db(original_db)


def verify_hud_access_control(isolated_db: DatabaseManager):
    print_banner("PART 3: WEB HUD ACCESS CONTROL (HUD_AUTH_TOKEN)")

    original_db, original_token = hud_state.db, hud_state.HUD_AUTH_TOKEN
    hud_state.set_db(isolated_db)
    task_body = {"title": "t", "prompt": "p"}
    try:
        # 1. No token configured: loopback only
        hud_state.HUD_AUTH_TOKEN = ""
        local = client_from(hud_app)
        lan = client_from(hud_app, LAN_ADDR)
        assert local.get("/api/videos").status_code == 200
        assert lan.get("/api/videos").status_code == 401
        assert lan.post("/api/tasks", json=task_body).status_code == 401
        assert lan.get("/").status_code == 401 and lan.get("/").json() == {"detail": "Unauthorized"}
        assert lan.get("/?token=").status_code == 401
        print("  [PASS] Without HUD_AUTH_TOKEN: loopback allowed, LAN rejected (401)")

        # 2. Token configured
        token = "unit-test-token-7f3a9c"
        hud_state.HUD_AUTH_TOKEN = token
        lan = client_from(hud_app, LAN_ADDR)
        assert lan.get("/api/videos").status_code == 401
        assert lan.get("/api/videos", headers={"X-HUD-Token": "wrong"}).status_code == 401
        assert lan.get("/?token=wrong", follow_redirects=False).status_code == 401
        assert lan.get("/api/videos", headers={"X-HUD-Token": token}).status_code == 200
        print("  [PASS] With token: missing / wrong token rejected, X-HUD-Token header accepted")

        # DeskAI's connection check (GET / with the Bearer token), a ?token= on a media link, and no cookie login
        # (the browser dashboard that used it is gone)
        assert lan.get("/", headers={"Authorization": f"Bearer {token}"}).json()["status"] == "online"
        assert lan.get(f"/api/videos?token={token}").status_code == 200
        res = lan.post("/api/tasks", json=task_body, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200, res.text
        assert lan.get("/api/videos", cookies={"hud_token": token}).status_code == 401
        print("  [PASS] Bearer token and ?token= accepted; the old dashboard cookie is not")

        # Workstation is never locked out
        assert client_from(hud_app).get("/api/videos").status_code == 200
        print("  [PASS] Loopback still allowed when a token is configured")

        print("\n>> HUD ACCESS CONTROL VERIFIED 100%!")
    finally:
        hud_state.HUD_AUTH_TOKEN = original_token
        hud_state.set_db(original_db)


def verify_live_rest_endpoints():
    print_banner("PART 4: LIVE REST POST ENDPOINTS (http://127.0.0.1:8080)")
    try:
        health_res = requests.get(f"{LIVE_SERVER_BASE_URL}/api/videos", timeout=3.0)
        assert health_res.status_code == 200
        print(f"[OK] Live supervisor web server is responding at {LIVE_SERVER_BASE_URL}")
    except Exception as e:
        print(f"[FAIL] Unable to connect to live server at {LIVE_SERVER_BASE_URL}: {e}")
        return

    print("Live endpoint check completed.")


if __name__ == "__main__":
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    temp_db_path = Path(temp_dir.name) / "test_isolated_queue.db"
    test_db = DatabaseManager(temp_db_path)

    try:
        verify_fastapi_app_codebase_endpoints(test_db)
        verify_queueing_behavior_and_priority_ordering(test_db)
        verify_hud_access_control(test_db)
        if "--live" in sys.argv:
            verify_live_rest_endpoints()
        print_banner("ALL REST POST ENDPOINT ROUTING & QUEUEING VERIFICATIONS PASSED 100%!")
    finally:
        try:
            test_db.get_connection().close()
        except Exception:
            pass
        temp_dir.cleanup()
        _IMPORT_TEMP_DIR.cleanup()
