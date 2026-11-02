"""
Going live: AgentWork changes to the agent itself or the website, put live safely
=================================================================================
- A job that changed nothing is "needs your input", not a success (exit code 4 from AgentWork).
- The owner's tap on the card's chip (POST /api/tasks/{id}/apply) is the go-ahead; in chat the agent only shows
  that card (offer_go_live), never acts.
- scripts/apply_update.py, on practice git repos: failing tests stop it before the live code is touched; a
  healthy restart keeps the change and pushes it; an unhealthy one rolls back to the exact previous commit.
- scripts/apply_website.py, on practice repos: it catches up with a main that moved on, builds, tests and pushes;
  a clash or a failing test pushes nothing.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("CONNECTORS_DIR", str(Path(tempfile.gettempdir()) / "no_connectors_in_tests"))

from core import self_update  # noqa: E402
from core.assistant_tools import AssistantTools  # noqa: E402
from core.database import NEEDS_INPUT_EXIT_CODE, DatabaseManager  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "state.db")


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def finished_job(db, exit_code=0, project="alwaysonagent", engine="agentwork"):
    tid = db.create_task(title=f"[{project}] Add a ping command", prompt="add /ping", engine=engine,
                         metadata={"project": project})
    db.update_task(tid, {"phase": "completed", "exit_code": exit_code})
    return tid


# ------------------------------------------------------------------------------------------ needs your input
def test_a_job_that_changed_nothing_needs_input_and_isnt_retried(tmp_path):
    from core.daemon import AutonomousDaemon
    daemon = AutonomousDaemon(workspace_dir=tmp_path / "ws", db_file=str(tmp_path / "ws" / "state.db"),
                              log_file=str(tmp_path / "ws" / "worker.log"))
    try:
        tid = daemon.db.create_task(title="[site] Fix the footer", prompt="fix it", engine="agentwork",
                                    metadata={"project": "site"})
        daemon.db.update_task(tid, {"phase": "in_progress"})
        daemon._finalize_task_completion(daemon.db.get_task(tid), NEEDS_INPUT_EXIT_CODE,
                                         "I couldn't find the footer file.\n[NEEDS_INPUT] The job changed no files.",
                                         "agentwork", False)
        t = daemon.db.get_task(tid)
        assert t["phase"] == "completed" and t["needs_input"] and t["retry_count"] == 0
        assert t["output_summary"].startswith("⚠️ Needs your input") and "couldn't find the footer" in t["output_summary"]

        from interfaces.hud.common import public_task
        assert public_task(t)["status_text"] == "Needs your input"
    finally:
        for h in list(daemon.logger.handlers):
            h.close()
            daemon.logger.removeHandler(h)


def test_exit_4_from_another_engine_is_still_a_failure(tmp_path):
    from core.daemon import AutonomousDaemon
    daemon = AutonomousDaemon(workspace_dir=tmp_path / "ws", db_file=str(tmp_path / "ws" / "state.db"),
                              log_file=str(tmp_path / "ws" / "worker.log"))
    try:
        tid = daemon.db.create_task(title="Script", prompt="x", engine="cloud")
        daemon.db.update_task(tid, {"phase": "in_progress"})
        daemon._finalize_task_completion(daemon.db.get_task(tid), 4, "error", "cloud", False)
        t = daemon.db.get_task(tid)
        assert t["phase"] != "completed" and not t["needs_input"]
    finally:
        for h in list(daemon.logger.handlers):
            h.close()
            daemon.logger.removeHandler(h)


def test_the_push_says_needs_input(db, monkeypatch):
    from core import push
    sent = []
    monkeypatch.setattr(push, "send", lambda kind, title, body, **kw: sent.append((kind, title)) or 1)
    watcher = push.TaskPushWatcher(db)                  # running before the job finishes
    finished_job(db, exit_code=NEEDS_INPUT_EXIT_CODE)
    watcher.check_once()
    assert sent == [("task_completed", "⚠️ Needs your input")]


# ------------------------------------------------------------------------------------------ the chat tool
@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    monkeypatch.setattr(self_update, "AGENT_WORK_DIR", tmp_path / "AgentWork")
    monkeypatch.setattr(self_update, "UPDATES", tmp_path / "updates")
    launched = []
    monkeypatch.setattr(self_update.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    return launched


def test_push_it_live_shows_the_card_but_never_acts(db, tmp_path, workspaces):
    tid = finished_job(db, project="website")
    (tmp_path / "AgentWork" / "workspaces" / tid).mkdir(parents=True)
    tools = AssistantTools(db, None, tmp_path, user_text="push it to the live site")
    out = tools.call("offer_go_live", {"task_id": tid})
    assert "🚀 Push live" in out and "Nothing has gone live yet" in out
    assert tools.card_task == tid and workspaces == []                  # only the user's tap starts it


def test_the_chip_tap_starts_the_projects_go_live_program(db, tmp_path, workspaces):
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    site, agent = finished_job(db, project="website"), finished_job(db)
    for tid in (site, agent):
        (tmp_path / "AgentWork" / "workspaces" / tid).mkdir(parents=True)
    original = hud_state.db
    hud_state.set_db(db)
    try:
        client = client_from(hud_app, addr=LOCAL_ADDR)
        r = client.post(f"/api/tasks/{site}/apply")
        assert r.status_code == 202 and r.json()["project"] == "website"
        assert workspaces[0][1].endswith("apply_website.py") and workspaces[0][workspaces[0].index("--job") + 1] == site
        (tmp_path / "updates" / f"{site}.json").write_text('{"status": "failed"}')     # (as if it had stopped)
        r = client.post(f"/api/tasks/{agent}/apply")
        assert r.status_code == 202 and workspaces[1][1].endswith("apply_update.py")
        assert workspaces[1][workspaces[1].index("--title") + 1] == "Add a ping command"
        r = client.post("/api/tasks/task-999/apply")
        assert r.status_code == 409
    finally:
        hud_state.set_db(original)


def test_the_go_live_chip_follows_the_jobs_state(db, tmp_path, workspaces):
    from interfaces.hud.action_chips import build_task_action_chips
    tid = finished_job(db, project="website")
    labels = lambda: [c["label"] for c in build_task_action_chips(db.get_task(tid))]
    chip = build_task_action_chips(db.get_task(tid))[-1]
    assert chip == {"label": "🚀 Push live", "action": "post", "url": f"/api/tasks/{tid}/apply", "variant": "primary"}
    (tmp_path / "updates").mkdir(exist_ok=True)
    for status, label in (("testing", "⏳ Going live…"), ("failed", "🔁 Try again"), ("applied", "✅ Live")):
        (tmp_path / "updates" / f"{tid}.json").write_text(json.dumps({"status": status}))
        assert labels()[-1] == label


@pytest.mark.parametrize("case,reason", [
    ("other project", "can't go live from here"),
    ("changed nothing", "changed nothing"),
    ("no copy", "is gone"),
    ("running", "another update"),
])
def test_what_cant_go_live_is_refused(db, tmp_path, workspaces, case, reason):
    tid = finished_job(db, project="site" if case == "other project" else "alwaysonagent",
                       exit_code=NEEDS_INPUT_EXIT_CODE if case == "changed nothing" else 0)
    if case != "no copy":
        (tmp_path / "AgentWork" / "workspaces" / tid).mkdir(parents=True)
    if case == "running":
        (tmp_path / "updates").mkdir()
        (tmp_path / "updates" / "lock").write_text("task-000")
    out = AssistantTools(db, None, tmp_path, user_text="apply it").call("offer_go_live", {"task_id": tid})
    assert reason in out and workspaces == []


# ------------------------------------------------------------------------------------------ apply_update.py
@pytest.fixture
def practice(tmp_path, monkeypatch):
    """A practice 'live' repo with a GitHub stand-in, and a job copy (worktree) with a change in it."""
    origin, live = tmp_path / "origin.git", tmp_path / "live"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git("init", "-q", "-b", "main", str(live), cwd=tmp_path)
    (live / "tests").mkdir()
    (live / "app.py").write_text("VERSION = 1\n")
    (live / "tests" / "test_app.py").write_text("import app\n\ndef test_version():\n    assert app.VERSION >= 1\n")
    (live / "conftest.py").write_text("import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    for args in (("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v1"),
                 ("remote", "add", "origin", str(origin)), ("push", "-q", "origin", "main")):
        git(*args, cwd=live)
    job = tmp_path / "AgentWork" / "workspaces" / "task-061"
    git("worktree", "add", "-q", "-b", "agent/task-061", str(job), cwd=live)
    spec = importlib.util.spec_from_file_location("apply_update", _ROOT / "scripts" / "apply_update.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LIVE", live)
    monkeypatch.setattr(mod, "UPDATES", tmp_path / "updates")
    monkeypatch.setattr(mod, "LOG", tmp_path / "worker.log")
    monkeypatch.setattr(mod.Update, "status", lambda self: {"status": "online", "active_tasks_count": 0})
    restarts = []
    monkeypatch.setattr(mod.Update, "restart", lambda self: restarts.append(1) or 0.0)
    return mod, live, job, origin, restarts


def test_failing_tests_stop_before_the_live_code_is_touched(practice):
    mod, live, job, origin, restarts = practice
    before = git("rev-parse", "HEAD", cwd=live)
    (job / "app.py").write_text("VERSION = 0\n")                     # breaks the test
    with pytest.raises(mod.UpdateError, match="tests failed on the job's copy"):
        mod.Update("task-061", job, "Break it", 8080).run()
    assert git("rev-parse", "HEAD", cwd=live) == before and restarts == []


def test_a_healthy_update_is_kept_and_pushed(practice, monkeypatch):
    mod, live, job, origin, restarts = practice
    (job / "app.py").write_text("VERSION = 2\n")
    monkeypatch.setattr(mod.Update, "healthy_after", lambda self, at: "")
    message = mod.Update("task-061", job, "Version 2", 8080).run()
    assert message.startswith("✅") and "pushed to GitHub" in message and restarts == [1]
    assert (live / "app.py").read_text() == "VERSION = 2\n"
    assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "HEAD", cwd=live)


def test_an_unhealthy_update_rolls_back_to_the_exact_previous_commit(practice, monkeypatch):
    mod, live, job, origin, restarts = practice
    before = git("rev-parse", "HEAD", cwd=live)
    (job / "app.py").write_text("VERSION = 3\n")
    checks = iter(["the agent restarted by itself (it's crashing)", ""])   # broken, then healthy after rollback
    monkeypatch.setattr(mod.Update, "healthy_after", lambda self, at: next(checks))
    update = mod.Update("task-061", job, "Version 3", 8080)
    message = update.run()
    assert message.startswith("↩️") and "crashing" in message and update.state["status"] == "rolled_back"
    assert git("rev-parse", "HEAD", cwd=live) == before and (live / "app.py").read_text() == "VERSION = 1\n"
    assert restarts == [1, 1] and git("rev-parse", "main", cwd=origin) == before     # nothing pushed


def test_it_waits_for_an_idle_agent(practice, monkeypatch):
    mod, live, job, origin, restarts = practice
    (job / "app.py").write_text("VERSION = 2\n")
    monkeypatch.setattr(mod.Update, "status", lambda self: {"status": "online", "active_tasks_count": 1})
    with pytest.raises(mod.UpdateError, match="a task is running"):
        mod.Update("task-061", job, "Version 2", 8080).run()


def test_the_log_check_sees_a_traceback_after_the_restart(practice, tmp_path):
    mod = practice[0]
    import datetime
    at = datetime.datetime(2026, 9, 30, 15, 0, 0).timestamp()
    (tmp_path / "worker.log").write_text(
        "[2026-09-30 14:59:00] [ERROR] old problem\nTraceback (most recent call last):\n"
        "[2026-09-30 15:00:05] [ERROR] Startup failed\nTraceback (most recent call last):\n  File x\n")
    assert mod._log_errors_since(at) == ["Traceback (most recent call last):"]


# ------------------------------------------------------------------------------------------ apply_website.py
@pytest.fixture
def site(tmp_path, monkeypatch):
    """A GitHub stand-in for the website, AgentWork's clone of it, and a job worktree with a change in it.
    npm is replaced: 'build' regenerates sitemap.xml, 'test' passes unless the page says FAIL."""
    origin, clone = tmp_path / "site.git", tmp_path / "clone"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    (clone / "index.html").write_text("<h1>Home</h1>\n")
    (clone / "about.html").write_text("<h1>About</h1>\n")
    for args in (("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v1"),
                 ("push", "-q", "origin", "main")):
        git(*args, cwd=clone)
    job = tmp_path / "AgentWork" / "workspaces" / "task-071"
    git("worktree", "add", "-q", "-b", "agent/task-071", str(job), cwd=clone)
    spec = importlib.util.spec_from_file_location("apply_website", _ROOT / "scripts" / "apply_website.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "UPDATES", tmp_path / "updates")
    real_run = mod.GoLive.run_cmd

    def run_cmd(self, args, what, timeout=900, check=True):
        if args[0] == mod.NPM:
            if args[1] == "run" and args[2] == "build":
                (self.workspace / "sitemap.xml").write_text("<urlset>" + "".join(
                    sorted(p.name for p in self.workspace.glob("*.html"))) + "</urlset>")
            if args[1] == "test" and "FAIL" in "".join(p.read_text() for p in self.workspace.glob("*.html")):
                raise mod.GoLiveError("npm test failed: 1 failing")
            return subprocess.CompletedProcess(args, 0, "ok: 12 passed", "")
        return real_run(self, args, what, timeout, check)
    monkeypatch.setattr(mod.GoLive, "run_cmd", run_cmd)
    (job / "node_modules").mkdir()
    return mod, origin, clone, job


def test_a_website_change_catches_up_builds_tests_and_pushes(site, tmp_path):
    mod, origin, clone, job = site
    (job / "guide.html").write_text("<h1>Sympathy card guide</h1>\n")
    # meanwhile the owner's website session pushed something else to main
    (clone / "about.html").write_text("<h1>About us</h1>\n")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "owner edit", cwd=clone)
    git("push", "-q", "origin", "main", cwd=clone)
    message = mod.GoLive("task-071", job, "Add the sympathy card guide").run()
    assert message.startswith("✅") and "example-site.com" in message
    shown = lambda path: git("show", f"main:{path}", cwd=origin)
    assert "Sympathy card guide" in shown("guide.html") and "About us" in shown("about.html")
    assert "guide.html" in shown("sitemap.xml")                                   # the build ran on the result
    log = git("log", "--format=%s", "main", cwd=origin)
    assert "build: regenerate (task-071)" in log and "owner edit" in log


def test_a_failing_test_pushes_nothing(site):
    mod, origin, clone, job = site
    before = git("rev-parse", "main", cwd=origin)
    (job / "guide.html").write_text("<h1>FAIL</h1>\n")
    with pytest.raises(mod.GoLiveError, match="npm test failed"):
        mod.GoLive("task-071", job, "Broken guide").run()
    assert git("rev-parse", "main", cwd=origin) == before


def test_a_clash_with_the_live_site_pushes_nothing(site):
    mod, origin, clone, job = site
    (job / "about.html").write_text("<h1>About (job)</h1>\n")
    (clone / "about.html").write_text("<h1>About (owner)</h1>\n")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "owner edit", cwd=clone)
    git("push", "-q", "origin", "main", cwd=clone)
    before = git("rev-parse", "main", cwd=origin)
    with pytest.raises(mod.GoLiveError, match="changed in the same places"):
        mod.GoLive("task-071", job, "About page").run()
    assert git("rev-parse", "main", cwd=origin) == before


def test_a_rebase_error_that_isnt_a_clash_says_what_git_said(site, monkeypatch):
    """2026-10-28 audit 5.5: every rebase failure was reported as "the live site changed in the same places", even
    git's "Filename too long"; the advice (run the job again) was wrong."""
    mod, origin, clone, job = site
    (job / "guide.html").write_text("<h1>Guide</h1>\n")
    real = mod.GoLive.run_cmd

    def failing_rebase(self, args, what, timeout=900, check=True):
        if what == "rebase":
            return subprocess.CompletedProcess(args, 128, "", "fatal: failed to stat 'x': Filename too long")
        return real(self, args, what, timeout, check)
    monkeypatch.setattr(mod.GoLive, "run_cmd", failing_rebase)
    with pytest.raises(mod.GoLiveError) as e:
        mod.GoLive("task-071", job, "Guide").run()
    assert "Filename too long" in str(e.value) and "same places" not in str(e.value)
