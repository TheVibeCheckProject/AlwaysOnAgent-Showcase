"""Computer tasks: the chat proposes a card, Run queues a task, the daemon runs AgentComputerUse (a stand-in
here: tests never run the real tool, which moves the mouse) and shows its steps and result."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import actions, computer_use  # noqa: E402
from core.assistant_tools import AssistantTools  # noqa: E402
from core.database import DatabaseManager  # noqa: E402

STAND_IN = """import json, pathlib, sys
here = pathlib.Path(__file__)
(here.parent / "args.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
print(json.dumps({"event": "progress", "step": 1, "action": "open_url", "observation": "Edge is open"}), flush=True)
print(json.dumps({"event": "progress", "step": 2, "action": "done", "observation": "the panel shows Springfield"}), flush=True)
print((here.parent / "final.json").read_text(encoding="utf-8"), flush=True)
"""


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "state.db")


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    def make(final: dict):
        folder = tmp_path / "AgentComputerUse"
        folder.mkdir(exist_ok=True)
        (folder / "agentcomputeruse.py").write_text(STAND_IN, encoding="utf-8")
        (folder / "final.json").write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(computer_use, "COMPUTER_USE_DIR", folder)
        return folder
    return make


def test_the_chat_only_proposes_a_card(db, tmp_path):
    tools = AssistantTools(db, None, tmp_path)
    out = tools.call("computer_task", {"goal": "Find the current temperature in Springfield, Illinois"})
    assert "Nothing has started" in out
    assert tools.proposals[0]["kind"] == "computer"
    assert tools.proposals[0]["add"]["goal"] == "Find the current temperature in Springfield, Illinois"
    assert db.list_tasks(limit=5) == []                      # nothing runs before the owner's tap
    assert "Error" in AssistantTools(db, None, tmp_path).call("computer_task", {"goal": "go"})


def test_run_queues_a_computer_task(db):
    task_id = actions.queue_computer(db, "Find the  current temperature in Springfield", source="DeskAI suggestion")
    t = db.get_task(task_id)
    assert t["engine"] == "computer" and t["metadata"]["goal"] == "Find the current temperature in Springfield"


def test_the_daemon_runs_the_tool_and_shows_its_steps_and_answer(db, stand_in):
    from core.daemon import EngineDispatcher
    folder = stand_in({"event": "done", "status": "done", "answer": "71°F in Springfield, Illinois",
                       "evidence": ["Springfield, IL", "71 °Fahrenheit"], "steps": 2})
    task_id = actions.queue_computer(db, "Find the current temperature in Springfield, Illinois")
    db.update_task(task_id, {"phase": "in_progress"})          # progress shows while a task runs
    d = EngineDispatcher.__new__(EngineDispatcher)
    d.db, d.logger = db, __import__("logging").getLogger("test")
    code, text, engine, _ = d._execute_computer(db.get_task(task_id))
    assert code == 0 and engine == "computer"
    assert text.startswith("71°F in Springfield, Illinois") and '"Springfield, IL"' in text
    args = json.loads((folder / "args.json").read_text(encoding="utf-8"))
    assert args[:3] == ["run", "Find the current temperature in Springfield, Illinois", "--go"]
    assert "Step 2: done" in (db.get_task(task_id).get("progress") or {}).get("message", "")


SAYS = """import json
for e in ({"step": 1, "action": "launch_app", "say": "Opening Notepad", "level": "info"},
          {"step": 2, "action": "type", "say": "Typing \\u201cHello\\u201d", "level": "info"},
          {"step": 3, "action": "click", "say": "Clicking \\u201cDelete\\u201d: not allowed (not its window)",
           "level": "warn"}):
    print(json.dumps({"event": "progress", **e}), flush=True)
print(json.dumps({"event": "done", "status": "needs_owner", "question": "it got stuck"}), flush=True)
"""


def test_the_task_keeps_a_plain_log_of_what_it_did(db, stand_in):
    """2026-10-10, the owner: a log on the task card of what the agent did, and any errors."""
    from core.daemon import EngineDispatcher
    from interfaces.hud.common import public_task
    folder = stand_in({})
    (folder / "agentcomputeruse.py").write_text(SAYS, encoding="utf-8")
    task_id = actions.queue_computer(db, "Open Notepad and type Hello")
    db.update_task(task_id, {"phase": "in_progress"})
    d = EngineDispatcher.__new__(EngineDispatcher)
    d.db, d.logger = db, __import__("logging").getLogger("test")
    code, text, _, _ = d._execute_computer(db.get_task(task_id))
    assert code == 1
    assert (db.get_task(task_id).get("progress") or {}).get("message") == \
        "Step 3 · Clicking “Delete”: not allowed (not its window)"
    log = public_task(db.get_task(task_id))["activity_log"]
    assert [(e["step"], e["text"], e["level"]) for e in log] == [
        (1, "Opening Notepad", "info"), (2, "Typing “Hello”", "info"),
        (3, "Clicking “Delete”: not allowed (not its window)", "warn"),
        (None, "Needs you: it got stuck", "warn")]


def test_a_tool_failure_is_in_the_log(db, stand_in):
    from core.daemon import EngineDispatcher
    folder = stand_in({})
    (folder / "agentcomputeruse.py").write_text(
        'import json\nprint(json.dumps({"event": "error", "message": "the screen is locked"}), flush=True)\n',
        encoding="utf-8")
    task_id = actions.queue_computer(db, "Open Notepad")
    d = EngineDispatcher.__new__(EngineDispatcher)
    d.db, d.logger = db, __import__("logging").getLogger("test")
    d._execute_computer(db.get_task(task_id))
    log = db.get_task(task_id)["metadata"]["activity_log"]
    assert log[-1]["level"] == "error" and "the screen is locked" in log[-1]["text"]


def test_a_stop_for_approval_is_not_a_success(db, stand_in):
    from core.daemon import EngineDispatcher
    stand_in({"event": "done", "status": "needs_approval", "question": "clicking 'Submit application'",
              "detail": {"item": "[12] Button 'Submit application'"}})
    task_id = actions.queue_computer(db, "Apply for the warehouse job on the site")
    d = EngineDispatcher.__new__(EngineDispatcher)
    d.db, d.logger = db, __import__("logging").getLogger("test")
    code, text, _, _ = d._execute_computer(db.get_task(task_id))
    assert code == 1 and "Nothing was submitted" in text and "Submit application" in text


# ---------------------------------------------------------------------------------------- Needs you
def _request(folder, rid="abc123", kind="approve", choices=("Approve", "Deny"), closed=False):
    req = folder / "data" / "requests"
    req.mkdir(parents=True, exist_ok=True)
    (req / f"{rid}.png").write_bytes(b"\x89PNG fake")
    data = {"id": rid, "kind": kind, "question": "Approve clicking 'Submit application'?", "choices": list(choices),
            "url": "https://jobs.example.com/apply", "screenshot": f"{rid}.png", "created": "2026-10-05T20:00:00"}
    if closed:
        data["closed"] = "2026-10-05T20:05:00"
    (req / f"{rid}.json").write_text(json.dumps(data), encoding="utf-8")


def test_pending_requests_and_answers(stand_in):
    folder = stand_in({"event": "done", "status": "done"})
    _request(folder)
    _request(folder, "old999", closed=True)
    pending = computer_use.pending_requests()
    assert [r["id"] for r in pending] == ["abc123"]
    assert computer_use.screenshot_path("abc123").read_bytes() == b"\x89PNG fake"
    with pytest.raises(computer_use.ComputerUseError, match="Approve or Deny"):
        computer_use.answer("abc123", "sure")
    assert computer_use.answer("abc123", "approve")["answer"] == "Approve"
    saved = json.loads((folder / "data" / "answers" / "abc123.json").read_text(encoding="utf-8"))
    assert saved == {"answer": "Approve"}
    assert computer_use.pending_requests() == []                       # answered
    with pytest.raises(computer_use.ComputerUseError, match="already"):
        computer_use.answer("abc123", "Deny")
    with pytest.raises(computer_use.ComputerUseError):
        computer_use.answer("../../etc", "Deny")                        # ids are checked


def test_a_choice_must_be_one_of_the_choices(stand_in):
    folder = stand_in({"event": "done", "status": "done"})
    _request(folder, "pop1", kind="choice", choices=("Allow", "Block"))
    with pytest.raises(computer_use.ComputerUseError, match="one of the choices"):
        computer_use.answer("pop1", "Maybe")
    assert computer_use.answer("pop1", "Block")["answer"] == "Block"


def test_a_secret_goes_to_the_tool_exactly_and_is_never_echoed(stand_in):
    """A password or code typed in the app: written exactly as typed for the tool (which types it into the field
    and deletes the file), never returned in the response, and the alert says to open DeskAI."""
    folder = stand_in({"event": "done", "status": "done"})
    _request(folder, "pw1", kind="secret", choices=())
    with pytest.raises(computer_use.ComputerUseError):
        computer_use.answer("pw1", "")
    assert computer_use.answer("pw1", " my pass word ")["answer"] == "(sent)"
    saved = json.loads((folder / "data" / "answers" / "pw1.json").read_text(encoding="utf-8"))
    assert saved == {"answer": " my pass word "}
    title, body = computer_use.alert_text({"kind": "secret", "question": "Type your password", "url": "https://accounts.google.com/x"})
    assert "DeskAI" in title and "accounts.google.com" in title and "not to the AI" in body


def test_the_alert_repeats_until_answered(stand_in):
    folder = stand_in({"event": "done", "status": "done"})
    _request(folder)
    sent, naps = [], []

    def fake_send(kind, title, body, task_id, channel, extra):
        sent.append((kind, title, channel, extra))

    def fake_sleep(secs):
        naps.append(secs)
        if len(naps) == 3:                                             # the owner answers during the 3rd wait
            computer_use.answer("abc123", "Deny")

    req = computer_use.get_request("abc123")
    computer_use.alert_owner(req, "task-001", send=fake_send, sleep=fake_sleep)
    import time as _t
    for _ in range(50):
        if len(naps) >= 3:
            break
        _t.sleep(0.05)
    _t.sleep(0.2)
    assert sent[0][0] == "needs_you" and sent[0][2] == "needs_you"
    assert sent[0][1] == "Approve? (jobs.example.com)"
    assert sent[0][3]["screenshot"] == "/api/computer/requests/abc123/screenshot"
    assert [s[1].startswith("Reminder") for s in sent] == [False, True, True]   # reminders stop once answered


def test_the_endpoints(stand_in):
    from interfaces.hud import server
    from tests._hud_client import LOCAL_ADDR, client_from
    folder = stand_in({"event": "done", "status": "done"})
    _request(folder)
    client = client_from(server.app, addr=LOCAL_ADDR)
    assert client.get("/api/computer/requests").json()["requests"][0]["id"] == "abc123"
    shot = client.get("/api/computer/requests/abc123/screenshot")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"
    assert client.post("/api/computer/requests/abc123/answer", json={"answer": "nope"}).status_code == 400
    assert client.post("/api/computer/requests/abc123/answer", json={"answer": "Approve"}).json()["answer"] == "Approve"
    assert client.post("/api/computer/requests/abc123/answer", json={"answer": "Deny"}).status_code == 409


TAKEOVER_STAND_IN = '''import json, pathlib
HERE = pathlib.Path(__file__).parent
STATE = HERE / "takeover_state.json"
INPUTS = HERE / "takeover_inputs.json"
class TakeoverError(Exception):
    pass
def status():
    return json.loads(STATE.read_text()) if STATE.exists() else {"active": False}
def active():
    return status()["active"]
def start(request_id=None):
    STATE.write_text(json.dumps({"active": True, "request_id": request_id}))
    return {"active": True, "request_id": request_id, "screen": [1920, 1080]}
def release():
    rid = status().get("request_id")
    STATE.unlink(missing_ok=True)
    return rid
VIEW_ONLY_FRAMES = []
def frame(view_only=False):
    VIEW_ONLY_FRAMES.append(view_only)
    return b"JPEGDATA", {"width": 1920, "height": 1080}
def apply(ev):
    if not active():
        raise TakeoverError("take over first")
    if ev.get("type") not in ("tap", "text"):
        raise TakeoverError("unknown input")
    done = json.loads(INPUTS.read_text()) if INPUTS.exists() else []
    INPUTS.write_text(json.dumps(done + [ev]))
    return ev["type"]
'''


def test_take_over_from_a_request_and_hand_back(stand_in):
    """The owner takes over for a waiting request, taps and types from the phone, and hands back: the request gets
    HANDED_BACK (the tool then looks again) and control is released. Stand-in takeover module: nothing moves."""
    from interfaces.hud import server
    from tests._hud_client import LOCAL_ADDR, client_from
    folder = stand_in({"event": "done", "status": "done"})
    (folder / "takeover.py").write_text(TAKEOVER_STAND_IN, encoding="utf-8")
    computer_use._TAKEOVER.clear()
    _request(folder, "cap1", kind="choice", choices=("I solved it",))
    client = client_from(server.app, addr=LOCAL_ADDR)
    assert client.get("/api/computer/takeover/frame").status_code == 409             # take over first
    assert client.post("/api/computer/takeover/input", json={"type": "tap", "x": 0.5, "y": 0.5}).status_code == 409
    assert client.post("/api/computer/takeover/start", json={"request_id": "nope"}).status_code == 409
    started = client.post("/api/computer/takeover/start", json={"request_id": "cap1"}).json()
    assert started["active"] and started["request_id"] == "cap1"
    frame = client.get("/api/computer/takeover/frame")
    assert frame.status_code == 200 and frame.content == b"JPEGDATA" and frame.headers["x-screen"] == "1920x1080"
    assert client.post("/api/computer/takeover/input", json={"type": "tap", "x": 0.5, "y": 0.5}).json()["done"] == "tap"
    assert client.post("/api/computer/takeover/input", json={"type": "explode"}).status_code == 400
    stopped = client.post("/api/computer/takeover/stop", json={}).json()
    assert stopped["handed_back_to"] == "cap1" and not client.get("/api/computer/takeover").json()["active"]
    saved = json.loads((folder / "data" / "answers" / "cap1.json").read_text(encoding="utf-8"))
    assert saved == {"answer": computer_use.HANDED_BACK}
    # Taking over with no request (just to use the computer) hands back to nobody
    client.post("/api/computer/takeover/start", json={})
    assert client.post("/api/computer/takeover/stop").json()["handed_back_to"] is None
    computer_use._TAKEOVER.clear()


def test_take_over_the_laptop_goes_through_its_own_take_over(stand_in, monkeypatch):
    """The laptop runs its own Take over (started at its login); the agent passes the phone's calls on with the key."""
    from interfaces.hud import server
    from tests._hud_client import LOCAL_ADDR, client_from
    stand_in({"event": "done", "status": "done"})
    calls = []

    def laptop(method, path, body=None, timeout=15):
        calls.append((method, path, body))
        if path == "/frame":
            return b"LAPTOPJPEG", {"X-Screen": "1920x1080"}
        if path == "/input":
            return json.dumps({"done": "tap at (960, 540)"}).encode(), {}
        return json.dumps({"active": path == "/start"}).encode(), {}
    monkeypatch.setattr(computer_use, "_laptop", laptop)
    client = client_from(server.app, addr=LOCAL_ADDR)
    assert client.post("/api/computer/takeover/start", json={"machine": "laptop"}).json()["active"]
    frame = client.get("/api/computer/takeover/frame?machine=laptop")
    assert frame.content == b"LAPTOPJPEG" and frame.headers["x-screen"] == "1920x1080"
    tap = client.post("/api/computer/takeover/input", json={"machine": "laptop", "type": "tap", "x": 0.5, "y": 0.5})
    assert tap.json()["done"] == "tap at (960, 540)"
    assert ("POST", "/input", {"type": "tap", "x": 0.5, "y": 0.5}) in calls           # "machine" isn't passed on
    assert client.post("/api/computer/takeover/stop", json={"machine": "laptop"}).json()["handed_back_to"] is None
    assert client.post("/api/computer/takeover/start", json={"machine": "toaster"}).status_code == 400


def test_view_only_shows_the_screen_without_taking_control(stand_in, monkeypatch):
    """DeskAI's "View only" box (2026-10-08): watching took control and stopped a running task. view=1 gets the
    picture with nobody in control, and leaves it that way; without it, a frame still needs Take over first."""
    from interfaces.hud import server
    from tests._hud_client import LOCAL_ADDR, client_from
    folder = stand_in({"event": "done", "status": "done"})
    (folder / "takeover.py").write_text(TAKEOVER_STAND_IN, encoding="utf-8")
    computer_use._TAKEOVER.clear()
    client = client_from(server.app, addr=LOCAL_ADDR)
    assert client.get("/api/computer/takeover/frame").status_code == 409
    watched = client.get("/api/computer/takeover/frame?view=1")
    assert watched.status_code == 200 and watched.content == b"JPEGDATA"
    assert not client.get("/api/computer/takeover").json()["active"]
    # ...and tells the tool so: a View only picture must not keep a forgotten Take over alive (2026-10-10)
    assert computer_use._takeover().VIEW_ONLY_FRAMES == [True]
    computer_use._TAKEOVER.clear()
    # The laptop's view only asks its Take over for the same
    paths = []
    monkeypatch.setattr(computer_use, "_laptop",
                        lambda method, path, body=None, timeout=15: paths.append(path) or (b"L", {"X-Screen": "1x1"}))
    client.get("/api/computer/takeover/frame?machine=laptop&view=1")
    assert paths == ["/frame?view=1"]


def test_take_over_shows_what_a_run_is_doing(stand_in, monkeypatch):
    """The banner on Take over's screen (2026-10-08): the run's goal and step, read-only, no control needed."""
    from interfaces.hud import server
    from tests._hud_client import LOCAL_ADDR, client_from
    folder = stand_in({"event": "done", "status": "done"})
    (folder / "takeover.py").write_text(TAKEOVER_STAND_IN + '''
def activity_now():
    return {"running": True, "goal": "Open notepad", "step": 2, "doing": "typing"}
''', encoding="utf-8")
    computer_use._TAKEOVER.clear()
    client = client_from(server.app, addr=LOCAL_ADDR)
    shown = client.get("/api/computer/takeover/activity").json()
    assert shown == {"running": True, "goal": "Open notepad", "step": 2, "doing": "typing"}
    assert not client.get("/api/computer/takeover").json()["active"]          # watching took nothing over
    computer_use._TAKEOVER.clear()
    # An older AgentComputerUse without the banner: nothing running, not an error
    (folder / "takeover.py").write_text(TAKEOVER_STAND_IN, encoding="utf-8")
    assert client.get("/api/computer/takeover/activity").json() == {"running": False}
    computer_use._TAKEOVER.clear()
    # The laptop's comes from its own Take over
    monkeypatch.setattr(computer_use, "_laptop", lambda method, path, body=None, timeout=15:
                        (b'{"running": false}', {}) if path == "/activity" else (b"{}", {}))
    assert client.get("/api/computer/takeover/activity?machine=laptop").json() == {"running": False}


def test_the_laptop_is_listed_once_it_is_set_up(monkeypatch):
    from core import config
    monkeypatch.setattr(config, "get_config", lambda k, d=None: {"LAPTOP_TAKEOVER_URL": "http://x:8765",
                                                                  "LAPTOP_TAKEOVER_TOKEN": "t"}.get(k, d))
    assert [m["id"] for m in computer_use.takeover_machines()] == ["mini", "laptop"]
    monkeypatch.setattr(config, "get_config", lambda k, d=None: d)
    assert [m["id"] for m in computer_use.takeover_machines()] == ["mini"]
    with pytest.raises(computer_use.ComputerUseError, match="isn't set up"):
        computer_use._laptop("GET", "/status")


def test_take_over_needs_the_tool(stand_in):
    from interfaces.hud import server
    from tests._hud_client import LOCAL_ADDR, client_from
    stand_in({"event": "done", "status": "done"})
    computer_use._TAKEOVER.clear()
    client = client_from(server.app, addr=LOCAL_ADDR)
    assert client.post("/api/computer/takeover/start", json={}).status_code == 503


def test_cancelling_task_stops_reminders_and_sends_cancel_push(stand_in):
    import time as _t
    folder = stand_in({"event": "done", "status": "done"})
    _request(folder, rid="can123")
    sent = []

    def fake_send(kind, title, body, task_id, channel, extra=None):
        sent.append((kind, title, task_id, channel, extra or {}))

    req = computer_use.get_request("can123")
    computer_use.alert_owner(req, "task-999", send=fake_send)

    assert len(sent) == 1
    assert sent[0][0] == "needs_you"
    assert sent[0][2] == "task-999"

    # Now cancel the task
    computer_use.cancel_for_task("task-999", reason="cancelled by owner")

    # Verify cancel push sent to dismiss DeskAI notification
    cancel_pushes = [s for s in sent if s[0] == "needs_you_cancel"]
    assert len(cancel_pushes) == 1
    assert cancel_pushes[0][4].get("cancel_request_id") == "can123"

    # Request is marked closed
    current = computer_use.get_request("can123")
    assert not current["pending"]
    assert current.get("closed")
    assert current.get("close_reason") == "cancelled by owner"

    # Wait briefly and verify NO reminder pushes were sent
    _t.sleep(0.1)
    reminders = [s for s in sent if s[1].startswith("Reminder")]
    assert len(reminders) == 0


def test_remind_thread_stops_if_computer_task_fails_or_finishes_in_db(db, stand_in):
    import time as _t
    from core import actions
    folder = stand_in({"event": "done", "status": "done"})
    _request(folder, rid="dbtest1")
    task_id = actions.queue_computer(db, "Test goal")
    db.update_task(task_id, {"phase": "in_progress"})
    sent, naps = [], []

    def fake_send(kind, title, body, task_id, channel, extra=None):
        sent.append((kind, title, task_id, channel, extra or {}))

    def fake_sleep(secs):
        naps.append(secs)
        # On first sleep, mark the task failed/cancelled in the DB
        db.cancel_task(task_id, "Aborted in DB")

    req = computer_use.get_request("dbtest1")
    computer_use.alert_owner(req, task_id, send=fake_send, sleep=fake_sleep, db=db)

    # Wait for the reminder thread to notice the cancel (up to 5 s): a fixed 0.1 s failed on a busy laptop
    # (2026-10-06, a Gradle build running alongside)
    deadline = _t.time() + 5
    while _t.time() < deadline and (not naps or computer_use.get_request("dbtest1")["pending"]):
        _t.sleep(0.05)

    # Reminders should have aborted because task was cancelled in DB
    reminders = [s for s in sent if s[1].startswith("Reminder")]
    assert len(reminders) == 0
    current = computer_use.get_request("dbtest1")
    assert not current["pending"]
    assert current.get("closed")


def test_a_computer_task_title_is_never_cut_mid_word(db):
    """The owner: the card ended "...download the PDF of Form W-9 into my Downloads folde"."""
    goal = "Go to irs.gov and download the PDF of Form W-9 into my Downloads folder. Don't fill anything in."
    t = db.get_task(actions.queue_computer(db, goal))
    assert t["title"] == "Computer: " + goal
    long = actions.task_title("word " * 80, "Computer: ")
    assert len(long) <= 200 and long.endswith("word…") and "  " not in long


def test_a_repeated_run_says_so_on_the_card():
    from core import computer_use
    out = computer_use.describe({"status": "done", "answer": "Done: open the W-9 page", "evidence": [],
                                 "requests": 0, "detail": {"retask": True}})
    assert out.endswith("(Repeated an earlier run: 0 AI requests.)")
    assert "Repeated" not in computer_use.describe({"status": "done", "answer": "x", "requests": 4})


def test_the_card_says_where_the_screenshots_are(tmp_path, monkeypatch):
    """2026-10-26: asked where task-233's screenshots went, the chat guessed a folder."""
    from core import computer_use
    run = tmp_path / "data" / "runs" / "20261026-212256"
    run.mkdir(parents=True)
    for n in range(3):
        (run / f"step-{n:02d}.jpg").write_bytes(b"x")
    monkeypatch.setattr(computer_use, "COMPUTER_USE_DIR", tmp_path)
    out = computer_use.describe({"status": "done", "answer": "Made the key.", "run": "20261026-212256"})
    assert out.endswith(f"Screenshots: 3, in {run}")
    # No folder, or an odd run id: nothing added
    assert computer_use.describe({"status": "done", "answer": "x", "run": "20991231-000000"}) == "x"
    assert computer_use.describe({"status": "done", "answer": "x", "run": "../../etc"}) == "x"


def test_a_bad_retask_id_never_reaches_the_tool():
    import pytest
    from core import computer_use
    with pytest.raises(computer_use.ComputerUseError):
        computer_use.forget_retask("../../state.db")


def test_a_retask_runs_with_its_exact_wording(monkeypatch):
    from fastapi.testclient import TestClient
    from interfaces.hud import computer_api, state
    from fastapi import FastAPI
    goal = "On Wikipedia, find what year Illinois became a state and tell me."
    monkeypatch.setattr(computer_use, "list_retasks", lambda: [{"id": "398ef44695c0", "goal": goal}])
    queued = []
    monkeypatch.setattr(actions, "queue_computer", lambda db, g, source="": queued.append((g, source)) or "task-9")
    monkeypatch.setattr(state, "db", object(), raising=False)
    app = FastAPI()
    app.include_router(computer_api.router)
    c = TestClient(app)
    r = c.post("/api/computer/retasks/398ef44695c0/run")
    assert r.status_code == 200 and r.json()["task_id"] == "task-9"
    assert queued == [(goal, "DeskAI /retask")]
    assert c.post("/api/computer/retasks/000000000000/run").status_code == 404


def test_credential_functions_and_api(monkeypatch):
    from fastapi.testclient import TestClient
    from interfaces.hud import computer_api
    from fastapi import FastAPI

    stored = [{"domain": "google.com", "username": "alice@gmail.com"}]
    calls = []

    def mock_run(args, input_text=None, **kwargs):
        calls.append((args, input_text))
        if args[0] == "passwords":
            return {"saved": list(stored)}
        if args[0] == "save-password":
            stored.append({"domain": args[1], "username": args[2]})
            return {"saved": args[1], "username": args[2]}
        if args[0] == "forget-password":
            before_len = len(stored)
            stored[:] = [s for s in stored if not (s["domain"] == args[1] and s["username"] == args[2])]
            return {"forgotten": len(stored) < before_len, "domain": args[1], "username": args[2]}
        return {}

    monkeypatch.setattr(computer_use, "run", mock_run)

    # Core function tests
    assert computer_use.list_passwords() == [{"domain": "google.com", "username": "alice@gmail.com"}]
    computer_use.save_password("github.com", "bob", "secret123")
    assert ("github.com", "bob") in [(s["domain"], s["username"]) for s in stored]
    assert calls[-1][1] == "secret123\n"
    computer_use.save_password("github.com", "bob", " spaced pw ")       # saved exactly as given
    assert calls[-1][1] == " spaced pw \n"
    with pytest.raises(computer_use.ComputerUseError):
        computer_use.save_password("github.com", "bob", "two\nlines")
    assert computer_use.forget_password("github.com", "bob") is True
    assert ("github.com", "bob") not in [(s["domain"], s["username"]) for s in stored]

    # API endpoint tests
    app = FastAPI()
    app.include_router(computer_api.router)
    client = TestClient(app)

    # GET /api/computer/credentials
    res = client.get("/api/computer/credentials")
    assert res.status_code == 200
    assert res.json() == {"credentials": [{"domain": "google.com", "username": "alice@gmail.com"}]}

    # POST /api/computer/credentials
    res = client.post("/api/computer/credentials", json={"domain": "anthropic.com", "username": "charlie", "password": "pw"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["saved"] == "anthropic.com"

    # DELETE /api/computer/credentials
    res = client.delete("/api/computer/credentials?domain=anthropic.com&username=charlie")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

    # DELETE non-existent credential -> 404
    res = client.delete("/api/computer/credentials?domain=unknown.com&username=none")
    assert res.status_code == 404

