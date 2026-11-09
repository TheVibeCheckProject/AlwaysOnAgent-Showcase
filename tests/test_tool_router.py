"""
Tests for the lightweight background tool router
=================================================
Validates that:
- Core tools (memory search, tasks, logs, ask_user) ALWAYS go with a reply, whatever the router says
  (2026-10-14: with zero tools the chat invented a log check for task-196).
- Requests needing more attach the router's groups on top of core, plus more_tools.
- Router model failure or disable flag falls back gracefully to core + regex groups.
- The router sees the assistant's previous reply, so follow-ups route correctly.
- MemoryManager.respond always uses chat_with_tools when tools exist, and tells the model when there are none.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.environ.setdefault("CONNECTORS_DIR", str(Path(tempfile.gettempdir()) / "no_connectors_in_tests"))

from core.assistant_tools import ROUTER_SYSTEM_PROMPT, TOOL_GROUPS, AssistantTools
from core.database import DatabaseManager
from core.memory import NO_TOOLS_NOTE, MemoryManager


class MockRouterLLM:
    """Mock LLM with complete() for routing and chat/chat_with_tools for responses."""

    def __init__(self, route_groups: list[str] | None = None, complete_error: bool = False):
        self.route_groups = route_groups if route_groups is not None else []
        self.complete_error = complete_error
        self.complete_calls = []
        self.chat_calls = []
        self.chat_with_tools_calls = []

    def complete(self, messages, max_tokens=60, temperature=0.0, json_mode=True, timeout=5, models=None):
        self.complete_calls.append(messages)
        if self.complete_error:
            raise RuntimeError("Router LLM timeout or API error")
        return json.dumps({"groups": self.route_groups}), "test:mock-router"

    def chat(self, messages, max_tokens=900, temperature=0.4, **kwargs):
        self.chat_calls.append(messages)
        return "Casual chat response"

    def chat_with_tools(self, messages, specs, call_tool, temperature=0.4):
        self.chat_with_tools_calls.append((messages, specs))
        return "Tool chat response", []


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "router_test.db")


def names(specs):
    return [s["function"]["name"] for s in specs]


def test_casual_chat_still_gets_the_core_tools(db, tmp_path):
    llm = MockRouterLLM(route_groups=[])
    tools = AssistantTools(db, None, tmp_path, user_text="good morning!")
    specs = tools.tool_specs(llm=llm, use_router=True)

    tool_names = names(specs)
    assert set(TOOL_GROUPS["core"]) <= set(tool_names)
    assert "more_tools" in tool_names
    assert tools.groups == []
    assert len(llm.complete_calls) == 1


def test_router_groups_are_added_on_top_of_core(db, tmp_path):
    llm = MockRouterLLM(route_groups=["web"])
    tools = AssistantTools(db, None, tmp_path, user_text="what is the weather in Dallas?")
    specs = tools.tool_specs(llm=llm, use_router=True)

    tool_names = names(specs)
    assert set(TOOL_GROUPS["web"]) <= set(tool_names)
    assert set(TOOL_GROUPS["core"]) <= set(tool_names)
    assert "more_tools" in tool_names
    assert tools.groups == ["web"]


def test_router_cannot_pick_core_or_memory_check(db, tmp_path):
    llm = MockRouterLLM(route_groups=["core", "memory_check", "mail"])
    tools = AssistantTools(db, None, tmp_path, user_text="any new mail?")
    specs = tools.tool_specs(llm=llm, use_router=True)

    assert tools.groups == ["mail"]
    assert tools.picked_by == "router"
    tool_names = names(specs)
    assert len(tool_names) == len(set(tool_names))          # core isn't listed twice
    assert "memory_check" not in tool_names                 # only while the weekly check waits


def test_router_naming_an_unknown_group_hands_over_to_the_keyword_picker(db, tmp_path):
    # "email" isn't a group: dropping it would leave the reply with core only
    llm = MockRouterLLM(route_groups=["email"])
    tools = AssistantTools(db, None, tmp_path, user_text="any new mail from Zoho?")
    specs = tools.tool_specs(llm=llm, use_router=True)

    assert tools.picked_by == "keywords"
    assert "mail" in tools.groups
    assert set(TOOL_GROUPS["mail"]) <= set(names(specs))


def test_router_uses_its_own_models_not_the_chats_first():
    from core.assistant_tools import ROUTER_MODELS
    from core.llm import CloudLLM
    assert CloudLLM.CHAT_MODELS[0] not in ROUTER_MODELS


def test_router_prompt_lists_every_pickable_group():
    # files and computer were missing at first, so the router could never pick them
    pickable = set(TOOL_GROUPS) - {"core", "memory_check"}
    for group in pickable:
        assert f"- {group}:" in ROUTER_SYSTEM_PROMPT, group


def test_router_failure_falls_back_to_baseline(db, tmp_path):
    llm = MockRouterLLM(complete_error=True)
    tools = AssistantTools(db, None, tmp_path, user_text="what's the weather?")
    specs = tools.tool_specs(llm=llm, use_router=True)

    tool_names = names(specs)
    # On fallback, core tools + regex-matched groups are returned
    assert set(TOOL_GROUPS["core"]) <= set(tool_names)
    assert "get_weather" in tool_names
    assert "more_tools" in tool_names


def test_router_disabled_by_env_var(db, tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_TOOL_ROUTER", "0")
    llm = MockRouterLLM(route_groups=["web"])
    tools = AssistantTools(db, None, tmp_path, user_text="what's the weather?")
    specs = tools.tool_specs(llm=llm, use_router=True)

    assert len(llm.complete_calls) == 0
    tool_names = names(specs)
    assert set(TOOL_GROUPS["core"]) <= set(tool_names)


def _seed_assistant_message(db, content):
    conn = db.get_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS mem_messages (id INTEGER PRIMARY KEY, conv TEXT, role TEXT, "
                 "content TEXT, created_at TEXT, tokens INTEGER)")
    conn.execute("INSERT INTO mem_messages (conv, role, content, created_at, tokens) VALUES "
                 "('c', 'assistant', ?, 'now', 10)", (content,))
    conn.commit()


def test_affirmation_inspects_last_assistant_message(db, tmp_path):
    _seed_assistant_message(db, "Would you like me to check the weather in Tokyo?")

    llm = MockRouterLLM(route_groups=["web"])
    tools = AssistantTools(db, None, tmp_path, user_text="yes please")
    tools.tool_specs(llm=llm, use_router=True)

    assert len(llm.complete_calls) == 1
    sent_prompt = llm.complete_calls[0][1]["content"]
    assert "Assistant's previous reply: Would you like me to check the weather in Tokyo?" in sent_prompt
    assert "User's message: yes please" in sent_prompt


def test_a_follow_up_keeps_the_task_tools_task_196(db, tmp_path):
    """2026-10-14, live: "oh I tapped it but it failed" got zero tools; the chat said "let me check the logs on
    task-196", then invented why the merge failed. The router must see the previous reply, and whatever it
    answers, the task and log tools stay."""
    _seed_assistant_message(db, "Calling offer_go_live brings up the task card with the go-live chip. Tap it.")

    llm = MockRouterLLM(route_groups=[])                   # the router's worst case: "no extra tools"
    tools = AssistantTools(db, None, tmp_path, user_text="oh I tapped it but it failed.")
    specs = tools.tool_specs(llm=llm, use_router=True)

    sent_prompt = llm.complete_calls[0][1]["content"]
    assert "go-live chip" in sent_prompt                   # not an affirmation, but it still gets the context
    tool_names = names(specs)
    for needed in ("get_task", "list_tasks", "read_logs", "search_memory"):
        assert needed in tool_names


def test_memory_check_is_offered_even_when_the_router_picks_nothing(db, tmp_path):
    class PendingReview:
        def pending(self):
            return True

    llm = MockRouterLLM(route_groups=[])
    tools = AssistantTools(db, None, tmp_path, user_text="keep both of those")
    tools._memory_review = lambda: PendingReview()
    assert "memory_check" in names(tools.tool_specs(llm=llm, use_router=True))


def test_memory_respond_sends_core_tools_for_casual_chat(db, tmp_path):
    llm = MockRouterLLM(route_groups=[])
    mem = MemoryManager(db, llm=llm)
    tools = AssistantTools(db, mem, tmp_path)

    reply = mem.respond("just saying hi!", tools=tools)
    assert reply == "Tool chat response"
    assert len(llm.chat_calls) == 0
    assert len(llm.chat_with_tools_calls) == 1
    assert "search_memory" in names(llm.chat_with_tools_calls[0][1])


def test_memory_respond_uses_chat_with_tools_when_tools_needed(db, tmp_path):
    llm = MockRouterLLM(route_groups=["web"])
    mem = MemoryManager(db, llm=llm)
    tools = AssistantTools(db, mem, tmp_path)

    reply = mem.respond("what's the weather in Dallas?", tools=tools)
    assert reply == "Tool chat response"
    assert len(llm.chat_calls) == 0
    assert len(llm.chat_with_tools_calls) == 1


def _routing_log(tmp_path):
    return [json.loads(line) for line in (tmp_path / "tool_routing.jsonl").read_text().splitlines()]


def test_the_routing_log_says_who_picked_the_groups(db, tmp_path):
    """scripts/router_report.py tells router picks from keyword fallbacks by this field."""
    mem = MemoryManager(db, llm=MockRouterLLM(route_groups=["web"]))
    mem.respond("what's the weather in Dallas?", tools=AssistantTools(db, mem, tmp_path))
    mem.llm = MockRouterLLM(complete_error=True)
    mem.respond("what's the weather in Tokyo?", tools=AssistantTools(db, mem, tmp_path))

    assert [row["picker"] for row in _routing_log(tmp_path)] == ["router", "keywords"]


def test_a_reply_without_tools_is_told_it_has_none(db, tmp_path):
    """If a reply ever gets no tool list, the model must hear that it can't look anything up."""
    class NoTools:
        user_text = ""
        groups: list[str] = []

        def tool_specs(self, llm=None, use_router=False):
            return []

        def call(self, name, args):
            return ""

    llm = MockRouterLLM()
    mem = MemoryManager(db, llm=llm)
    reply = mem.respond("did the merge work?", tools=NoTools())
    assert reply == "Casual chat response"
    assert llm.chat_calls[0][0]["content"].endswith(NO_TOOLS_NOTE)
