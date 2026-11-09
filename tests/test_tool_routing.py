"""
Tool groups: each chat reply gets the tools its conversation is about, not all of them
======================================================================================
All 30 tool descriptions were ~4,000 tokens on every call, which pushed chats past free-tier request limits
(Groq's 413). A reply now gets "core" plus the matching groups, and more_tools loads another group mid-reply.
Routing uses both regex keywords and local semantic embeddings with telemetry logged to tool_routing.jsonl.
"""

import io
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

from core import llm as llm_module
from core.assistant_tools import TOOL_GROUPS, AssistantTools
from core.database import DatabaseManager
from core.memory import MemoryManager


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "state.db")


def names(specs):
    return [s["function"]["name"] for s in specs]


def test_every_tool_belongs_to_a_group():
    every = set(names(AssistantTools.specs([])))
    grouped = {n for tools in TOOL_GROUPS.values() for n in tools}
    assert every - grouped == set() and grouped - every == set()


@pytest.mark.parametrize("message,group", [
    ("what are my most important emails this week?", "mail"),
    ("make a new video and post it to tiktok", "videos"),
    ("remind me tomorrow at 9am to call mom", "schedules"),
    ("how are we doing, send me the numbers", "stats"),
    ("fix the bug in the website footer", "projects"),
    ("read the document [attached: a.pdf (id att-1)]", "files"),
    ("what's the weather like?", "web"),
])
def test_a_message_gets_its_group_and_the_core(db, tmp_path, message, group):
    tools = AssistantTools(db, None, tmp_path, user_text=message)
    got = names(tools.tool_specs())
    assert group in tools.groups
    assert set(TOOL_GROUPS[group]) <= set(got) and set(TOOL_GROUPS["core"]) <= set(got)
    assert got[-1] == "more_tools"
    assert len(json.dumps(tools.tool_specs())) < len(json.dumps(AssistantTools.specs([]))) * 0.7


def test_small_talk_gets_core_tools_only(db, tmp_path):
    tools = AssistantTools(db, None, tmp_path, user_text="how are you?")
    got = names(tools.tool_specs())
    assert tools.groups == [] and set(got) == set(TOOL_GROUPS["core"]) | {"more_tools"}
    assert tools.guides() == ""                       # no group, no how-to notes (2026-10-27)


def test_a_groups_how_to_goes_only_with_its_tools(db, tmp_path):
    from core.assistant_tools import GROUP_GUIDES
    tools = AssistantTools(db, None, tmp_path, user_text="what are my most important emails this week?")
    tools.tool_specs()
    notes = tools.guides()
    assert "mail" in tools.groups and GROUP_GUIDES["mail"] in notes
    assert GROUP_GUIDES["canvas"] not in notes
    # Loading a group mid-reply hands over its how-to with the tools
    assert GROUP_GUIDES["canvas"] in tools.more_tools("canvas")


def test_a_tool_in_two_loaded_groups_is_sent_once(db, tmp_path):
    """Gemini rejects a request that declares a function twice (Oct 11: contacts_search; Oct 27: connect_google)."""
    tools = AssistantTools(db, None, tmp_path, user_text="hi")
    tools.tool_specs()
    tools.groups = ["mail", "calendar", "drive", "phone"]
    tools._specs = None
    tools.pick_groups = lambda: ["mail", "calendar", "drive", "phone"]
    got = names(tools.tool_specs())
    assert len(got) == len(set(got)) and "connect_google" in got and "contacts_search" in got


def test_more_tools_is_short(db, tmp_path):
    """It goes with every call; its long group descriptions made it the biggest tool (450 tokens) until 2026-10-27."""
    from core.memory import count_tokens
    tools = AssistantTools(db, None, tmp_path, user_text="hi")
    tools.tool_specs()
    assert count_tokens(json.dumps(tools._more_tools_spec())) <= 260


def test_a_follow_up_keeps_the_group_of_the_exchange_before_it(db, tmp_path):
    conn = db.get_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS mem_messages (id INTEGER PRIMARY KEY, conv TEXT, role TEXT, "
                 "content TEXT, created_at TEXT, tokens INTEGER)")
    conn.execute("INSERT INTO mem_messages (conv, role, content, created_at, tokens) VALUES "
                 "('c', 'assistant', 'Want me to post it to TikTok?', 'now', 5)")
    conn.commit()
    tools = AssistantTools(db, None, tmp_path, user_text="yes do it")
    tools.tool_specs()
    assert "videos" in tools.groups


def test_routing_can_be_switched_off(db, tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_TOOL_ROUTING", "0")
    tools = AssistantTools(db, None, tmp_path, user_text="hi")
    assert len(tools.tool_specs()) == len(AssistantTools.specs([]))


def test_more_tools_reaches_the_model_on_the_next_round(db, tmp_path):
    """The model asks for the mail group, then sees gmail_search in the very next request and uses it."""
    # Vague query that does not initially route to mail
    tools = AssistantTools(db, None, tmp_path, user_text="help me with that property issue")
    specs = tools.tool_specs()
    assert "gmail_search" not in names(specs)
    runner = llm_module.CloudLLM(keys={"groq": "test-key"})
    sent, replies = [], [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "more_tools", "arguments": '{"group": "mail"}'}}]}}]},
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "gmail_search", "arguments": '{"query": "landlord"}'}}]}}]},
        {"choices": [{"message": {"content": "Nothing from your landlord."}}]},
    ]

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        sent.append(json.loads(req.data))
        return Resp(json.dumps(replies[len(sent) - 1]).encode())

    called = []
    orig = llm_module.urllib.request.urlopen
    llm_module.urllib.request.urlopen = fake_urlopen
    try:
        reply, _trace = runner.chat_with_tools([{"role": "user", "content": "help me with that property issue"}], specs,
                                              lambda n, a: called.append(n) or tools.call(n, a))
    finally:
        llm_module.urllib.request.urlopen = orig
    assert reply == "Nothing from your landlord."
    assert "gmail_search" not in names(sent[0]["tools"]) and "gmail_search" in names(sent[1]["tools"])
    assert names(sent[1]["tools"])[-1] == "more_tools" and called == ["more_tools", "gmail_search"]
    assert "mail" in tools.groups


def test_tool_routing_logged_to_jsonl_without_message_text(db, tmp_path):
    class FakeLLM:
        def chat_with_tools(self, messages, specs, call_fn, temperature=0.4):
            return "Test reply", [{"name": "get_weather", "arguments": "{}"}]

    mem = MemoryManager(db, llm=FakeLLM())
    secret_text = "secret message: what's the weather like?"
    tools = AssistantTools(db, mem, tmp_path, user_text=secret_text)
    reply = mem.respond(secret_text, tools=tools)
    assert reply == "Test reply"

    routing_file = tmp_path / "tool_routing.jsonl"
    assert routing_file.exists()
    lines = routing_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert "web" in record["groups"]
    assert record["tools_called"] == ["get_weather"]
    assert record["tool_tokens"] > 0
    assert record["message_length"] == len(secret_text)
    assert secret_text not in lines[0]
    assert "time" in record
    assert "added_groups" in record


def test_fake_embedder_injected(db, tmp_path):
    class FakeEmbedder:
        def embed(self, texts, query=False, wait=False):
            import numpy as np
            return np.ones((len(texts), 384), dtype=np.float32) / (384 ** 0.5)

    fake = FakeEmbedder()
    tools = AssistantTools(db, None, tmp_path, user_text="some query", embedder=fake)
    assert tools.embedder is fake


def test_embedder_failure_falls_back_silently(db, tmp_path):
    class BrokenEmbedder:
        def embed(self, *a, **k):
            raise RuntimeError("Embedder exploded")

    tools = AssistantTools(db, None, tmp_path, user_text="what's the weather like?", embedder=BrokenEmbedder())
    groups = tools.pick_groups()
    assert "web" in groups


# Labelled evaluation set: 50+ diverse messages across paraphrases, typos, follow-ups, and negative cases
LABELLED_ROUTING_CASES = [
    # Mail
    ("what are my most important emails this week?", ["mail"]),
    ("anything from my landlord?", ["mail"]),
    ("did United confirm my flight booking?", ["mail"]),
    ("am I subscribed to any streaming services?", ["mail"]),
    ("find the receipt for my new laptop", ["mail"]),
    ("unsubscribe me from that spam list", ["mail"]),
    ("check my inbox for any bills", ["mail"]),

    # Videos
    ("make a new video and post it to tiktok", ["videos"]),
    ("let's render another quote clip", ["videos"]),
    ("post the newest reel to our account", ["videos"]),
    ("generate a fresh affirmation clip", ["videos"]),
    ("show me the videos we created", ["videos"]),

    # Stats
    ("how are we doing, send me the numbers", ["stats"]),
    ("put together our weekly numbers report", ["stats"]),
    ("how much traffic did we get from Google?", ["stats"]),
    ("check our Pinterest impressions and clicks", ["stats"]),
    ("post new pins for the new guide", ["stats"]),
    ("statistcs report please", ["stats"]),

    # Projects
    ("fix the bug in the website footer", ["projects"]),
    ("there's an error in the layout code", ["projects"]),
    ("add a dark mode toggle to the site", ["projects"]),
    ("start an AgentWork job to update css", ["projects"]),
    ("apply the update to yourself", ["projects"]),
    ("inspect the repository files", ["projects"]),
    ("fixx the bug in index.html", ["projects"]),

    # Schedules
    ("remind me tomorrow at 9am to call mom", ["schedules"]),
    ("wake me up at 7am tomorrow", ["schedules"]),
    ("set a daily reminder to take vitamins", ["schedules"]),
    ("schedule a recurring post every Tuesday", ["schedules"]),
    ("what reminders do I have active?", ["schedules"]),
    ("cancel the 8pm reminder", ["schedules"]),
    ("shcedule a task for 5pm", ["schedules"]),

    # Files
    ("read the document [attached: a.pdf (id att-1)]", ["files"]),
    ("summarize the pdf I uploaded", ["files"]),
    ("what prompt was used for the last picture?", ["files"]),
    ("extract text from the attached screenshot", ["files"]),
    ("what did I draw earlier?", ["files"]),

    # Web / general
    ("what's the weather like?", ["web"]),
    ("weatehr in chicago", ["web"]),
    ("serch google for python 3.12", ["web"]),
    ("what is the capital of Peru?", ["web"]),
    ("who won the game last night?", ["web"]),
    ("can you read and summarize https://example.com/article", ["web"]),

    ("what time is it in Tokyo right now", ["web"]),

    # Small talk / general chat: core tools only since 2026-10-04 (IDEAS #16; the web group used to be added
    # whenever nothing matched, ~444 tokens for nothing); more_tools loads a group if one is needed
    ("good morning how are you today", []),
    ("hello there!", []),
    ("tell me a funny joke", []),
    ("thanks for the help", []),
    ("what is 25 times 4", []),

    # Negative cases (must NOT pull in the mistaken group)
    ("I watched a video about cats", []),
    ("the film was two hours long", []),
    ("I mailed a letter to my grandmother", []),
]


def test_tool_routing_precision_and_recall(db, tmp_path):
    try:
        from core.embedder import get_embedder
        emb = get_embedder()
        if not emb.ready and not emb.ensure_loaded():
            pytest.skip("Local ONNX embedder not available")
    except Exception:
        pytest.skip("Local ONNX embedder not available")

    tp, fp, fn = 0, 0, 0
    group_stats = {g: {"tp": 0, "fp": 0, "fn": 0} for g in TOOL_GROUPS if g != "core"}
    for msg, exp in LABELLED_ROUTING_CASES:
        tools = AssistantTools(db, None, tmp_path, user_text=msg)
        got = tools.pick_groups()
        exp_set = set(exp)
        got_set = set(got)

        tp += len(exp_set & got_set)
        fp += len(got_set - exp_set)
        fn += len(exp_set - got_set)

        for g, stats in group_stats.items():
            if g in exp_set and g in got_set:
                stats["tp"] += 1
            elif g not in exp_set and g in got_set:
                stats["fp"] += 1
            elif g in exp_set and g not in got_set:
                stats["fn"] += 1

    overall_precision = tp / (tp + fp) if (tp + fp) else 0.0
    overall_recall = tp / (tp + fn) if (tp + fn) else 0.0

    print("\n--- Tool Routing Precision and Recall Report ---")
    for g, s in sorted(group_stats.items()):
        p = s["tp"] / (s["tp"] + s["fp"]) if (s["tp"] + s["fp"]) else 1.0
        r = s["tp"] / (s["tp"] + s["fn"]) if (s["tp"] + s["fn"]) else 1.0
        print(f"Group {g:10s} | Precision: {p:.2f} | Recall: {r:.2f} (TP={s['tp']}, FP={s['fp']}, FN={s['fn']})")
    print(f"Overall Precision: {overall_precision:.3f} (target >= 0.70)")
    print(f"Overall Recall:    {overall_recall:.3f} (target >= 0.90)")

    assert overall_recall >= 0.90, f"Expected overall recall >= 0.90, got {overall_recall:.3f}"
    assert overall_precision >= 0.70, f"Expected overall precision >= 0.70, got {overall_precision:.3f}"
