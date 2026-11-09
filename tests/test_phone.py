"""Texts and calls (2026-10-11): the agent drafts, the phone does it. A card shows who, the number and the exact
message; DeskAI opens Messages or the dialer filled in and the owner sends or calls. The agent never sends a text."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.assistant_tools import AssistantTools
from core.database import DatabaseManager
from interfaces.hud import app as hud_app
from interfaces.hud import common
from interfaces.hud import state as hud_state
from tests._hud_client import LOCAL_ADDR, client_from


class FakeMemory:
    def __init__(self):
        self.events = []

    def record_event(self, text):
        self.events.append(text)


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "state.db")
    mem = FakeMemory()
    monkeypatch.setattr(hud_state, "get_memory", lambda: mem)
    original = hud_state.db
    hud_state.set_db(db)
    yield mem, lambda text: AssistantTools(db, mem, tmp_path, user_text=text, source="test chat")
    hud_state.set_db(original)


def contacts(*people):
    return patch("core.connectors.run", return_value={"contacts": [
        {"name": n, "emails": [], "phones": list(p), "org": ""} for n, p in people]})


def test_a_number_goes_straight_on_the_card(env):
    _, tools = env
    t = tools("text 217-555-0101 that I'm outside")
    out = t.call("propose_text", {"to": "217-555-0101", "body": "I'm outside"})
    assert "Nothing has been sent" in out
    card = t.proposals[-1]
    assert card["kind"] == "text" and card["add"] == {"name": "217-555-0101", "number": "2175550101", "body": "I'm outside"}


def test_a_name_is_looked_up_in_contacts(env):
    _, tools = env
    t = tools("text Jake I'll be late")
    with contacts(("Jake Rivera", ["+1 217-555-0199"])):
        out = t.call("propose_text", {"to": "Jake", "body": "I'll be late"})
    assert "Messages" in out
    assert t.proposals[-1]["add"]["number"] == "+1 217-555-0199"
    assert "Text Jake Rivera (+1 217-555-0199)" in t.proposals[-1]["instruction"]


def test_more_than_one_number_means_asking(env):
    _, tools = env
    t = tools("call Sam")
    with contacts(("Sam Lee", ["111-222-3333", "444-555-6666"])):
        out = t.call("propose_call", {"to": "Sam"})
    assert "more than one number" in out and "ask_user" in out and not t.proposals


def test_not_in_google_contacts_the_phone_looks_it_up(env):
    """The owner's contacts live on the phone (2026-10-11: "Sam Rivera (New number)" wasn't in Google)."""
    _, tools = env
    t = tools("text Sam Rivera hi")
    with contacts(("Pat", [])):
        out = t.call("propose_text", {"to": "Sam Rivera", "body": "hi"})
    assert "finds the name in its own contacts" in out
    card = t.proposals[-1]
    assert card["add"] == {"name": "Sam Rivera", "number": "", "body": "hi"}
    assert "from your phone's contacts" in card["instruction"]


def test_the_card_carries_the_phone_details_and_opening_it_is_remembered(env):
    mem, tools = env
    t = tools("call the dentist")
    with contacts(("Dentist", ["217-555-0123"])):
        t.call("propose_call", {"to": "Dentist"})
    card = common.store_proposals(t.proposals)[0]
    assert card["kind"] == "call" and card["phone"] == {"name": "Dentist", "number": "217-555-0123"}
    r = client_from(hud_app, addr=LOCAL_ADDR).post(f"/api/proposals/{card['id']}/run")
    assert r.status_code == 200 and "Opened a call to Dentist" in r.json()["message"]
    assert any("they send or call themselves" in e for e in mem.events)


def test_texting_words_offer_the_phone_tools(env):
    _, tools = env
    for text in ("text my brother that I'll be late", "call the doctor's office", "let my wife know I'm leaving"):
        assert "propose_text" in [s["function"]["name"] for s in tools(text).tool_specs()], text


def test_claiming_a_card_that_wasnt_made_gets_an_honest_note():
    """2026-10-11: "Here is the card to text Sam" after the text tool had failed."""
    from core.memory import unbacked_claim_note
    said = "I've loaded the phone tool now. Here is the card to text Sam:"
    failed = [{"name": "propose_text", "result": "Error: no phone number for 'Sam'"}]
    assert "There's no card this time" in unbacked_claim_note(said, failed)
    worked = [{"name": "propose_text", "result": "The user sees a card with the exact text"}]
    assert unbacked_claim_note(said, worked) == said
