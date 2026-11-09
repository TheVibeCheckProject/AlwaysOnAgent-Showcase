"""
DeskAI companion endpoints
=====================================================
Suggested tasks with Run / Dismiss, AgentWork jobs, posting an existing video, push
registration and sending, the task push watcher, backup, restart and logs.
"""

import os
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

_IMPORT_TEMP_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ.setdefault("STATE_DB_PATH", str(Path(_IMPORT_TEMP_DIR.name) / "phase2_test.db"))

import core.config
from core import backup, push
from core.database import DatabaseManager
from interfaces.hud import app as hud_app
from interfaces.hud import common as hud_common
from interfaces.hud import state as hud_state
from tests._hud_client import LOCAL_ADDR, client_from


class FakeAgentWork:
    def __init__(self):
        self.added = []

    def list_projects(self):
        return [{"name": "site", "description": "The website", "repo": "github.com/x/site"}]

    def add_project(self, name, repo, description):
        if "unreachable" in repo:
            raise ValueError("couldn't reach the repo")
        self.added.append(name)
        return {"name": name, "repo": repo, "description": description}


class FakeMemory:
    """Answers every chat by proposing the tasks it was given, like the real model calling propose_task."""

    def __init__(self, db, proposals=()):
        self.db, self.llm = db, None
        self.proposals = list(proposals)
        self.events = []

    def respond(self, user_text, system_context="", tools=None, **kwargs):
        for instruction, project in self.proposals:
            assert "Proposed" in tools.propose_task(instruction, reason="it helps", project=project)
        return "Here's a thought."

    def record_event(self, text):
        self.events.append(text)
        return len(self.events)

    def add_message(self, *a, **k):
        return 1


@pytest.fixture
def hud(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "state.db")
    original_db, original_memory, original_aw = hud_state.db, hud_state._memory, hud_state._agentwork
    hud_state.set_db(db)
    hud_state.set_agentwork(FakeAgentWork())
    monkeypatch.setattr(push, "DEVICES_FILE", tmp_path / "push_devices.json")
    monkeypatch.setattr(hud_state, "VIDEOS_DIR", tmp_path / "videos")
    (tmp_path / "videos").mkdir()
    hud_common._proposals.clear()
    yield db, client_from(hud_app, addr=LOCAL_ADDR)
    hud_state.set_db(original_db)
    hud_state._memory = original_memory
    hud_state.set_agentwork(original_aw)


def test_chat_returns_proposals_that_run_once(hud):
    db, client = hud
    mem = FakeMemory(db, [("Clean up the old render folders", None)])
    hud_state.set_memory(mem)
    data = client.post("/api/chat", json={"message": "my disk is full"}).json()

    assert len(data["proposals"]) == 1
    p = data["proposals"][0]
    assert p["instruction"] == "Clean up the old render folders" and p["reason"] == "it helps" and p["project"] is None
    assert "Suggested tasks:" in data["reply"]            # older app builds still read the text

    res = client.post(f"/api/proposals/{p['id']}/run")
    assert res.status_code == 200, res.text
    task = db.get_task(res.json()["task_id"])
    assert task["prompt"] == "Clean up the old render folders" and task["phase"] == "backlog"
    assert task["metadata"]["source"] == "DeskAI suggestion"
    assert any("queued background task" in e for e in mem.events)

    assert client.post(f"/api/proposals/{p['id']}/run").status_code == 404        # already handled
    assert client.post("/api/proposals/nope/dismiss").status_code == 404


def test_project_proposal_becomes_agentwork_job_and_dismiss_is_remembered(hud):
    db, client = hud
    mem = FakeMemory(db, [("Add a holidays page to the site", "site"), ("Check the site's links", "site")])
    hud_state.set_memory(mem)
    first, second = client.post("/api/chat", json={"message": "website ideas?"}).json()["proposals"]
    assert first["project"] == "site"

    task = db.get_task(client.post(f"/api/proposals/{first['id']}/run").json()["task_id"])
    assert task["engine"] == "agentwork" and task["title"].startswith("[site] ")
    assert task["timeout_seconds"] == 1800 and task["metadata"]["project"] == "site"

    assert client.post(f"/api/proposals/{second['id']}/dismiss").json() == {"status": "ok"}
    assert any("dismissed a suggested task" in e for e in mem.events)


def test_text_block_can_be_turned_off(hud):
    db, client = hud
    hud_state.set_memory(FakeMemory(db, [("Clean up the old render folders", None)]))
    db.set_state("proposal_text_block", "off")
    data = client.post("/api/chat", json={"message": "disk full"}).json()
    assert len(data["proposals"]) == 1 and "Suggested tasks:" not in data["reply"]


def test_agentwork_endpoints(hud):
    db, client = hud
    hud_state.set_memory(FakeMemory(db))
    assert client.get("/api/agentwork/projects").json() == [
        {"name": "site", "description": "The website", "repo": "github.com/x/site"}]

    assert client.post("/api/agentwork/jobs", json={"project": "site", "instruction": "short"}).status_code == 400
    assert client.post("/api/agentwork/jobs", json={"project": "nope", "instruction": "Add a calendar page"}).status_code == 400
    res = client.post("/api/agentwork/jobs", json={"project": "site", "instruction": "Add a calendar page"})
    assert res.status_code == 200, res.text
    task = db.get_task(res.json()["task_id"])
    assert task["engine"] == "agentwork" and task["metadata"]["project"] == "site"


def test_publish_existing_video(hud):
    db, client = hud
    (hud_state.VIDEOS_DIR / "clip.mp4").write_bytes(b"\x00" * 10)
    assert client.post("/api/videos/missing.mp4/publish").status_code == 404
    assert client.post("/api/videos/..%2F..%2Fstate.db/publish").status_code == 404

    task = db.get_task(client.post("/api/videos/clip.mp4/publish").json()["task_id"])
    assert task["engine"] == "media_pipeline" and task["metadata"]["action"] == "tiktok"
    assert Path(task["metadata"]["video_path"]).name == "clip.mp4"


def test_video_thumbnails(hud):
    _db, client = hud
    (hud_state.VIDEOS_DIR / "clip.mp4").write_bytes(b"\x00" * 10)

    # 1. GET /api/videos returns thumbnail_url
    res = client.get("/api/videos")
    assert res.status_code == 200
    videos = res.json()["videos"]
    clip_item = next((v for v in videos if v["filename"] == "clip.mp4"), None)
    assert clip_item is not None
    assert clip_item["thumbnail_url"] == "/api/videos/clip.mp4/thumbnail"

    # 2. 404 on missing or traversal paths
    assert client.get("/api/videos/missing.mp4/thumbnail").status_code == 404
    assert client.get("/api/videos/..%2F..%2Fstate.db/thumbnail").status_code == 404

    # 3. Cached thumbnail returns 200 and image/jpeg
    thumbs_dir = hud_state.VIDEOS_DIR / ".thumbnails"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    fake_thumb = thumbs_dir / "clip.mp4.jpg"
    fake_thumb.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    os.utime(fake_thumb, (fake_thumb.stat().st_atime, (hud_state.VIDEOS_DIR / "clip.mp4").stat().st_mtime + 10))

    thumb_res = client.get("/api/videos/clip.mp4/thumbnail")
    assert thumb_res.status_code == 200
    assert thumb_res.headers["content-type"] == "image/jpeg"
    assert thumb_res.content.startswith(b"\xff\xd8\xff\xe0")


def test_push_registration_and_test_endpoint(hud, monkeypatch):
    _, client = hud
    assert client.post("/api/push/test").json() == {"status": "no_devices"}
    token = "fcm-token-" + "x" * 30
    assert client.post("/api/push/register", json={"token": token, "device_name": "Pixel"}).json()["status"] == "ok"
    assert client.post("/api/push/register", json={"token": token}).status_code == 200     # idempotent
    assert push.device_count() == 1

    monkeypatch.setattr(push, "key_file", lambda: Path("definitely-missing.json"))
    assert client.post("/api/push/test").json()["status"] == "not_configured"

    sent = []
    monkeypatch.setattr(push, "is_configured", lambda: True)
    monkeypatch.setattr(push, "send", lambda *a, **k: sent.append((a, k)) or 1)
    assert client.post("/api/push/test").json() == {"status": "sent", "devices": 1}
    assert sent[0][0][0] == "test"

    assert client.post("/api/push/unregister", json={"token": token}).json() == {"status": "ok"}
    assert push.device_count() == 0


def test_push_send_payload_and_stale_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "DEVICES_FILE", tmp_path / "devices.json")
    push.register_device("good-token", "Pixel")
    push.register_device("old-token", "Old phone")
    monkeypatch.setattr(push, "is_configured", lambda: True)
    monkeypatch.setattr(push, "_access_token", lambda: "oauth")
    monkeypatch.setattr(push, "_project_id", lambda: "deskai-123")

    posted = []

    class Resp:
        def __init__(self, status, text=""):
            self.status_code, self.text, self.ok = status, text, status == 200

    def fake_post(url, headers=None, json=None, timeout=0):
        posted.append((url, headers, json))
        return Resp(200) if json["message"]["token"] == "good-token" else Resp(404, '{"error": "UNREGISTERED"}')

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    n = push.send("task_completed", "✅ Task finished", "x" * 500, task_id="task-9")

    assert n == 1
    url, headers, body = posted[0]
    assert url.endswith("/projects/deskai-123/messages:send") and headers["Authorization"] == "Bearer oauth"
    data = body["message"]["data"]
    assert data["type"] == "task_completed" and data["task_id"] == "task-9" and data["channel"] == "tasks"
    assert len(data["body"]) == push.MAX_BODY and body["message"]["android"]["priority"] == "high"
    assert all(isinstance(v, str) for v in data.values())
    assert push.device_count() == 1                         # the unregistered phone was dropped


def test_task_watcher_announces_each_finish_once(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "state.db")
    old = db.create_task(title="Finished before start", prompt="x")
    db.update_task(old, {"phase": "completed"})

    sent = []
    monkeypatch.setattr(push, "send", lambda kind, title, body, task_id="", channel="tasks": sent.append((kind, body, task_id)) or 1)
    watcher = push.TaskPushWatcher(db)
    assert watcher.check_once() == 0                         # nothing new at startup

    done = db.create_task(title="Render video", prompt="x")
    failed = db.create_task(title="Broken job", prompt="x")
    cancelled = db.create_task(title="Stopped job", prompt="x")
    db.update_task(done, {"phase": "completed"})
    db.update_task(failed, {"phase": "failed", "last_error": "boom"})
    db.update_task(cancelled, {"phase": "failed", "last_error": "Cancelled manually via Mission Control Web HUD"})
    watcher.check_once()
    watcher.check_once()
    assert sorted(sent) == [("task_completed", "Render video", done), ("task_failed", "Broken job", failed)]

    db.update_task(failed, {"phase": "backlog"})            # retried
    watcher.check_once()
    db.update_task(failed, {"phase": "completed"})
    watcher.check_once()
    assert sent[-1] == ("task_completed", "Broken job", failed)


def test_owner_alerts_go_to_the_phone(monkeypatch):
    alerts = []
    monkeypatch.setattr(push, "send_alert", lambda text: alerts.append(text) or 1)
    assert core.config.notify_owner("Backup failed") is True
    assert alerts == ["Backup failed"]
    monkeypatch.setattr(push, "send_alert", lambda text: 0)         # no phone registered: reported, not hidden
    assert core.config.notify_owner("Backup failed") is False


def test_backup_restart_and_logs(hud, monkeypatch):
    db, client = hud
    monkeypatch.setattr(backup, "mirror_dir", lambda: None)
    res = client.post("/api/backup").json()
    assert res["ok"] is True and "Memory backed up" in res["message"]
    assert backup.last_result(db)["ok"] is True

    restarts = []
    monkeypatch.setattr(hud_common, "schedule_restart",
                        lambda reason, now=False: restarts.append((reason, now)) or {"status": "restarting"})
    res = client.post("/api/restart")
    assert res.status_code == 202 and res.json() == {"status": "restarting"} and restarts == [("DeskAI", False)]
    client.post("/api/restart?now=true")
    assert restarts[-1] == ("DeskAI", True)

    db.append_log("INFO", "first line")
    db.append_log("INFO", "second line")
    logs = client.get("/api/logs?limit=2").json()
    assert [l["message"] for l in logs] == ["first line", "second line"]
    assert client.get("/api/logs?limit=0").status_code == 422


def test_ask_user_rules(tmp_path):
    from core.assistant_tools import AssistantTools
    tools = AssistantTools(DatabaseManager(tmp_path / "s.db"), None, tmp_path, agentwork=FakeAgentWork())
    assert "Error" in tools.ask_user("Which one?", ["only one"])
    assert "Error" in tools.ask_user("Which one?", ["a", "A", " a "])            # duplicates collapse to one
    assert "Error" in tools.ask_user("?", ["a", "b"])
    assert "Asked" in tools.ask_user("Which project is it for?", ["site", "alwaysonagent", "x" * 90])
    assert tools.question == {"text": "Which project is it for?", "options": ["site", "alwaysonagent", "x" * 60],
                              "allow_other": True}
    assert "one question per reply" in tools.ask_user("Another?", ["a", "b"])
    assert "wait for their answer" in tools.propose_task("Add a calendar page", project="site")
    assert "wait for their answer" in tools.propose_project("recipes", "https://github.com/me/r.git", "Recipes")

    tools = AssistantTools(DatabaseManager(tmp_path / "t.db"), None, tmp_path, agentwork=FakeAgentWork())
    tools.propose_task("Add a calendar page", project="site")
    assert "ask before proposing" in tools.ask_user("Which one?", ["a", "b"])


def test_propose_project_rules(tmp_path):
    from core.assistant_tools import AssistantTools
    tools = AssistantTools(DatabaseManager(tmp_path / "s.db"), None, tmp_path, agentwork=FakeAgentWork())
    assert "lowercase" in tools.propose_project("Bad Name", "https://github.com/me/r.git", "Recipes")
    assert "https git link" in tools.propose_project("recipes", "github.com/me/r", "Recipes")
    assert "already an AgentWork project" in tools.propose_project("site", "https://github.com/x/site.git", "The website")
    assert "Proposed" in tools.propose_project("Recipes", "https://github.com/me/recipes.git", " Recipe  app ")
    p = tools.proposals[0]
    assert p["kind"] == "add_project" and p["add"] == {"name": "recipes", "repo": "https://github.com/me/recipes.git",
                                                        "description": "Recipe app"}


def test_chat_returns_a_question(hud):
    db, client = hud

    class AskingMemory(FakeMemory):
        def respond(self, user_text, system_context="", tools=None, **kwargs):
            tools.ask_user("Which project should the /gif command go in?", ["site", "alwaysonagent"])
            return "Which project should the /gif command go in?"
    hud_state.set_memory(AskingMemory(db))
    data = client.post("/api/chat", json={"message": "add a /gif command"}).json()
    q = data["question"]
    assert q["text"] == "Which project should the /gif command go in?" and q["options"] == ["site", "alwaysonagent"]
    assert q["allow_other"] is True and len(q["id"]) == 10 and data["proposals"] == []

    hud_state.set_memory(FakeMemory(db))
    assert client.post("/api/chat", json={"message": "hi"}).json()["question"] is None


def test_add_project_card_and_endpoint(hud):
    db, client = hud
    mem = FakeMemory(db)

    class ProposingMemory(FakeMemory):
        def respond(self, user_text, system_context="", tools=None, **kwargs):
            tools.propose_project("recipes", "https://github.com/me/recipes.git", "Recipe app")
            return "Want me to add it?"
    hud_state.set_memory(ProposingMemory(db))
    p = client.post("/api/chat", json={"message": "my recipe repo"}).json()["proposals"][0]
    assert p["kind"] == "add_project" and "recipes" in p["instruction"]

    hud_state.set_memory(mem)
    res = client.post(f"/api/proposals/{p['id']}/run").json()
    assert res["status"] == "success" and res["project"] == "recipes" and "task_id" not in res
    assert hud_state.get_agentwork().added == ["recipes"]
    assert any("added the AgentWork project 'recipes'" in e for e in mem.events)

    res = client.post("/api/agentwork/projects", json={"name": "blog", "repo": "https://github.com/me/blog.git",
                                                      "description": "My blog"})
    assert res.status_code == 200 and res.json()["project"]["name"] == "blog"
    res = client.post("/api/agentwork/projects", json={"name": "x", "repo": "https://unreachable.example/a/b.git",
                                                      "description": "Nope"})
    assert res.status_code == 400 and "couldn't reach" in res.json()["detail"]


def test_memory_budget_endpoint(tmp_path, monkeypatch):
    from core.database import DatabaseManager
    from core.llm_usage import UsageLog
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests.test_deskai_phase2 import FakeMemory, client_from

    db = DatabaseManager(tmp_path / "budget_test.db")
    hud_state.set_db(db)
    hud_state.set_memory(FakeMemory(db))

    usage = UsageLog(db)
    usage.record("gemini-3.8-flash", {"prompt_tokens": 100, "completion_tokens": 50}, provider="gemini")
    usage.record("gemini-3.5-flash-lite", {"prompt_tokens": 80, "completion_tokens": 20}, provider="gemini")

    client = client_from(hud_app)
    res = client.get("/api/memory/budget")
    assert res.status_code == 200
    data = res.json()
    assert data["calls_today"] == 2
    assert data["tokens_today"] == 250
    assert "google_gemini_pacific" in data["resets"]
    assert "cloudflare_utc" in data["resets"]
    models = {m["model"]: m for m in data["by_model"]}
    assert "gemini-3.8-flash" in models and models["gemini-3.8-flash"]["calls"] == 1

