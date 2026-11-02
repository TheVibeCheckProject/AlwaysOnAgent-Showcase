"""Procedural memory: learned recipes, their fail-safes and where they're used (core/procedures.py, IDEAS #25)."""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.test_memory import FakeEmbedder, FakeLLM, TEST_DIR, make_memory  # noqa: E402
from core import procedures as procedures_mod  # noqa: E402


class LessonLLM(FakeLLM):
    """Also answers the distil prompt with a scripted lesson (None = 'nothing to learn')."""

    def __init__(self):
        super().__init__()
        self.lesson = {"when": "searching the web for opening hours", "do": "include the town name in the query"}

    def chat(self, messages, max_tokens=800, temperature=0.6, json_mode=False, timeout=30, **kw):
        if messages[0]["content"].startswith("You help a personal assistant learn"):
            self.calls.append(messages)
            return json.dumps({"lesson": self.lesson})
        return super().chat(messages, max_tokens, temperature, json_mode, timeout)


RECOVERY = [
    {"name": "web_research", "arguments": '{"query": "opening hours"}', "result": "Error: too vague, add a place."},
    {"name": "web_research", "arguments": '{"query": "Springfield library opening hours"}', "result": "Open 9-5."},
]


def test_owner_taught_recipes_are_saved_used_and_never_retired():
    llm = LessonLLM()
    mem = make_memory("proc_owner", llm=llm)
    llm.extract_ops = {"add": [], "update": [], "delete": [], "procedure": [
        {"area": "TikTok videos", "when": "writing the TikTok caption", "do": "keep it under ten words",
         "evidence": "keep the caption under ten words"},
        {"area": "x", "when": "anything", "do": "invented by the model", "evidence": "never said this"}]}
    mem.learn_from_exchange("From now on keep the caption under ten words when you post", "Will do.", msg_id=7)
    rows = mem.procedures.list()
    assert len(rows) == 1, rows                                     # the ungrounded one is dropped
    p = rows[0]
    assert p["area"] == "tiktok videos" and p["source"] == "owner" and p["source_ref"] == "7"

    ctx = mem.build_context("writing the tiktok caption now")[-1]["content"]
    assert "How you've done this before" in ctx and "keep it under ten words" in ctx
    for _ in range(5):
        mem.procedures.record_outcome([p], {p["id"]: False})
    assert mem.procedures.get(p["id"])["status"] == "active"        # the owner's own: never retired by itself


def test_a_tool_that_failed_then_worked_teaches_a_checked_recipe():
    llm = LessonLLM()
    mem = make_memory("proc_tool", llm=llm)
    assert [t for t, _ in mem.procedures.recoveries(RECOVERY)] == ["web_research"]
    assert mem.procedures.recoveries([RECOVERY[1]]) == []            # nothing failed: nothing to learn
    timeout = [{"name": "get_weather", "arguments": '{"location": "Springfield"}', "result": "Error running get_weather: timed out"},
               {"name": "get_weather", "arguments": '{"location":"Springfield"}', "result": "72F, clear."}]
    assert mem.procedures.recoveries(timeout) == []                  # the same call just worked again: no lesson

    llm.verdict = False                                              # the second opinion isn't convinced
    assert mem.procedures.learn_from_trace("when is the library open?", RECOVERY) == 0
    assert mem.procedures.list() == []
    llm.verdict = True
    assert mem.procedures.learn_from_trace("when is the library open?", RECOVERY, msg_id=3) == 1
    p = mem.procedures.list()[0]
    assert p["area"] == "tool:web_research" and p["source"] == "tool" and "Springfield library" in p["evidence"]
    verify = [c for c in llm.calls if c[0]["content"].startswith("You double-check")][-1][1]["content"]
    assert "include the town name" in verify and "too vague" in verify

    llm.lesson = None                                                # "a one-off: nothing to learn"
    assert mem.procedures.learn_from_trace("again", RECOVERY) == 0


def test_outcomes_are_scored_and_failing_learned_recipes_retire():
    mem = make_memory("proc_score", llm=LessonLLM())
    pid, _ = mem.procedures.add("tool:web_research", "searching for hours", "include the town name", "tool")
    p = mem.procedures.get(pid)
    ok_trace = [{"name": "web_research", "result": "Open 9-5."}]
    bad_trace = [{"name": "web_research", "result": "Error: nothing found"}]
    assert mem.procedures.chat_outcomes([p], ok_trace) == {pid: True}
    assert mem.procedures.chat_outcomes([p], bad_trace) == {pid: False}
    assert mem.procedures.chat_outcomes([p], [{"name": "get_weather", "result": "Sunny"}]) == {pid: None}
    mem.procedures.record_outcome([p], {pid: True})
    for _ in range(2):
        mem.procedures.record_outcome([p], {pid: False})
    assert mem.procedures.get(pid)["status"] == "active"             # 2 fails, 1 help: still in use
    mem.procedures.record_outcome([p], {pid: False})
    r = mem.procedures.get(pid)
    assert r["status"] == "retired" and (r["uses"], r["helped"], r["failed"]) == (4, 1, 3)
    assert mem.procedures.relevant("search for hours", areas=["tool:web_research"]) == []
    pinned, _ = mem.procedures.add("tool:web_research", "searching prices", "say the currency", "tool", pinned=True)
    for _ in range(5):
        mem.procedures.record_outcome([mem.procedures.get(pinned)], {pinned: False})
    assert mem.procedures.get(pinned)["status"] == "active"           # pinned: never retired


def test_only_the_best_few_go_in_and_duplicates_update():
    llm = LessonLLM()
    mem = make_memory("proc_rank", llm=llm)
    for i in range(8):
        mem.procedures.add("project:website", f"changing page {i} of the website", f"run the build after page {i}", "seed")
    mem.procedures.add("weather", "the owner mentions fishing", "use the cabin's forecast", "owner")
    got = mem.procedures.relevant("fix a typo on the website", areas=["project:website"])
    assert 0 < len(got) <= procedures_mod.MAX_PER_PROMPT and all(r["area"] == "project:website" for r in got)
    assert len(mem.procedures.relevant("x", areas=["project:website"], budget=20)) == 1     # budget caps it
    # The same recipe again: updates it (the owner's newer word wins) instead of a second copy
    n = len(mem.procedures.list())
    pid, created = mem.procedures.add("weather", "the owner mentions fishing", "use the cabin's forecast, Pine Lake",
                                      "owner")
    assert not created and len(mem.procedures.list()) == n and "Pine Lake" in mem.procedures.get(pid)["steps"]


def test_forget_reaches_recipes():
    llm = LessonLLM()
    mem = make_memory("proc_forget", llm=llm)
    keep, _ = mem.procedures.add("email", "sorting email", "archive newsletters", "owner")
    gone, _ = mem.procedures.add("weather", "the owner mentions the cabin", "use the cabin forecast", "owner")
    llm.verdict = True
    mem.procedures.relevant = lambda q, **kw: [mem.procedures.get(gone)]   # what search would find for it
    out = mem.forget_everywhere("anything about the cabin")
    assert out["procedures"] == 1 and mem.procedures.get(gone) is None and mem.procedures.get(keep)


def test_tasks_get_their_recipes_and_teach_new_ones(monkeypatch):
    from core.daemon import AutonomousDaemon
    ws = TEST_DIR / "proc_daemon"
    daemon = AutonomousDaemon(workspace_dir=ws, db_file=str(ws / "state.db"), log_file=str(ws / "worker.log"))
    try:
        daemon.memory.embedder = FakeEmbedder()
        llm = LessonLLM()
        daemon.memory.llm = llm
        procs = daemon.memory.procedures
        pid, _ = procs.add("project:website", "changing the website", "run npm ci before the build", "seed")
        tid = daemon.db.create_task(title="[website] fix footer", prompt="Fix the footer", engine="agentwork",
                                    metadata={"project": "website"})
        task = daemon.db.get_task(tid)
        briefed = daemon._with_memory_briefing(task)
        assert briefed["prompt"].startswith("Fix the footer") and "run npm ci before the build" in briefed["prompt"]
        daemon._learn_from_task(task, 0, None, "done")
        assert procs.get(pid)["helped"] == 1 and not daemon._task_recipes

        # Succeeded only on the retry: a recipe is learned from the earlier failure (checked by the second opinion)
        llm.lesson = {"when": "the website build fails on missing packages", "do": "run npm ci first"}
        retried = {**task, "retry_count": 1}
        daemon._learn_from_task(retried, 0, {"output_summary": "npm ERR! missing script: build"}, "build passed")
        daemon.memory.wait_idle()
        learned = [p for p in procs.list() if p["source"] == "task"]
        assert len(learned) == 1 and learned[0]["area"] == "project:website" and "npm ERR" in learned[0]["evidence"]
    finally:
        daemon.executor.shutdown(wait=False)
        for h in list(daemon.logger.handlers):
            h.close()
            daemon.logger.removeHandler(h)


def test_a_lesson_that_copies_its_job_is_refused():
    """The owner (2026-10-11): "a task isn't copied step by step... I don't want a repeat Notepad call wanting to
    read a grocery list". Know-how passes; the job's own file, text or a script of its steps doesn't."""
    happened = ('A background task (engine:computer): "Open Groceries.txt on the Desktop and tell me the items"\n'
                'The earlier attempt failed: Notepad opened behind\nThe retry succeeded: "1. Milk 2. Eggs 3. Bread"')
    copy = procedures_mod.copy_problem
    assert "file" in copy({"when": "reading the grocery list", "do": "open Groceries.txt in Notepad"}, happened)
    assert "text" in copy({"when": "a list", "do": 'answer "1. Milk 2. Eggs 3. Bread"'}, happened)
    assert "script" in copy({"when": "reading a file", "do": "open Notepad, then press ctrl+o, then type the path, "
                                                              "then press Enter, then read it"}, happened)
    assert copy({"when": "reading a text file", "do": "open it by its path with open_file; no need to start "
                                                      "Notepad first"}, happened) is None


def test_a_copied_lesson_never_reaches_the_second_opinion(monkeypatch):
    mem = make_memory("proc_copy")
    asked = []
    monkeypatch.setattr(mem, "_bg_chat", lambda *a, **k: json.dumps(
        {"lesson": {"when": "reading the grocery list", "do": "open Groceries.txt in Notepad"}}))
    monkeypatch.setattr(mem, "second_opinion", lambda q: asked.append(q) or (True, "ok"))
    task = {"id": "task-1", "engine": "computer", "title": "Read Groceries.txt",
            "prompt": "Open Groceries.txt on the Desktop and tell me the items"}
    assert mem.procedures.learn_from_task(task, "Notepad opened behind", "Milk, Eggs, Bread") is False
    assert asked == [] and mem.procedures.list() == []
