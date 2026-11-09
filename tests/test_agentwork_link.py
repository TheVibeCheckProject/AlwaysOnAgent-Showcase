"""
Tests for the agent's connection to AgentWork, the separate project tool (C:\\Projects\\AgentWork):
listing its projects, handing it a job (the instruction on stdin), quota refusals, the dispatcher's
routing and the chat's
project proposals. A fake agentwork.py stands in for the tool; nothing outside a temporary folder is touched.

Run:  python tests/test_agentwork_link.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos (it would render and send real videos): point the agent at a
# folder where it isn't installed, so an accidental call fails harmlessly
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

from core.assistant_tools import AssistantTools
from core.daemon import AutonomousDaemon, EngineDispatcher
from core.database import DatabaseManager
from core.memory import MemoryManager
from core.task_engines import QUOTA_EXIT_CODE, AgentWorkEngine
from tests.test_memory import FakeEmbedder, FakeLLM

TEST_DIR = Path(tempfile.mkdtemp(prefix="agentwork_link_test_"))
HOME = TEST_DIR / "AgentWork"
HOME.mkdir()
(HOME / "agentwork.py").write_text('''import json, sys
args = sys.argv[1:]
if args[0] == "list":
    print(json.dumps([{"name": "site", "description": "Test website", "repo": "https://example.com/site.git",
                       "base_branch": "main", "engine": "antigravity", "model": "claude-sonnet-4-6"}]))
    sys.exit(0)
if args[0] == "add":
    if args[1] == "unreachable":
        print("[agentwork] couldn't reach the repo", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"name": args[1], "repo": args[3], "description": args[5], "base_branch": "main",
                      "engine": "antigravity", "model": "claude-sonnet-4-6"}))
    sys.exit(0)
instruction = sys.stdin.read()
print("RUN " + " ".join(args[1:]))
print("INSTRUCTION " + instruction)
if "quota" in instruction:
    print("error: Individual quota reached. Resets in 2h1m0s.")
    sys.exit(76)
print("[Changes on branch agent/" + args[2] + ", not pushed]")
''', encoding="utf-8")


def test_engine_lists_projects_and_runs_a_job():
    engine = AgentWorkEngine(HOME)
    assert [p["name"] for p in engine.list_projects()] == ["site"]
    assert AgentWorkEngine(TEST_DIR / "missing").list_projects() == []

    code, out = engine.execute_task({"id": "task-5", "prompt": "Add a calendar page", "timeout_seconds": 60,
                                     "metadata": {"project": "site"}})
    assert code == 0 and "RUN site task-5 --timeout 60" in out and "INSTRUCTION Add a calendar page" in out, out
    code, out = engine.execute_task({"id": "task-6", "prompt": "use up the quota", "metadata": {"project": "site"}})
    assert code == QUOTA_EXIT_CODE and "resets in 2h1m0s" in out, out
    assert engine.execute_task({"id": "task-7", "prompt": "x"})[0] == 2              # no project
    print("  - the engine lists AgentWork's projects and hands it jobs (instruction on stdin, quota passed on)")


def test_dispatcher_routes_to_agentwork_with_what_he_has_said():
    ws = TEST_DIR / "dispatch"
    ws.mkdir()
    dispatcher = EngineDispatcher(ws, task_root=ws / "workspaces", agent_work_dir=HOME)
    code, out, used, fallback = dispatcher.execute({"id": "task-8", "prompt": "Add a calendar page", "engine": "agentwork",
                                                    "timeout_seconds": 60, "metadata": {"project": "site"}})
    assert (code, used, fallback) == (0, "agentwork", False) and "RUN site task-8" in out, out
    assert not (ws / "workspaces" / "task-8").exists()           # AgentWork makes its own folder

    from core.procedures import Procedures
    recipes = []
    procs = SimpleNamespace(relevant=lambda prompt, areas=(): list(recipes), block=Procedures.block)
    fake = SimpleNamespace(memory=SimpleNamespace(task_context=lambda prompt: "[What memory knows]", procedures=procs),
                           logger=None, _task_recipes={})
    task = {"id": "t", "prompt": "Add a calendar page", "engine": "agentwork"}
    # The owner's instruction first, then (2026-11-03: "a personal assistant for me and no one else") what he's said
    # that relates to it, and what earlier jobs on the project taught (IDEAS #25)
    first = AutonomousDaemon._with_memory_briefing(fake, task)["prompt"]
    assert first.startswith("Add a calendar page") and "[What memory knows]" in first
    assert "[What memory knows]" in AutonomousDaemon._with_memory_briefing(fake, {**task, "engine": "cloud"})["prompt"]
    recipes.append({"id": 1, "situation": "changing the site", "steps": "run npm ci before the build"})
    briefed = AutonomousDaemon._with_memory_briefing(fake, task)["prompt"]
    assert briefed.startswith("Add a calendar page") and "run npm ci" in briefed and "[What memory knows]" in briefed
    nothing = SimpleNamespace(memory=SimpleNamespace(task_context=lambda prompt: "", procedures=SimpleNamespace(
        relevant=lambda prompt, areas=(): [], block=Procedures.block)), logger=None, _task_recipes={})
    assert AutonomousDaemon._with_memory_briefing(nothing, task)["prompt"] == "Add a calendar page"
    print("  - the dispatcher routes 'agentwork' tasks to the tool: the instruction, lessons and what he's said")


def test_chat_proposals_use_agentwork_projects():
    db = DatabaseManager(TEST_DIR / "proposals.db")
    tools = AssistantTools(db, MemoryManager(db, llm=FakeLLM(), embedder=FakeEmbedder()), TEST_DIR,
                           agentwork=AgentWorkEngine(HOME))
    spec = next(s for s in tools.tool_specs() if s["function"]["name"] == "propose_task")
    assert spec["function"]["parameters"]["properties"]["project"]["enum"] == ["site"]
    assert "project" not in next(s for s in AssistantTools.specs() if s["function"]["name"] == "propose_task")[
        "function"]["parameters"]["properties"]
    assert "no AgentWork project called 'nope'" in tools.propose_task("Add a calendar page", project="nope")
    tools.propose_task("Add a calendar page", project="site")
    assert tools.proposals[0]["project"] == "site"
    print("  - chat can propose an AgentWork job on a known project (DeskAI's Run button is in test_deskai_phase2)")


def test_engine_adds_projects_through_the_tool():
    engine = AgentWorkEngine(HOME)
    project = engine.add_project("recipes", "https://github.com/me/recipes.git", "Recipe app")
    assert project["name"] == "recipes" and project["repo"] == "https://github.com/me/recipes.git"
    try:
        engine.add_project("unreachable", "https://github.com/me/x.git", "Nope")
        raise AssertionError("a refusal was not raised")
    except ValueError as e:
        assert "couldn't reach" in str(e) and "[agentwork]" not in str(e)
    print("  - the engine registers a repo through AgentWork's add command and passes on its refusals")


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_") and callable(fn):
                print(f"[{name}]")
                fn()
        print("\nALL AGENTWORK LINK TESTS PASSED")
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)
