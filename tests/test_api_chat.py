"""
Test suite for DeskAI /api/chat endpoint integration
=====================================================
Verifies:
1. Authentication (Bearer header, X-HUD-Token header, ?token query param, loopback vs LAN)
2. CORS preflight and headers
3. Built-in quick commands (/status, /task <prompt>, /cancel)
4. Conversational chat turns and response schema
5. Health check endpoints (/api/health and GET / with Accept: application/json)
"""

import os
import time
import re
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos (it would render and send real videos): point the agent at a
# folder where it isn't installed, so an accidental call fails harmlessly
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

import core.config
_IMPORT_TEMP_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["STATE_DB_PATH"] = str(Path(_IMPORT_TEMP_DIR.name) / "chat_test.db")

from interfaces.hud import app as hud_app  # noqa: E402
from interfaces.hud import common as hud_common, state as hud_state  # noqa: E402
from core.database import DatabaseManager
from tests._hud_client import LAN_ADDR, LOCAL_ADDR, client_from


def test_api_chat_authentication_and_cors():
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    isolated_db = DatabaseManager(Path(temp_dir.name) / "test_chat_auth.db")
    original_db = hud_state.db
    original_token = hud_state.HUD_AUTH_TOKEN
    hud_state.set_db(isolated_db)

    token = "deskai-secret-token-42"
    try:
        # 1. Configured token
        hud_state.HUD_AUTH_TOKEN = token
        lan_client = client_from(hud_app, addr=LAN_ADDR)
        local_client = client_from(hud_app, addr=LOCAL_ADDR)

        # Missing token from LAN -> 401
        res = lan_client.post("/api/chat", json={"message": "hello"})
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"

        # Wrong token from LAN -> 401
        res = lan_client.post("/api/chat", json={"message": "hello"}, headers={"Authorization": "Bearer wrong-token"})
        assert res.status_code == 401

        res = lan_client.post("/api/chat", json={"message": "hello"}, headers={"X-HUD-Token": "wrong-token"})
        assert res.status_code == 401

        res = lan_client.post("/api/chat?token=wrong-token", json={"message": "hello"})
        assert res.status_code == 401

        # Valid token via Authorization: Bearer
        res = lan_client.post(
            "/api/chat",
            json={"message": "/status"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["success"] is True
        assert "Daemon is active" in data["reply"]
        assert data["agent_status"] == "idle"

        # Valid token via X-HUD-Token header
        res = lan_client.post(
            "/api/chat",
            json={"message": "/status"},
            headers={"X-HUD-Token": token}
        )
        assert res.status_code == 200, res.text

        # Valid token via ?token= query parameter
        res = lan_client.post(
            f"/api/chat?token={token}",
            json={"message": "/status"}
        )
        assert res.status_code == 200, res.text

        # Local loopback client is trusted even without token
        res = local_client.post("/api/chat", json={"message": "/status"})
        assert res.status_code == 200
        # Not on the answer either (until 2026-10-27 /api/chat, refine and the media lists sent a wildcard)
        assert "access-control-allow-origin" not in {k.lower() for k in res.headers.keys()}

        # No CORS: DeskAI is a native app. A cross-origin preflight isn't granted, and a browser request from a
        # web page (it carries Origin) needs the token even from this PC (2026-10-04)
        res_opt = lan_client.options("/api/chat", headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST"
        })
        assert "access-control-allow-origin" not in {k.lower() for k in res_opt.headers.keys()}
        res = local_client.post("/api/chat", json={"message": "/task delete everything"},
                                headers={"Origin": "http://evil.example"})
        assert res.status_code == 401
        res = local_client.post("/api/chat", json={"message": "/status"},
                                headers={"Origin": "http://evil.example", "X-HUD-Token": token})
        assert res.status_code == 200

        # 2. Empty token in .env: LAN rejected, local allowed
        hud_state.HUD_AUTH_TOKEN = ""
        res = lan_client.post("/api/chat", json={"message": "hello"})
        assert res.status_code == 401

        res = local_client.post("/api/chat", json={"message": "/status"})
        assert res.status_code == 200

    finally:
        hud_state.HUD_AUTH_TOKEN = original_token
        hud_state.set_db(original_db)


def test_api_chat_quick_commands():
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    isolated_db = DatabaseManager(Path(temp_dir.name) / "test_chat_commands.db")
    original_db = hud_state.db
    original_token = hud_state.HUD_AUTH_TOKEN
    hud_state.set_db(isolated_db)
    hud_state.HUD_AUTH_TOKEN = ""
    client = client_from(hud_app, addr=LOCAL_ADDR)

    try:
        # /status command
        res = client.post("/api/chat", json={"message": "/status"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["agent_status"] == "idle"
        assert "Active Task: None" in data["reply"]

        # /task <prompt> command
        task_prompt = "Build a automated unit test"
        res = client.post("/api/chat", json={"message": f"/task {task_prompt}"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["task_id"] is not None
        assert task_prompt in data["reply"]
        task_id = data["task_id"]

        # Verify task is created in DB
        task_in_db = isolated_db.get_task(task_id)
        assert task_in_db is not None
        assert task_in_db["prompt"] == task_prompt

        # Set task in progress to test /cancel and agent_status == "working"
        isolated_db.update_task(task_id, {"phase": "in_progress"})
        res = client.post("/api/chat", json={"message": "/status"})
        assert res.json()["agent_status"] == "working"

        # /cancel command
        res = client.post("/api/chat", json={"message": "/cancel"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["task_id"] == task_id
        assert "was cancelled" in data["reply"]
        assert isolated_db.get_task(task_id)["phase"] == "failed"

    finally:
        hud_state.HUD_AUTH_TOKEN = original_token
        hud_state.set_db(original_db)


def test_api_chat_conversational_and_health():
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    isolated_db = DatabaseManager(Path(temp_dir.name) / "test_chat_conv.db")
    original_db = hud_state.db
    original_token = hud_state.HUD_AUTH_TOKEN
    hud_state.set_db(isolated_db)
    hud_state.HUD_AUTH_TOKEN = ""
    client = client_from(hud_app, addr=LOCAL_ADDR)

    try:
        # Empty message validation
        res = client.post("/api/chat", json={"message": "   "})
        assert res.status_code in (400, 422)

        # Conversational message
        res = client.post("/api/chat", json={
            "message": "Hello agent, what can you do?",
            "model": "auto",
            "conversation_id": "test-session-123"
        })
        assert res.status_code == 200
        data = res.json()
        # Tests have no model keys (conftest), so every model "fails": the reply says so honestly (audit 2.1)
        assert data["success"] is False and "aren't answering" in data["reply"]
        assert data["agent_status"] in ("idle", "working", "waiting_for_approval", "error")

        # Health endpoint
        res = client.get("/api/health")
        assert res.status_code == 200
        assert res.json()["status"] == "online"

        # Root endpoint JSON health check
        res = client.get("/", headers={"Accept": "application/json"})
        assert res.status_code == 200
        assert res.json()["status"] == "online"

    finally:
        hud_state.HUD_AUTH_TOKEN = original_token
        hud_state.set_db(original_db)


def test_api_chat_outage_says_so_and_calls_no_second_model(monkeypatch):
    """2026-10-28 audit 2.1: when every model failed, a direct Gemini call (no tools, persona or history) answered,
    then a canned "set your API keys" with success true. Now the reply says the services aren't answering."""
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    isolated_db = DatabaseManager(Path(temp_dir.name) / "test_chat_gemini.db")
    original_db = hud_state.db
    hud_state.set_db(isolated_db)
    client = client_from(hud_app, addr=LOCAL_ADDR)

    orig_memory = hud_state._memory
    stored = []

    class MockMem:
        db = isolated_db
        llm = None

        def respond(self, *args, **kwargs):
            return None

        def add_message(self, role, text, *args, **kwargs):
            stored.append(role)
            return 1

    hud_state._memory = MockMem()
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    import google.genai
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: pytest.fail("a direct Gemini call was made"))
    try:
        res = client.post("/api/chat", json={"message": "test prompt"})
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False and "aren't answering" in body["reply"] and "API_KEY" not in body["reply"]
        assert stored == []                     # the outage notice isn't kept in the conversation
    finally:
        hud_state._memory = orig_memory
        hud_state.set_db(original_db)


def test_deskai_cohesion_endpoints():
    """Verify /api/status, /api/tasks, /api/tasks/run, /api/tasks/cancel, and /api/memory."""
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    isolated_db = DatabaseManager(Path(temp_dir.name) / "test_cohesion.db")
    original_db = hud_state.db
    original_token = hud_state.HUD_AUTH_TOKEN
    hud_state.set_db(isolated_db)
    token = "test-token-cohesion"
    hud_state.HUD_AUTH_TOKEN = token

    try:
        lan_client = client_from(hud_app, addr=LAN_ADDR)
        headers = {"X-HUD-Token": token}

        # 1. /api/status needs the token (DeskAI sends it); only /api/health answers without (audit 1.5)
        assert lan_client.get("/api/status").status_code == 401
        assert lan_client.get("/api/health").status_code == 200
        res_status = lan_client.get("/api/status", headers=headers)
        assert res_status.status_code == 200
        data = res_status.json()
        assert data["status"] == "online"
        assert "version" in data
        assert "uptime_seconds" in data
        assert "cpu_percent" in data
        assert "memory_percent" in data
        assert "active_model" in data
        assert "active_tasks_count" in data

        # 2. /api/tasks (requires auth)
        res_no_auth = lan_client.get("/api/tasks")
        assert res_no_auth.status_code == 401

        res_tasks = lan_client.get("/api/tasks", headers=headers)
        assert res_tasks.status_code == 200
        assert isinstance(res_tasks.json(), list)

        # 3. /api/tasks/run (creates and triggers task)
        res_run = lan_client.post("/api/tasks/run", json={"command": "Check system specs"}, headers=headers)
        assert res_run.status_code == 200
        task_id = res_run.json()["task_id"]
        assert task_id.startswith("task-")

        # 4. /api/tasks/cancel (halts task)
        res_cancel = lan_client.post("/api/tasks/cancel", json={"task_id": task_id}, headers=headers)
        assert res_cancel.status_code == 200
        assert res_cancel.json()["task_id"] == task_id

        # 5. /api/memory?query=...
        res_mem = lan_client.get("/api/memory?query=test", headers=headers)
        assert res_mem.status_code == 200
        assert "facts" in res_mem.json()
        assert res_mem.json()["query"] == "test"

    finally:
        hud_state.HUD_AUTH_TOKEN = original_token
        hud_state.set_db(original_db)


def test_api_chat_image_generation(monkeypatch):
    """/image goes to the ImageGen tool: the help guide, a made image shown in the reply, failures, and the prompt
    kept short-term only. (ImageGen's own tests cover the flags and the image services.)"""
    from core import imagegen, recent_images
    from core.assistant_tools import AssistantTools

    local_client = client_from(hud_app, addr=LOCAL_ADDR)
    calls = []

    def fake_run(args, timeout=240, on_progress=None):
        calls.append(args)
        if args[0] == "help":
            return {"text": "🎨 **Image Generation Guide (`/image`)** ... `--style anime` ..."}
        request = args[args.index("--request") + 1]
        out_dir = Path(args[args.index("--out-dir") + 1])
        if "impossible" in request:
            raise imagegen.ImageGenError("none of the image services could make it",
                                         {"prompt": "an impossible thing", "style": None})
        name = f"gen_{len(calls):08x}.jpg"
        (out_dir / name).write_bytes(b"\xFF\xD8" + b"0" * 1200)
        table = {
            "a futuristic desk --style cyberpunk --raw": ("a futuristic desk", "cyberpunk", "", "cloudflare-flux-1-schnell"),
            "a quiet forest --style anime": ("a quiet forest", "anime", "A misty quiet forest at dawn, soft light",
                                            "pollinations-sana"),
        }
        prompt, style, enhanced, model = table[request]
        return {"image": str(out_dir / name), "filename": name, "prompt": prompt, "enhanced": enhanced,
                "style": style, "model": model, "seconds": 1.0, "ai_usage": []}

    monkeypatch.setattr(imagegen, "run", fake_run)
    made = []
    try:
        # 1. /image help and an empty /image return the guide
        for message in ("/image help", "/image"):
            data = local_client.post("/api/chat", json={"message": message}).json()
            assert data["success"] is True and "Image Generation Guide" in data["reply"], data

        # 2. A made image is shown in the reply; the request is passed on with its flags, into the gallery folder
        data = local_client.post("/api/chat", json={"message": "/image a futuristic desk --style cyberpunk --raw"}).json()
        assert data["success"] is True and data["model"] == "cloudflare-flux-1-schnell"
        assert 'Generated Image:** "a futuristic desk"' in data["reply"] and "Style:** `cyberpunk`" in data["reply"]
        m = re.search(r"\]\(/images/(gen_[a-f0-9]+\.jpg)\)", data["reply"])
        assert m and (hud_state.IMAGES_DIR / m.group(1)).exists()
        made.append(m.group(1))
        assert calls[-1] == ["generate", "--request", "a futuristic desk --style cyberpunk --raw",
                             "--out-dir", str(hud_state.IMAGES_DIR)]

        # 3. An enhanced prompt is shown; "generate an image of" works like /image
        data = local_client.post("/api/chat", json={"message": "generate an image of a quiet forest --style anime"}).json()
        assert "A misty quiet forest" in data["reply"] and "Prompt enriched by Gemini" in data["reply"]
        made.append(re.search(r"/images/(gen_[a-f0-9]+\.jpg)", data["reply"]).group(1))

        # 4. A failure is explained, and recorded short-term as failed
        data = local_client.post("/api/chat", json={"message": "/image an impossible thing"}).json()
        assert data["success"] is False and "none of the image services" in data["reply"]

        # 5. Image prompts stay out of long-term memory (the owner's choice) but are kept short-term for follow-ups
        mem_msgs = hud_state.get_memory().list_messages(limit=20)
        assert not any("futuristic desk" in m["content"] or "quiet forest" in m["content"] for m in mem_msgs), \
            "image prompts must not reach long-term memory"
        recent = recent_images.recent(hud_state.db, 10)
        assert [r["prompt"] for r in recent[:3]] == ["an impossible thing", "a quiet forest", "a futuristic desk"]
        assert recent[0]["ok"] is False and recent[1]["style"] == "anime" and recent[1]["model"] == "pollinations-sana"
        assert recent[1]["enhanced"].startswith("A misty")
        listing = AssistantTools(hud_state.db, None, _ROOT).call("recent_image_prompts", {"limit": 3})
        assert "a quiet forest" in listing and "style anime" in listing
    finally:
        for name in made:
            (hud_state.IMAGES_DIR / name).unlink(missing_ok=True)


def test_image_jobs_run_in_the_background(monkeypatch):
    """POST /api/images/jobs answers at once; the image's reply is collected from GET /api/images/jobs/{id}, and the
    same turn_id sent again doesn't make a second image."""
    import threading
    import time
    from core import imagegen
    from interfaces.hud import chat as hud_chat

    client = client_from(hud_app, addr=LOCAL_ADDR)
    release, calls = threading.Event(), []

    def fake_run(args, timeout=240, on_progress=None):
        calls.append(args)
        if args[0] == "help":
            return {"text": "🎨 **Image Generation Guide (`/image`)**"}
        on_progress("AI Horde: 2nd in line")
        release.wait(5)
        out_dir = Path(args[args.index("--out-dir") + 1])
        name = "gen_job00001.jpg"
        (out_dir / name).write_bytes(b"\xFF\xD8" + b"0" * 1200)
        return {"image": str(out_dir / name), "filename": name, "prompt": "a red kite", "enhanced": "",
                "style": None, "model": "cloudflare-flux-2-klein", "seconds": 1.0, "ai_usage": []}

    monkeypatch.setattr(imagegen, "run", fake_run)
    monkeypatch.setattr(hud_chat, "_image_jobs", {})
    try:
        assert client.post("/api/images/jobs", json={"message": "hello"}).status_code == 400
        data = client.post("/api/images/jobs", json={"message": "/image help"}).json()
        assert data["status"] == "done" and "Image Generation Guide" in data["reply"]

        data = client.post("/api/images/jobs", json={"message": "/image a red kite --raw", "turn_id": "t-kite"}).json()
        assert data["job_id"] == "t-kite" and data["status"] == "running" and data["reply"] is None
        again = client.post("/api/images/jobs", json={"message": "/image a red kite --raw", "turn_id": "t-kite"}).json()
        assert again["job_id"] == "t-kite" and again["status"] == "running"
        for _ in range(100):                                  # what ImageGen says it's doing, while it works
            if client.get("/api/images/jobs/t-kite").json()["stage"] == "AI Horde: 2nd in line":
                break
            time.sleep(0.05)
        assert client.get("/api/images/jobs/t-kite").json()["stage"] == "AI Horde: 2nd in line"
        release.set()
        for _ in range(100):
            data = client.get("/api/images/jobs/t-kite").json()
            if data["status"] != "running":
                break
            time.sleep(0.05)
        assert data["status"] == "done" and "](/images/gen_job00001.jpg)" in data["reply"]
        assert sum(1 for c in calls if c[0] == "generate") == 1
        assert client.get("/api/images/jobs/nope").status_code == 404
    finally:
        release.set()
        (hud_state.IMAGES_DIR / "gen_job00001.jpg").unlink(missing_ok=True)


def test_imagegen_progress_is_read_while_it_runs(monkeypatch, tmp_path):
    """core.imagegen.run reads ImageGen's progress lines as they're printed, not after it ends."""
    import time
    from core import imagegen
    (tmp_path / "imagegen.py").write_text(
        "import json, sys, time\n"
        "print(json.dumps({'event': 'progress', 'stage': 'AI Horde: 3rd in line'}), flush=True)\n"
        "time.sleep(1.5)\n"
        "print(json.dumps({'event': 'done', 'filename': 'x.jpg'}), flush=True)\n", encoding="utf-8")
    monkeypatch.setattr(imagegen, "IMAGEGEN_DIR", tmp_path)
    seen = []
    started = time.time()
    result = imagegen.run(["generate"], timeout=30, on_progress=lambda s: seen.append((s, time.time() - started)))
    assert result == {"filename": "x.jpg"}
    assert seen and seen[0][0] == "AI Horde: 3rd in line" and seen[0][1] < 1.4, seen

    (tmp_path / "imagegen.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    with pytest.raises(imagegen.ImageGenError, match="didn't finish within 1s"):
        imagegen.run(["generate"], timeout=1)


def test_image_requests_fail_cleanly_without_imagegen():
    # The tests point IMAGEGEN_DIR at a folder where it isn't installed
    data = client_from(hud_app, addr=LOCAL_ADDR).post("/api/chat", json={"message": "/image a red kite --raw"}).json()
    assert data["success"] is False and "ImageGen isn't installed" in data["reply"]






def test_a_cancelled_turn_returns_nothing_and_stores_nothing():
    """The user stops a reply in DeskAI (POST /api/chat/cancel while the turn runs): it isn't returned as an
    answer, and no fallback answer is made up in its place."""
    from interfaces.hud import chat as hud_chat
    from interfaces.hud.models import ChatCancel
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    isolated_db = DatabaseManager(Path(temp_dir.name) / "test_chat_cancel.db")
    original_db = hud_state.db
    hud_state.set_db(isolated_db)
    client = client_from(hud_app, addr=LOCAL_ADDR)
    orig_memory = hud_state._memory
    seen = {}

    class MockMem:
        db = isolated_db
        llm = None

        def respond(self, user_text, system_context="", tools=None, cancelled=None):
            hud_chat.chat_cancel(ChatCancel(turn_id="t-1"))      # the user taps Stop while the model works
            seen["cancelled"] = cancelled()
            return None if cancelled() else "a stale answer"

        def add_message(self, *args, **kwargs):
            return 1

    hud_state._memory = MockMem()
    try:
        res = client.post("/api/chat", json={"message": "okay what about casion", "turn_id": "t-1"})
        assert res.status_code == 200 and seen["cancelled"] is True
        assert res.json()["success"] is False and res.json()["reply"] == "(cancelled)"
        # another turn isn't affected; the cancel endpoint answers on its own too
        assert client.post("/api/chat/cancel", json={"turn_id": "t-2"}).json() == {"ok": True}
        assert not hud_chat._is_cancelled("t-3") and not hud_chat._is_cancelled(None)
    finally:
        hud_state._memory = orig_memory
        hud_state.set_db(original_db)


def test_a_message_sent_twice_is_answered_once():
    """DeskAI resends to its other address when a connection fails, even after the server took the message. The
    repeat (same turn_id), during or after the first run, gets the first answer; the model runs once."""
    import threading
    from interfaces.hud import chat as hud_chat
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    isolated_db = DatabaseManager(Path(temp_dir.name) / "test_chat_twice.db")
    original_db = hud_state.db
    hud_state.set_db(isolated_db)
    client = client_from(hud_app, addr=LOCAL_ADDR)
    orig_memory = hud_state._memory
    calls, started, release = [], threading.Event(), threading.Event()

    class MockMem:
        db = isolated_db
        llm = None

        def respond(self, user_text, system_context="", tools=None, cancelled=None):
            calls.append(user_text)
            started.set()
            release.wait(5)
            return f"answer {len(calls)}"

        def add_message(self, *args, **kwargs):
            return 1

    hud_state._memory = MockMem()
    try:
        out = {}
        first = threading.Thread(target=lambda: out.update(a=client.post(
            "/api/chat", json={"message": "hello", "turn_id": "twice-1"})))
        first.start()
        assert started.wait(5)
        second = threading.Thread(target=lambda: out.update(b=client.post(
            "/api/chat", json={"message": "hello", "turn_id": "twice-1"})))
        second.start()
        release.set()
        first.join(10)
        second.join(10)
        assert calls == ["hello"]
        assert out["a"].json()["reply"] == out["b"].json()["reply"] == "answer 1"
        late = client.post("/api/chat", json={"message": "hello", "turn_id": "twice-1"})
        assert late.json()["reply"] == "answer 1" and calls == ["hello"]
        # A new turn_id is a new message
        assert client.post("/api/chat", json={"message": "hello", "turn_id": "twice-2"}).json()["reply"] == "answer 2"
    finally:
        hud_state._memory = orig_memory
        hud_state.set_db(original_db)
        hud_chat._turns.clear()


def test_api_chat_refine_and_enhance():
    """POST /api/chat/refine and POST /api/chat/enhance enhance whatever text is currently typed in the chat box."""
    from interfaces.hud import chat as hud_chat
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    isolated_db = DatabaseManager(Path(temp_dir.name) / "test_chat_refine.db")
    original_db = hud_state.db
    original_token = hud_state.HUD_AUTH_TOKEN
    orig_memory = hud_state._memory
    hud_state.set_db(isolated_db)

    token = "test-token-refine-123"
    hud_state.HUD_AUTH_TOKEN = token

    lan_client = client_from(hud_app, addr=LAN_ADDR)
    local_client = client_from(hud_app, addr=LOCAL_ADDR)

    captured_prompts = []
    captured_models = []

    class MockLLM:
        def complete(self, messages, max_tokens=600, temperature=0.4, timeout=30, models=None, **kwargs):
            captured_prompts.append(messages)
            captured_models.append(models)
            user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
            if "with quotes" in user_msg:
                return '"Enhanced prompt with quotes removed"', "mock:gemini-3.5-flash-lite"
            return "Please provide a detailed breakdown of the task.", "mock:gemini-3.5-flash-lite"

    class MockMem:
        db = isolated_db
        llm = MockLLM()

    hud_state._memory = MockMem()

    try:
        # 1. Auth required from LAN
        res = lan_client.post("/api/chat/refine", json={"text": "hello"})
        assert res.status_code == 401

        # 2. Empty or missing text -> 400
        auth_header = {"Authorization": f"Bearer {token}"}
        res = lan_client.post("/api/chat/refine", json={"text": ""}, headers=auth_header)
        assert res.status_code == 400

        res = local_client.post("/api/chat/refine", json={})
        assert res.status_code == 400

        # 3. Successful refinement via /api/chat/refine
        res = local_client.post("/api/chat/refine", json={"text": "remind me to call mom"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["original"] == "remind me to call mom"
        assert data["refined"] == "Please provide a detailed breakdown of the task."
        assert data["model"] == "mock:gemini-3.5-flash-lite"

        # 4. /api/chat/enhance alias endpoint works identically
        res = local_client.post("/api/chat/enhance", json={"text": "summarize repo logs"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["original"] == "summarize repo logs"
        assert data["refined"] == "Please provide a detailed breakdown of the task."

        # 5. Alternate field names (prompt or message) accepted
        res = local_client.post("/api/chat/refine", json={"prompt": "create a tiktok video"})
        assert res.status_code == 200
        assert res.json()["original"] == "create a tiktok video"

        res = local_client.post("/api/chat/refine", json={"message": "draft email"})
        assert res.status_code == 200
        assert res.json()["original"] == "draft email"

        # 6. Quotes stripped from LLM output
        res = local_client.post("/api/chat/refine", json={"text": "with quotes test"})
        assert res.status_code == 200
        assert res.json()["refined"] == "Enhanced prompt with quotes removed"

        # 7. Optional instruction passed to prompt
        res = local_client.post("/api/chat/refine", json={"text": "fix bug", "instruction": "make it concise"})
        assert res.status_code == 200
        last_call = captured_prompts[-1]
        user_content = next(m["content"] for m in last_call if m["role"] == "user")
        assert "Special refinement instruction: make it concise" in user_content

        # 7b. Never the Flash models (their ~20 a day are for chat replies)
        for models in captured_models:
            assert models, "refine must name its routes"
            assert not [m for m in models if m.startswith("gemini:") and "flash" in m and "lite" not in m]

        # 7c. Sign-in details in the draft never reach a model (same rule as the chat's intercept)
        calls_before = len(captured_prompts)
        zoho = "my zoho code 1000." + "A" * 30 + " please connect"
        google = "http://localhost:8765/?state=x&code=4/abc123&scope=email"
        for draft in (zoho, google, "finish this: " + google):
            res = local_client.post("/api/chat/refine", json={"text": draft})
            assert res.status_code == 200
            assert res.json()["refined"] == draft.strip() and res.json()["model"] == "none"
        res = local_client.post("/api/chat/refine", json={"text": "connect zoho", "instruction": zoho})
        assert res.json()["model"] == "none"
        assert len(captured_prompts) == calls_before

        # 8. Fallback when no LLM is available
        hud_state._memory = None
        from core import llm as core_llm
        orig_avail = core_llm.CloudLLM.is_available
        core_llm.CloudLLM.is_available = lambda self: False
        try:
            res = local_client.post("/api/chat/refine", json={"text": "raw fallback text"})
            assert res.status_code == 200
            fallback_data = res.json()
            assert fallback_data["success"] is True
            assert fallback_data["refined"] == "raw fallback text"
            assert fallback_data["model"] == "raw"
        finally:
            core_llm.CloudLLM.is_available = orig_avail
    finally:
        hud_state._memory = orig_memory
        hud_state.HUD_AUTH_TOKEN = original_token
        hud_state.set_db(original_db)



def test_commands_match_the_word_and_errors_dont_leak(monkeypatch):
    """2026-10-28 audit 2.7: "/tasks" was queued as the task "s"; 2.9: a 500 sent the exception's own text."""
    from interfaces.hud import chat as chat_mod
    assert chat_mod.COMMAND_TASK.match("/task render a video") and chat_mod.COMMAND_TASK.match("/task")
    assert not chat_mod.COMMAND_TASK.match("/tasks") and not chat_mod.COMMAND_TASK.match("/taskforce idea")
    assert chat_mod.COMMAND_CANCEL.match("/cancel") and not chat_mod.COMMAND_CANCEL.match("/cancellation policy?")
    err = chat_mod._server_error("answering a chat message", RuntimeError(r"no such table at C:\Projects\secret.db"))
    assert err.status_code == 500 and "secret.db" not in err.detail and "error " in err.detail


def test_several_images_share_one_scene_wait(monkeypatch):
    """2.8: each image without its scene note waited SCENE_WAIT in turn; three passed DeskAI's wait."""
    from interfaces.hud import chat as chat_mod
    waits = []
    monkeypatch.setattr(chat_mod.documents, "load", lambda aid: {"id": aid, "name": f"{aid}.jpg", "kind": "image"})

    def wait(aid, timeout):
        waits.append(timeout)
        time.sleep(0.2)
        return None
    monkeypatch.setattr(chat_mod.vision, "wait_for_scene", wait)
    monkeypatch.setattr(chat_mod, "SCENE_WAIT", 0.3)
    monkeypatch.setattr(hud_state, "get_memory", lambda: (_ for _ in ()).throw(RuntimeError("stop after attachments")))
    client = client_from(hud_app, addr=LOCAL_ADDR)
    client.post("/api/chat", json={"message": "look", "attachments": ["att-000000000001", "att-000000000002",
                                                                       "att-000000000003"]})
    # 0.3 s for all three: the first gets it all, the next what's left, the last nothing
    assert len(waits) == 3 and waits[0] <= 0.3 and waits[1] < waits[0] and waits[2] == 0.0
