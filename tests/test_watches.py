"""Mail watches (docs/WATCHES.md): "let me know whenever Zoho gets a Pinterest email"."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.assistant_tools import AssistantTools  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.watches import Watches  # noqa: E402


def zoho_mail(i, sender="recommendations@explore.pinterest.com", subject="Obsessed", name="Pinterest"):
    return {"id": f"9/{i}", "from": sender, "from_name": name, "subject": subject, "snippet": "", "date": ""}


class FakeConnectors:
    def __init__(self):
        self.zoho, self.gmail, self.queries = [], [], []

    def zoho_search(self, key, limit=10):
        self.queries.append(("zoho", key))
        return list(self.zoho)

    def search(self, query, limit=10):
        self.queries.append(("gmail", query))
        return {"emails": list(self.gmail)}


class FakeProactive:
    def __init__(self, quiet=False):
        self.sent, self.is_quiet = [], quiet

    def quiet(self, t):
        return self.is_quiet

    def deliver(self, kind, text, topic="", urgent=False):
        assert urgent, "a watch's email is never held for later"
        self.sent.append((kind, text, topic))
        return {"kind": kind, "text": text}


@pytest.fixture
def env(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    conn, pro = FakeConnectors(), FakeProactive()
    return db, conn, pro, Watches(db, conn, pro)


def test_only_new_mail_is_told_and_only_once(env):
    _, conn, pro, w = env
    conn.zoho = [zoho_mail(1)]                                   # already there when the watch is made
    made = w.add("zoho", sender="pinterest")
    assert ("zoho", "sender:pinterest") in conn.queries
    assert w.run_check() == []                                   # the old one doesn't count
    conn.zoho = [zoho_mail(2, subject="Your app is approved"), zoho_mail(1)]
    w.run_check()
    assert len(pro.sent) == 1 and "Your app is approved" in pro.sent[0][1] and "id 9/2" in pro.sent[0][1]
    assert pro.sent[0][2] == f"watch:{made['id']}"
    w.run_check()
    assert len(pro.sent) == 1                                    # never twice
    assert w.get(made["id"])["matches"] == 1


def test_other_fields_are_checked_here_and_once_stops(env):
    _, conn, pro, w = env
    made = w.add("zoho", sender="pinterest", subject="approved", once=True)
    conn.zoho = [zoho_mail(3, subject="Obsessed"), zoho_mail(4, sender="x@other.com", subject="approved", name="Other"),
                 zoho_mail(5, subject="Your app is approved")]
    w.run_check()
    assert len(pro.sent) == 1 and "9/5" in pro.sent[0][1] and "9/3" not in pro.sent[0][1] and "9/4" not in pro.sent[0][1]
    assert "stopped now" in pro.sent[0][1] and w.list() == []


def test_gmail_query_and_quiet_hours_hold(env):
    db, conn, _, _ = env
    quiet = FakeProactive(quiet=True)
    w = Watches(db, conn, quiet)
    w.add("gmail", sender="jackson.com")
    assert ("gmail", "from:(jackson.com) newer_than:2d") in conn.queries
    conn.gmail = [{"id": "g1", "from": "customercare@jackson.com", "from_name": "Jackson", "subject": "Your claim"}]
    w.run_check()
    assert quiet.sent == []                                      # held, not lost: still unseen
    quiet.is_quiet = False
    w.run_check()
    assert len(quiet.sent) == 1 and "Gmail: Jackson <customercare@jackson.com>" in quiet.sent[0][1]


def test_a_watch_needs_something_to_match_and_a_connected_mailbox(env):
    _, conn, _, w = env
    with pytest.raises(ValueError):
        w.add("zoho")

    def broken(key, limit=10):
        raise RuntimeError("Zoho Mail isn't connected yet")
    conn.zoho_search = broken
    with pytest.raises(RuntimeError):
        w.add("zoho", sender="pinterest")
    assert w.list() == []


def test_chat_tools(env, tmp_path, monkeypatch):
    db, conn, _, _ = env
    monkeypatch.setattr("core.watches.Watches.__init__.__defaults__", (conn, None))
    t = AssistantTools(db, None, tmp_path, user_text="let me know whenever zoho gets a pinterest email")
    out = t.call("watch_mail", {"source": "zoho", "sender": "pinterest"})
    assert "watch #1: Zoho Mail, sender contains 'pinterest', every time" in out
    assert "watch #1" in t.call("list_watches", {})
    assert "doesn't ask for this" in t.call("stop_watch", {"id": 1})          # audit 2.3: the user's words only
    t.user_text = "stop that pinterest watch"
    assert t.call("stop_watch", {"id": 1}) == "Stopped watch #1."
    assert t.call("list_watches", {}) == "No active watches."


# ---------------------------------------------------------------- 2026-10-10: "only the upgrade email", asked twice
def test_a_watch_can_be_narrowed_in_place_without_a_flood(env):
    db, conn, pro, w = env
    conn.zoho = [zoho_mail(1), zoho_mail(2, sender="pinbot@account.pinterest.com", subject="Your API access request")]
    w.add("zoho", sender="pinterest")
    changed = w.change(1, sender="pinbot@account.pinterest.com")
    assert changed["sender"] == "pinbot@account.pinterest.com" and changed["active"] == 1
    w.run_check()
    assert pro.sent == []                                        # what matched already isn't news
    with pytest.raises(ValueError):
        w.change(1, sender="", subject="", words="")
    with pytest.raises(ValueError):
        w.change(99, words="x")


def test_mentioning_what_a_watch_matches_loads_the_watch_tools(env, tmp_path, monkeypatch):
    """The second ask had no 'watch' in it ("only show the pinterest email..."), so no watch tools: the reply
    claimed it had muted them."""
    db, conn, _, w = env
    monkeypatch.setattr("core.watches.Watches.__init__.__defaults__", (conn, None))
    w.add("zoho", sender="pinterest")
    t = AssistantTools(db, None, tmp_path, user_text="Can you only show the pinterest email that gives me access")
    assert "change_watch" in [s["function"]["name"] for s in t.tool_specs()]
    t2 = AssistantTools(db, None, tmp_path, user_text="what's the weather")
    assert "change_watch" not in [s["function"]["name"] for s in t2.tool_specs()]
    assert "Changed: watch #1" in t.call("change_watch", {"id": 1, "words": "access"})


def test_a_reply_claiming_a_change_no_tool_made_gets_an_honest_note():
    from core.memory import unbacked_claim_note
    said = "Got it, my bad. I've muted those completely, I won't ping you about the Pinterest recommendations."
    assert "I didn't actually change any watch" in unbacked_claim_note(said, [])
    assert unbacked_claim_note(said, [{"name": "change_watch"}]) == said
    assert unbacked_claim_note("It's 75F and clear in Springfield.", []) == "It's 75F and clear in Springfield."


def test_a_follow_up_reminds_when_nothing_came_by_the_deadline(env, monkeypatch):
    """"If the landlord hasn't replied by Friday, remind me" (2026-10-11): a once watch with a deadline."""
    import datetime
    _, conn, pro, w = env
    made = w.add("gmail", sender="office@landlord.example.com", deadline="2099-10-17", note="the lease")
    assert made["once"] == 1 and made["deadline"].startswith("2099-10-17T17:00")
    assert "follow-up until 2099-10-17 17:00" in Watches.describe(made)
    w.run_check()
    assert pro.sent == [] and w.get(made["id"])["active"] == 1           # not due yet
    with w.db.get_connection() as c:
        c.execute("UPDATE watches SET deadline = ? WHERE id = ?",
                  ((datetime.datetime.now().astimezone() - datetime.timedelta(minutes=1)).isoformat(), made["id"]))
    w.run_check()
    assert len(pro.sent) == 1 and "nothing from office@landlord.example.com yet (the lease)" in pro.sent[0][1]
    assert w.list() == []                                                # done after the reminder
    w.run_check()
    assert len(pro.sent) == 1                                            # never twice


def test_a_reply_before_the_deadline_ends_the_follow_up(env):
    _, conn, pro, w = env
    made = w.add("gmail", sender="landlord", deadline="2099-10-17", note="the lease")
    conn.gmail = [{"id": "g1", "from": "office@landlord.example.com", "from_name": "Landlord", "subject": "Re: lease", "snippet": ""}]
    w.run_check()
    assert len(pro.sent) == 1 and "it came in before the deadline" in pro.sent[0][1]
    assert w.get(made["id"])["active"] == 0


def test_a_bad_or_past_deadline_is_refused(env):
    _, _, _, w = env
    for bad, why in (("next friday", "must be a date"), ("2001-01-01", "already passed")):
        with pytest.raises(ValueError, match=why):
            w.add("gmail", sender="landlord", deadline=bad)


def test_follow_up_words_offer_the_watch_tools(env, tmp_path):
    db = env[0]
    for text in ("if the landlord hasn't replied by Friday, remind me", "follow up with me if Sam doesn't answer by Monday"):
        t = AssistantTools(db, None, tmp_path, user_text=text, source="test chat")
        assert "watch_mail" in [s["function"]["name"] for s in t.tool_specs()], text
