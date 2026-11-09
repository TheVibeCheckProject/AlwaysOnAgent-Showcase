"""The background helper (core/follow_up.py) and the promise guard (core/memory.py keep_promise).

2026-10-26: the chat said "I'll pull those files for you right now. One moment!" and "Hang tight", and nothing ran: a
reply ends the turn. Now a promise either comes with look_into or starts it."""
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import follow_up
from core.assistant_tools import TOOL_GROUPS, AssistantTools
from core.database import DatabaseManager
from core.memory import PROMISE_FAILED_NOTE, PROMISE_STARTED_NOTE, keep_promise

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_gap_from_other_tests():
    """Each test starts as if no helper ran recently (MIN_GAP_SECONDS counts from the last run's end)."""
    follow_up._last_end = 0.0
    yield
    follow_up._last_end = 0.0


# The real replies from 2026-10-26/27 that promised work and did none
BROKEN_PROMISES = [
    ("They should be in the temporary computer-use capture directory. I'll pull those files for you right now so you "
    "can see them. One moment!"),
    ("Let me fix that right now and pull any captured files from the container so you can check them out. Hang tight "
    "a second."),
    "Let's do a quick search in the background to locate exactly where the computer task saved those screenshot files.",
]
# Ordinary replies that must not start anything
FINE = [
    "Take your time. I'll just hang out here quietly—let me know whenever you're ready.",
    "Understood, I won't do anything yet. I'll just wait here until you're ready.",
    "Once you save it and restart your agent, your video gallery will be live. Let me know if you run into any snags!",
    "I checked task-233: it finished at 21:25 and saved 18 screenshots.",
    # 2026-10-27/31: "hang/hold tight" meaning wait, which started the helper on nothing
    ("Since you're locked out of your session for a couple of hours, do you want me to keep an eye on anything in "
    "the background, or just hang tight until you're ready to jump back in?"),
    "Got it, still waiting on that update from the contractor then. I'll hold tight on that.",
]


def test_hang_tight_while_working_is_still_a_promise():
    for reply in ("Hang tight while I pull those files.", "Hold tight, I'm checking the logs."):
        asked: list[str] = []
        keep_promise(reply, [], lambda q, asked=asked: asked.append(q) or "Started: ...")
        assert asked == [reply], reply


def test_a_broken_promise_starts_the_helper():
    for reply in BROKEN_PROMISES:
        asked: list[str] = []
        out = keep_promise(reply, [], lambda q, asked=asked: asked.append(q) or "Started: ...")
        assert asked == [reply], reply
        assert out.endswith("(" + PROMISE_STARTED_NOTE + ")")


def test_ordinary_replies_are_left_alone():
    for reply in FINE:
        out = keep_promise(reply, [], lambda q, reply=reply: (_ for _ in ()).throw(AssertionError(f"started on: {reply}")))
        assert out == reply


def test_a_promise_already_kept_is_left_alone():
    trace = [{"name": "look_into", "arguments": "{}", "result": "Started: ..."}]
    assert keep_promise(BROKEN_PROMISES[0], trace, None) == BROKEN_PROMISES[0]


def test_a_watch_or_schedule_keeps_its_own_promise():
    """'I'll check every 5 minutes and let you know' is the watch talking, not a broken promise."""
    for tool, reply in (("watch_mail", "Done. I'll check every 5 minutes and let you know when Sam replies."),
                        ("create_schedule", "Set. I'll check the weather each morning at 7 and let you know.")):
        trace = [{"name": tool, "arguments": "{}", "result": "ok"}]
        assert keep_promise(reply, trace, lambda q, tool=tool: (_ for _ in ()).throw(AssertionError(tool))) == reply


def test_a_failed_start_says_nothing_is_running():
    failed = [{"name": "look_into", "arguments": "{}", "result": "Error: busy"}]
    out = keep_promise(BROKEN_PROMISES[1], failed, lambda q: "Error: already looking into 2 things")
    assert out.endswith("(" + PROMISE_FAILED_NOTE + ")")


def test_look_into_is_a_core_tool_with_a_description():
    assert "look_into" in TOOL_GROUPS["core"]
    assert "look_into" in {s["function"]["name"] for s in AssistantTools.specs()}


class FakeLLM:
    """Calls the tools it's told to, then answers. Records what it was offered."""

    def __init__(self, calls, answer="Found it: 18 screenshots in data/runs/20261026-212256."):
        self.calls, self.answer, self.offered, self.results = calls, answer, None, []

    def chat_with_tools(self, messages, tools, call_tool, **kw):
        self.offered = {t["function"]["name"] for t in tools}
        self.kw = kw
        self.results = [call_tool(name, "{}") for name in self.calls]
        return self.answer, [{"name": n, "arguments": "{}", "result": r} for n, r in zip(self.calls, self.results)]


def _db():
    return DatabaseManager(Path(tempfile.mkdtemp(prefix="follow_up_")) / "f.db")


def test_the_helper_only_looks():
    llm = FakeLLM(["propose_task", "list_tasks"])
    text = follow_up.run("Where did task-233 save its screenshots?", _db(), None, llm, ROOT)
    assert llm.offered and llm.offered <= set(follow_up.READ_ONLY_TOOLS)
    assert "propose_task" not in llm.offered
    assert llm.results[0].startswith("Error: propose_task isn't available")
    assert not llm.results[1].startswith("Error")
    assert llm.kw["max_rounds"] == follow_up.MAX_ROUNDS
    assert text.startswith("Found it")


def test_no_answer_still_tells_the_user():
    text = follow_up.run("Where are the screenshots?", _db(), None, FakeLLM([], answer=None), ROOT)
    assert text.startswith("I couldn't finish looking into this")


def test_an_answer_with_no_lookup_behind_it_is_never_sent():
    # 2026-10-31: no tool, 2 seconds, "the case has no decision posted yet" sent as a finding
    class Lazy(FakeLLM):
        def __init__(self, second_calls):
            super().__init__([], answer="Permit 26-01234: no decision posted yet.")
            self.rounds, self.second_calls = [], second_calls

        def chat_with_tools(self, messages, tools, call_tool, **kw):
            self.rounds.append(messages)
            self.calls = self.second_calls if len(self.rounds) > 1 else []
            return super().chat_with_tools(messages, tools, call_tool, **kw)

    stubborn = Lazy([])
    text = follow_up.run("Has the contractor's start date come through?", _db(), None, stubborn, ROOT)
    assert text == follow_up.NOTHING_CHECKED and len(stubborn.rounds) == 2
    assert stubborn.rounds[1][-1]["content"] == follow_up.LOOK_FIRST          # it was asked once to look first
    looks = Lazy(["list_tasks"])
    assert follow_up.run("Has it come through?", _db(), None, looks, ROOT).startswith("Permit 26-01234")
    failed = Lazy(["propose_task"])                                            # a refused tool isn't a lookup
    assert follow_up.run("Has it come through?", _db(), None, failed, ROOT) == follow_up.NOTHING_CHECKED


def test_start_returns_at_once_and_delivers_later():
    got, done = [], threading.Event()
    release = threading.Event()

    class SlowLLM(FakeLLM):
        def chat_with_tools(self, *a, **kw):
            release.wait(5)
            return super().chat_with_tools(*a, **kw)

    msg = follow_up.start("Where are the screenshots?", _db(), None, SlowLLM(["list_tasks"]), ROOT,
                          deliver=lambda t: (got.append(t), done.set()), context="")
    assert msg.startswith("Started") and not got          # the chat didn't wait for it
    release.set()
    assert done.wait(5) and got[0].startswith("Found it")
    for _ in range(50):
        if follow_up.running() == 0:
            break
        threading.Event().wait(0.02)
    assert follow_up.running() == 0


def test_one_at_a_time_and_a_gap_between_runs():
    """2026-10-27, the first afternoon live: 4 runs in 10 minutes, 23 AI calls."""
    release = threading.Event()
    finished = threading.Semaphore(0)

    class Blocked(FakeLLM):
        def chat_with_tools(self, *a, **kw):
            release.wait(5)
            return super().chat_with_tools(*a, **kw)

    db = _db()
    started = [follow_up.start(f"q{i}", db, None, Blocked([]), ROOT, deliver=lambda t: finished.release(), context="")
               for i in range(follow_up.MAX_RUNNING + 1)]
    release.set()
    assert all(s.startswith("Started") for s in started[:follow_up.MAX_RUNNING])
    assert started[-1].startswith("Error: the background helper is already looking into")
    for _ in range(follow_up.MAX_RUNNING):
        assert finished.acquire(timeout=5)
    for _ in range(50):
        if follow_up.running() == 0:
            break
        threading.Event().wait(0.02)
    again = follow_up.start("q again", db, None, FakeLLM([]), ROOT, deliver=lambda t: None, context="")
    assert again.startswith("Error: the background helper just finished")


def test_the_chat_tool_records_what_it_handed_off(monkeypatch):
    monkeypatch.setattr(follow_up, "start", lambda q, *a, **k: "Started: ...")
    tools = AssistantTools(_db(), None, ROOT)
    assert tools.call("look_into", '{"question": "Where are the screenshots?"}').startswith("Started")
    assert tools.follow_ups == ["Where are the screenshots?"]
