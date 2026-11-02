"""
The weekly memory check (core/memory_review.py): proposals the code can't check are dropped, nothing changes
before the owner answers, and the answer applies exactly the kept items (in DeskAI or in chat).
"""

import datetime
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import memory_review as mr  # noqa: E402
from core.assistant_tools import AssistantTools  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.memory import MemoryManager  # noqa: E402
from tests.test_memory import FakeEmbedder, FakeLLM as MemoryLLM  # noqa: E402

TZ = datetime.datetime.now().astimezone().tzinfo


class ReviewLLM(MemoryLLM):
    """The memory's scripted answers, plus the weekly check's."""

    def __init__(self, answer):
        super().__init__()
        self.answer = answer

    def chat(self, messages, **kw):
        if "keep a personal assistant's long-term memory" in messages[0]["content"]:
            self.calls.append(messages)
            return json.dumps(self.answer)
        return super().chat(messages)


class Delivered(list):
    def deliver(self, kind, text, topic="", canvas=None):
        self.append({"kind": kind, "text": text, "topic": topic})
        return {"id": len(self)}


@pytest.fixture
def setup(tmp_path):
    def make(answer):
        llm = ReviewLLM(answer)
        mem = MemoryManager(DatabaseManager(tmp_path / "state.db"), llm=llm, embedder=FakeEmbedder())
        known = mem.add_fact("Alex plays golf at Maple Ridge Golf Course.", "preference")[0]
        stale = mem.add_fact("Alex has the contractor ready to start once his permit is approved.", "plan")[0]
        mem.add_message("user", "I run Example Site, it posts affirmation videos to TikTok.")
        mem.add_message("event", "Zoho Mail connected: wecare@example-site.com")
        mem.add_message("user", "The city approved my permit, the contractor starts Monday.")
        sent = Delivered()
        return mem, mr.MemoryReview(mem.db, mem, llm, sent), sent, known, stale
    return make


def answer_with(known, stale):
    return {"add": [
        {"content": "Alex runs Example Site, which posts affirmation videos to TikTok.",
         "category": "project", "quote": "I run Example Site"},
        {"content": "Example Site's email is wecare@example-site.com.", "category": "project",
         "quote": "Zoho Mail connected: wecare@example-site.com"},
        {"content": "Alex has a dog called Pepper.", "category": "person", "quote": "my dog Pepper"},     # never said
        {"content": "Alex plays golf at Maple Ridge Golf Course.", "category": "preference",
         "quote": "I run Example Site"},                                                       # already known
    ], "retire": [{"id": stale, "reason": "the permit was approved"}, {"id": 999, "reason": "no such fact"}]}


def now():
    return datetime.datetime.now().astimezone()


def test_due_on_saturday_late_morning_once_a_week(setup):
    mem, review, *_ = setup({"add": [], "retire": []})
    sat = datetime.datetime(2026, 10, 17, 11, 5, tzinfo=TZ)
    assert sat.weekday() == mr.REVIEW_WEEKDAY
    assert not review.due(sat.replace(hour=10)) and not review.due(sat - datetime.timedelta(days=1))
    assert review.due(sat)
    review.db.set_state(mr.STATE_LAST, sat.isoformat())
    assert not review.due(sat.replace(hour=15))
    assert review.due(sat + datetime.timedelta(days=7))


def test_only_checkable_proposals_are_asked_and_nothing_changes_yet(setup):
    mem, review, sent, known, stale = setup({})
    review.llm.answer = answer_with(known, stale)
    before = {f["id"] for f in mem._fact_rows()}
    pending = review.run(now())
    items = pending["items"]
    assert [(i["n"], i["op"]) for i in items] == [(1, "add"), (2, "add"), (3, "retire")]
    assert "Pepper" not in json.dumps(items) and items[2]["fact_id"] == stale
    assert sent[0]["kind"] == "memory" and "1. Alex runs Example Site" in sent[0]["text"]
    assert {f["id"] for f in mem._fact_rows()} == before                  # asked, not applied
    assert review.pending()["items"] == items


def test_the_answer_applies_exactly_the_kept_items(setup):
    mem, review, sent, known, stale = setup({})
    review.llm.answer = answer_with(known, stale)
    review.run(now())
    with pytest.raises(ValueError):
        review.answer([1, 7])                                               # 7 isn't in it: nothing applied
    assert review.pending() is not None
    r = review.answer([1, 3])
    facts = {f["content"]: f for f in mem._fact_rows()}
    assert "Alex runs Example Site, which posts affirmation videos to TikTok." in facts
    assert not any("wecare@" in c for c in facts)                           # item 2 skipped
    assert stale not in {f["id"] for f in mem._fact_rows()} and r["retired"] == [stale] and r["skipped"] == 1
    assert review.pending() is None
    with pytest.raises(ValueError):
        review.answer([2])                                                  # answered once only


def test_a_quiet_week_sends_nothing(setup):
    mem, review, sent, *_ = setup({"add": [], "retire": []})
    assert review.run(now()) is None and sent == [] and review.pending() is None


def test_chat_offers_memory_check_only_while_one_waits(setup, tmp_path):
    mem, review, sent, known, stale = setup({})
    tools = AssistantTools(mem.db, mem, tmp_path, user_text="keep 1 and 3", embedder=FakeEmbedder())
    assert "memory_check" not in [s["function"]["name"] for s in tools.tool_specs()]
    review.llm.answer = answer_with(known, stale)
    review.run(now())
    tools = AssistantTools(mem.db, mem, tmp_path, user_text="keep 1 and 3", embedder=FakeEmbedder())
    assert "memory_check" in [s["function"]["name"] for s in tools.tool_specs()]
    assert tools.memory_check([1, 3]).startswith("Done: 1 new fact(s) saved, 1 retired, 1 skipped")
    assert tools.memory_check([1]).startswith("Error:")


def test_the_week_is_read_with_dates(setup):
    """The first real check turned "it's his birthday today" (said Oct 7) into "October 5th"."""
    mem, review, *_ = setup({"add": [], "retire": []})
    week = review._week(now())
    today = now().strftime("%Y-%m-%d")
    assert all(line.startswith("[") and today in line for line in week["owner"] + week["events"])
