"""The owner's lists (Google Tasks through Connectors, 2026-10-11): reading, adding and checking off happen right
away (the owner's choice: private and easy to undo); the chat says what it did."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.assistant_tools import AssistantTools  # noqa: E402
from core.database import DatabaseManager  # noqa: E402


class FakeMemory:
    def record_event(self, text):
        pass


@pytest.fixture
def tools(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    return lambda text: AssistantTools(db, FakeMemory(), tmp_path, user_text=text, source="test chat")


def test_adding_several_items_at_once(tools):
    t = tools("add milk and eggs to the shopping list")
    calls = []

    def run(args, stdin=None, **kw):
        calls.append((args, stdin))
        return {"id": "x", "title": stdin["title"], "list": "Shopping", "due": ""}
    with patch("core.connectors.run", side_effect=run):
        out = t.call("add_to_list", {"items": ["milk", "eggs"], "list": "shopping"})
    assert "Added to the Shopping list: milk, eggs" in out and "no card" in out
    assert [c[1]["title"] for c in calls] == ["milk", "eggs"] and calls[0][0] == ["add-task"]
    assert not t.proposals                                  # right away: no card


def test_one_item_as_plain_text_works_too(tools):
    t = tools("I need to buy batteries")
    with patch("core.connectors.run", return_value={"id": "x", "title": "batteries", "list": "My Tasks", "due": ""}):
        assert "batteries" in t.call("add_to_list", {"items": "batteries"})


def test_reading_a_list_and_a_missing_one(tools):
    t = tools("what's on my shopping list")
    with patch("core.connectors.run", return_value={"found": True, "list": "Shopping", "list_id": "L1", "items": [
            {"id": "1", "title": "milk", "due": "", "notes": "", "done": False},
            {"id": "2", "title": "Mail the forms", "due": "2026-10-17", "notes": "", "done": False}]}):
        out = t.call("list_items", {"list": "Shopping"})
    assert "Shopping:" in out and "- milk" in out and "Mail the forms (due 2026-10-17)" in out
    with patch("core.connectors.run", return_value={"found": False, "list": "Hardware", "items": [], "lists": ["Shopping"]}):
        assert "no list called 'Hardware'" in t.call("list_items", {"list": "Hardware"})


def test_checking_off_reports_what_matched(tools):
    t = tools("check off milk and buy")
    replies = iter([{"done": True, "title": "milk", "list": "Shopping"},
                    {"done": False, "reason": "more than one item matches", "candidates": ["buy stamps", "buy envelopes"]}])
    with patch("core.connectors.run", side_effect=lambda *a, **k: next(replies)):
        out = t.call("check_off", {"items": ["milk", "buy"], "list": "Shopping"})
    assert "Checked off: milk" in out and "buy stamps, buy envelopes" in out


def test_list_words_offer_the_list_tools(tools):
    for text in ("add milk to the shopping list", "what's on my to-do list", "check off eggs"):
        assert "add_to_list" in [s["function"]["name"] for s in tools(text).tool_specs()], text
