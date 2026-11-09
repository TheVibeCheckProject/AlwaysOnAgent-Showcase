"""DeskAI's repo browser (interfaces/hud/github_api.py): browse the owner's GitHub repos and start work on one
without adding it as a project first."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import DatabaseManager
from interfaces.hud import app as hud_app
from interfaces.hud import github_api
from interfaces.hud import state as hud_state
from tests._hud_client import LOCAL_ADDR, client_from

REPOS = [{"repo": "example-org/Website", "description": "the site", "private": False, "updated": "2026-10-06",
          "default_branch": "main", "url": "https://github.com/example-org/Website"},
         {"repo": "example-user/TVSP", "description": "", "private": False, "updated": "2025-10-04",
          "default_branch": "master", "url": "https://github.com/example-user/TVSP"}]


class FakeAgentWork:
    def __init__(self):
        self.projects = [{"name": "website", "repo": "https://github.com/example-org/Website.git"}]
        self.added = []

    def list_projects(self):
        return list(self.projects)

    def add_project(self, name, repo, description, base="main"):
        self.added.append((name, repo, description, base))
        self.projects.append({"name": name, "repo": repo})
        return {"name": name, "repo": repo, "description": description}


class FakeMemory:
    def record_event(self, text):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "state.db")
    aw = FakeAgentWork()
    original = hud_state.db
    hud_state.set_db(db)
    monkeypatch.setattr(hud_state, "get_agentwork", lambda: aw)
    monkeypatch.setattr(hud_state, "get_memory", lambda: FakeMemory())
    github_api._repos_cache.update(at=0.0, repos=None)
    yield db, aw, client_from(hud_app, addr=LOCAL_ADDR)
    hud_state.set_db(original)


def test_repos_say_which_are_already_projects(env):
    _, _, client = env
    with patch("core.connectors.run", return_value={"repos": REPOS}):
        got = client.get("/api/github/repos").json()["repos"]
    assert [(r["repo"], r["project"]) for r in got] == [("example-org/Website", "website"),
                                                         ("example-user/TVSP", None)]


def test_browsing_a_folder_and_a_file(env):
    _, _, client = env
    with patch("core.connectors.run", return_value={"repo": "o/r", "path": "docs", "entries": []}) as run:
        assert client.get("/api/github/tree", params={"repo": "o/r", "path": "docs"}).status_code == 200
    assert run.call_args[0][0] == ["github-tree", "o/r", "--path", "docs"]
    from core.connectors import ConnectorError
    with patch("core.connectors.run", side_effect=ConnectorError("HTTP 404: Not Found")):
        r = client.get("/api/github/file", params={"repo": "o/r", "path": "nope.md"})
    assert r.status_code == 502 and "Not Found" in r.json()["detail"]


def test_work_on_a_known_project_queues_the_job(env):
    db, aw, client = env
    with patch("core.connectors.run", return_value={"repos": REPOS}):
        r = client.post("/api/github/work", json={"repo": "example-org/Website",
                                                  "instruction": "Fix the typo on the about page",
                                                  "path": "about/index.html"}).json()
    assert r["project"] == "website" and r["registered"] is False and aw.added == []
    task = db.get_task(r["task_id"])
    assert task["engine"] == "agentwork" and "about/index.html" in task["prompt"]


def test_work_on_a_new_repo_registers_it_first_on_its_default_branch(env):
    _, aw, client = env
    with patch("core.connectors.run", return_value={"repos": REPOS}):
        r = client.post("/api/github/work", json={"repo": "example-user/TVSP", "instruction": "Add a README for it"}).json()
    assert r["registered"] is True and r["project"] == "tvsp"
    assert aw.added == [("tvsp", "https://github.com/example-user/TVSP.git", "example-user/TVSP on GitHub", "master")]
    with patch("core.connectors.run", return_value={"repos": REPOS}):
        bad = client.post("/api/github/work", json={"repo": "someone/else", "instruction": "Do something useful"})
        short = client.post("/api/github/work", json={"repo": "example-user/TVSP", "instruction": "fix"})
    assert bad.status_code == 404 and short.status_code == 400


def test_sharing_a_folder_sends_the_file_and_removes_it(env, tmp_path):
    """2026-10-10, the owner: share a file or folder without copying and pasting the code."""
    _, _, client = env

    def fake_run(args, timeout=60, **kw):
        out = Path(args[args.index("--out") + 1])
        out.write_bytes(b"PK zip bytes")
        assert args[:5] == ["github-download", "o/r", "--path", "core", "--format"] and "--out" in args
        return {"name": "core.zip", "files": 12, "skipped": ["core/big.bin (too big)"], "bytes": 12, "out": str(out)}
    with patch("core.connectors.run", side_effect=fake_run):
        r = client.get("/api/github/download", params={"repo": "o/r", "path": "core", "format": "zip"})
    assert r.status_code == 200 and r.content == b"PK zip bytes"
    assert r.headers["content-type"] == "application/zip" and 'filename="core.zip"' in r.headers["content-disposition"]
    assert r.headers["x-left-out"] == "1" and r.headers["x-files"] == "12"
    assert list((tmp_path / "tmp" / "share").iterdir()) == []          # removed once sent
    assert client.get("/api/github/download", params={"repo": "o/r", "format": "exe"}).status_code == 400
