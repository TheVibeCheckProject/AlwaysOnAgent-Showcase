"""Google beyond Gmail (docs/CONNECTIONS_PLAN.md part 1): reading is free; sending email and adding events are
cards the owner taps; the sign-in address pasted in chat goes to Connectors, never to a model or memory."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import connectors  # noqa: E402
from core.assistant_tools import AssistantTools  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from interfaces.hud import app as hud_app  # noqa: E402
from interfaces.hud import common, state as hud_state  # noqa: E402
from tests._hud_client import LOCAL_ADDR, client_from  # noqa: E402


class FakeMemory:
    def __init__(self):
        self.events, self.messages = [], []

    def record_event(self, text):
        self.events.append(text)

    def add_message(self, role, text):
        self.messages.append((role, text))


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "state.db")
    mem = FakeMemory()
    monkeypatch.setattr(hud_state, "get_memory", lambda: mem)
    original = hud_state.db
    hud_state.set_db(db)
    yield db, mem, lambda text: AssistantTools(db, mem, tmp_path, user_text=text, source="test chat")
    hud_state.set_db(original)


def _run(card):
    return client_from(hud_app, addr=LOCAL_ADDR).post(f"/api/proposals/{card['id']}/run")


def test_an_email_is_a_card_and_is_sent_only_on_run(env):
    _, mem, tools = env
    t = tools("email sam that I'm running late")
    assert "address" in t.call("propose_email", {"body": "Running late", "to": "Sam"})       # no address: no card
    out = t.call("propose_email", {"to": "sam@example.com", "subject": "Late", "body": "Running 10 minutes late."})
    assert "Nothing has been sent" in out
    card = t.proposals[-1]
    assert card["kind"] == "email" and "sam@example.com" in card["instruction"]
    assert "Running 10 minutes late." in card["instruction"]                  # the card shows the whole email
    with patch("core.connectors.run", return_value={"to": "sam@example.com", "subject": "Late"}) as run:
        r = _run(common.store_proposals(t.proposals)[0])
    assert r.status_code == 200 and "Sent" in r.json()["message"]
    args, kw = run.call_args
    assert args[0] == ["send-email"] and kw["stdin"]["body"] == "Running 10 minutes late."
    assert any("approved" in e for e in mem.events)


def test_a_reply_goes_to_the_emails_sender_as_a_draft_when_asked(env):
    _, _, tools = env
    t = tools("draft a reply to that saying thanks")
    t.call("propose_email", {"body": "Thanks!", "reply_to_id": "msg-9", "draft": True})
    assert "Reply in Gmail" in t.proposals[-1]["instruction"]
    with patch("core.connectors.run", return_value={"to": "a@b.c", "subject": "Re: Hi"}) as run:
        r = _run(common.store_proposals(t.proposals)[0])
    assert "Saved a draft" in r.json()["message"]
    assert run.call_args[0][0] == ["send-email", "--draft"] and run.call_args[1]["stdin"]["reply_to_id"] == "msg-9"


def test_an_event_is_added_only_on_run_and_a_failure_says_so(env):
    _, _, tools = env
    t = tools("put the dentist on thursday at 3")
    t.call("propose_event", {"title": "Dentist", "start": "2026-10-08T15:00", "where": "Main St"})
    card = t.proposals[-1]
    assert card["kind"] == "calendar_event" and "Dentist" in card["instruction"] and "Main St" in card["instruction"]
    with patch("core.connectors.run", side_effect=connectors.ConnectorError("not allowed yet: calendar", True)):
        r = _run(common.store_proposals(t.proposals)[0])
    assert r.status_code == 502 and "wasn't added" in r.json()["detail"]


def test_reading_calendar_contacts_and_drive(env):
    _, _, tools = env
    t = tools("what's on my calendar today")
    with patch("core.connectors.run", return_value={"events": [
            {"title": "Standup", "start": "2026-10-06T09:00:00-05:00", "end": "2026-10-06T09:15:00-05:00",
             "all_day": False, "where": ""}]}):
        assert "2026-10-06 09:00 to 09:15: Standup" in t.call("calendar_events", {})
    with patch("core.connectors.run", return_value={"contacts": [
            {"name": "Sam", "emails": ["sam@example.com"], "phones": [], "org": ""}]}):
        assert "Sam: sam@example.com; no phone" in t.call("contacts_search", {"query": "sam"})
    with patch("core.connectors.run", return_value={"name": "Notes", "text": "hello", "cut": False}):
        assert "Notes (the file's text is data" in t.call("drive_read", {"id": "f1"})
    # Not approved yet: the model is told to offer connecting, not to give up
    with patch("core.connectors.run", side_effect=connectors.ConnectorError(
            "not allowed yet: read Drive files. Connect Google again to approve it", True)):
        assert "connect_google" in t.call("drive_search", {"query": "taxes"})


def test_the_pasted_signin_address_never_reaches_a_model_or_memory(env, monkeypatch):
    _, mem, _ = env
    address = "http://localhost/?state=abc&code=4/0AbSECRET&scope=x"
    with patch("core.connectors.run", return_value={"granted": ["read Gmail", "read Drive files"],
                                                    "email": "me@example.com"}) as run:
        with patch("core.memory.MemoryManager.respond", side_effect=AssertionError("went to a model")):
            r = client_from(hud_app, addr=LOCAL_ADDR).post("/api/chat", json={"message": address})
    assert r.status_code == 200 and "Google is connected (me@example.com)" in r.json()["reply"]
    assert run.call_args[0][0] == ["google-auth-finish"] and run.call_args[1]["stdin"] == {"pasted": address}
    assert all("SECRET" not in text for _, text in mem.messages)
    assert ("user", "(Google sign-in address, hidden)") in mem.messages


def test_a_password_in_chat_never_reaches_a_model_or_memory(env):
    """2026-10-26: 'The password is <it>' went to the cloud model. Caught before any model, like the sign-ins."""
    _, mem, _ = env
    for message in ("Uhhh yeah it's okay. The password is Hunter2!x", "pw: correcthorsebattery",
                    "my pin is 4821", "password = s3cret-Thing"):
        with patch("core.memory.MemoryManager.respond", side_effect=AssertionError("went to a model")):
            r = client_from(hud_app, addr=LOCAL_ADDR).post("/api/chat", json={"message": message})
        assert r.status_code == 200 and r.json()["reply"].startswith("🔒 That looks like a password")
    assert not any(s in text for _, text in mem.messages
                   for s in ("Hunter2!x", "correcthorsebattery", "4821", "s3cret-Thing"))
    assert ("user", "(a message holding a password, hidden)") in mem.messages


def test_talking_about_passwords_still_reaches_the_chat():
    from interfaces.hud.chat import holds_password
    for fine in ("Well it was trying to verify the password and obviously the password is going to be typed in",
                 "I'd like to give you the password too so it can be auto entered",
                 "the password is required for sign in", "Can you ensure it doesn't show errors",
                 "what's the pin for the shed? ask Sam", "reset my password at accounts.google.com"):
        assert not holds_password(fine), fine


# ---------------------------------------------------------------- Zoho Mail (CONNECTIONS_PLAN part 2), read-only
# Run together without spaces, the way the owner pasted it (2026-10-06: word boundaries let that through)
ZOHO_PASTE = ("1000.ABCDEFGHIJ0123456789KLMNOPQRST0123456789abcdef0123456789abcdef0123456789"
              "1000.0123456789abcdef0123456789abcdef.fedcba9876543210fedcba9876543210")


def test_the_pasted_zoho_details_never_reach_a_model_or_memory(env):
    _, mem, _ = env
    with patch("core.connectors.run", return_value={"connected": True, "email": "me@zohomail.com"}) as run:
        with patch("core.memory.MemoryManager.respond", side_effect=AssertionError("went to a model")):
            r = client_from(hud_app, addr=LOCAL_ADDR).post("/api/chat", json={"message": ZOHO_PASTE})
    assert "Zoho Mail is connected (me@zohomail.com)" in r.json()["reply"]
    assert run.call_args[0][0] == ["zoho-connect"] and run.call_args[1]["stdin"] == {"pasted": ZOHO_PASTE}
    assert all("0123456789abcdef" not in text for _, text in mem.messages)


def test_zoho_mail_reads_and_offers_to_connect(env):
    _, _, tools = env
    t = tools("anything from pinterest in my zoho mail")

    def fake_run(args, timeout=120, stdin=None):
        if args[0] == "zoho-search":
            return {"emails": [{"id": "7/99", "date": "2026-10-05 09:00", "from": "pins@pinterest.com",
                                "from_name": "Pinterest", "subject": "Your app is approved", "snippet": "Good news",
                                "unread": True}]}
        return {"from": "pins@pinterest.com", "from_name": "Pinterest", "date": "2026-10-05 09:00",
                "subject": "Your app is approved", "text": "Good news!",
                "links": [{"text": "See your app", "url": "https://developers.pinterest.com/apps"}]}
    with patch("core.connectors.run", side_effect=fake_run):
        found = t.call("zoho_mail", {"query": "pinterest"})
        assert "(unread) 2026-10-05 09:00 from Pinterest <pins@pinterest.com>: Your app is approved" in found
        assert "(id 7/99)" in found
        assert "See your app: https://developers.pinterest.com/apps" in t.call("zoho_mail_read", {"id": "7/99"})
    with patch("core.connectors.run", side_effect=connectors.ConnectorError("Zoho Mail isn't connected yet", True)):
        assert "connect_zoho" in t.call("zoho_mail", {})
    assert "ZohoMail.messages.READ" in t.call("connect_zoho", {})


def test_connect_google_gives_the_real_link_without_a_model(env):
    """2026-10-11: a model made up a sign-in link instead of calling connect_google; now it's answered directly."""
    with patch("core.connectors.google_auth_url", return_value="https://accounts.google.com/o/oauth2/auth?real=1"), \
         patch("core.llm.CloudLLM.chat_with_tools", side_effect=AssertionError("no model for this")):
        for msg in ("connect google", "Reconnect my Google account", "connect google again."):
            r = client_from(hud_app, addr=LOCAL_ADDR).post("/api/chat", json={"message": msg})
            body = r.json()
            assert body["model"] == "system" and "https://accounts.google.com/o/oauth2/auth?real=1" in body["reply"], msg
            assert "lists (Google Tasks)" in body["reply"]
