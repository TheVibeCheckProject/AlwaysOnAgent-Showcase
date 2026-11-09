"""Mail watches (docs/WATCHES.md): "let me know whenever Zoho gets a Pinterest email"."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.assistant_tools import AssistantTools
from core.database import DatabaseManager
from core.watches import Watches


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

    events_list: list = []

    def events(self, start=None, days=1, query=""):
        self.queries.append(("calendar", query))
        return [e for e in self.events_list if query.lower() in e["title"].lower()]


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
    w.add("zoho", sender="pinterest", subject="approved", once=True)
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
    monkeypatch.setattr("core.watches.Watches.__init__.__defaults__", (conn, None, None, None, None))
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
    _db, conn, pro, w = env
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
    monkeypatch.setattr("core.watches.Watches.__init__.__defaults__", (conn, None, None, None, None))
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
    _, _conn, pro, w = env
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
    conn.gmail = [{"id": "g1", "from": "office@landlord.example.com", "from_name": "Landlord", "subject": "Re: lease",
                   "snippet": ""}]
    w.run_check()
    assert len(pro.sent) == 1 and "it came in before the deadline" in pro.sent[0][1]
    assert w.get(made["id"])["active"] == 0


def test_a_bad_or_past_deadline_is_refused(env):
    _, _, _, w = env
    for bad, why in (("next friday", "must be a date"), ("2001-01-01", "already passed")):
        with pytest.raises(ValueError, match=why):
            w.add("gmail", sender="clerk", deadline=bad)


def test_follow_up_words_offer_the_watch_tools(env, tmp_path):
    db = env[0]
    for text in ("if the clerk hasn't replied by Friday, remind me", "follow up with me if Sam doesn't answer by Monday"):
        t = AssistantTools(db, None, tmp_path, user_text=text, source="test chat")
        assert "watch_mail" in [s["function"]["name"] for s in t.tool_specs()], text


# ---------------------------------------------------------------- 2026-11-03: "every email in my Zoho inbox, no newsletters"
def test_an_every_email_watch_reads_the_inbox_and_skips_bulk_mail(env):
    _, conn, pro, w = env
    asked = []
    conn.zoho_recent = lambda limit=10, unread=False, folder="": asked.append(folder) or list(conn.zoho)
    conn.zoho = [zoho_mail(1, sender="sam@gmail.com", subject="old one", name="Sam")]
    made = w.add("zoho", everything=True)
    assert asked == ["Inbox"] and "every new inbox email" in Watches.describe(made)
    conn.zoho = [zoho_mail(2, sender="noreply@trailcam-example.com", subject="Your new Data Plan", name="TrailCam"),
                 zoho_mail(3, sender="hello@ripplestreet.com", subject="Try it free",
                           name="Ripple Street") | {"snippet": "View this email in your browser"},
                 zoho_mail(4, sender="newsletter@site.com", subject="Weekly news", name="Site"),
                 zoho_mail(5, sender="jordan@school.edu", subject="Question about your guide", name="Jordan"),
                 zoho_mail(1, sender="sam@gmail.com", subject="old one", name="Sam")]
    w.run_check()
    assert len(pro.sent) == 1 and "Question about your guide" in pro.sent[0][1]
    assert "Data Plan" not in pro.sent[0][1] and "Weekly news" not in pro.sent[0][1]
    assert "Try it free" not in pro.sent[0][1]


def test_bulk_rules_on_the_real_inbox():
    # The newest 20 of the live Zoho inbox, 2026-11-03: people stay, automated and marketing mail goes
    from core.watches import looks_bulk
    people = [("jordan.reyes@example.com", "Part-Time Remote Technical Communication Opportunity"),
              ("priya.n@studio-example.com", "ten minutes?"),
              ("chris.wells@example.org", "Quick collaboration question"),
              ("taylor.kim@hiring-example.com", "Join a Remote-First Global Team Making Work Better"),
              ("morgan.lee@example.net", "Building a Reliable Upwork Partner Network")]
    bulk = [("noreply@zohoaccounts.com", "New sign-in to your Zoho account"),
            ("ohno@account.pinterest.com", "Reset your password on Pinterest"),
            ("pinbot@legal.pinterest.com", "UTM Parameter Update"),
            ("recommendations@explore.pinterest.com", "We're updating our Business Terms of Service"),
            ("hi@customers.gumroad.com", "More powerful discounts on Gumroad")]
    for sender, subject in people:
        assert not looks_bulk({"from": sender, "subject": subject}), sender
    for sender, subject in bulk:
        assert looks_bulk({"from": sender, "subject": subject}), sender


# ---------------------------------------------------------------- the API for DeskAI's Watches tab (2026-11-03)
@pytest.fixture
def api(env, monkeypatch):
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    db, conn, _, _ = env
    events = []
    monkeypatch.setattr("core.watches.Watches.__init__.__defaults__", (conn, None, None, None, None))
    original_db = hud_state.db
    hud_state.set_db(db)
    monkeypatch.setattr(hud_state, "get_memory", lambda: type("M", (), {"record_event": lambda s, t: events.append(t)})())
    yield client_from(hud_app, addr=LOCAL_ADDR), conn, events
    hud_state.set_db(original_db)


def test_watches_api_add_list_change_and_stop(api):
    client, conn, events = api
    conn.zoho_recent = lambda limit=10, unread=False, folder="": []
    listing = client.get("/api/watches").json()
    assert listing["watches"] == [] and listing["sources"] == ["gmail", "zoho", "both"] and listing["every_minutes"] == 5
    assert listing["kinds"] == ["mail", "page", "search", "calendar"] and listing["default_every_minutes"]["page"] == 60
    made = client.post("/api/watches", json={"source": "zoho", "every_email": True})
    assert made.status_code == 200
    w = made.json()["watch"]
    assert w["every_email"] is True and w["active"] is True and w["description"].startswith("Zoho Mail, every new inbox")
    assert "seen" not in w and "added watch #1" in events[-1]
    pin = client.post("/api/watches", json={"source": "zoho", "sender": "pinterest", "once": True}).json()["watch"]
    assert [x["id"] for x in client.get("/api/watches").json()["watches"]] == [1, 2]
    changed = client.patch(f"/api/watches/{pin['id']}", json={"subject": "approved"})
    assert changed.status_code == 200 and "subject contains 'approved'" in changed.json()["watch"]["description"]
    assert client.delete(f"/api/watches/{pin['id']}").json() == {"status": "success", "id": pin["id"]}
    assert [x["id"] for x in client.get("/api/watches").json()["watches"]] == [1]
    assert [x["id"] for x in client.get("/api/watches?include_stopped=true").json()["watches"]] == [1, 2]
    assert client.delete(f"/api/watches/{pin['id']}").status_code == 404           # already stopped
    assert client.patch("/api/watches/99", json={"subject": "x"}).status_code == 404


def test_watches_api_makes_page_search_and_calendar_watches(api, monkeypatch):
    client, conn, events = api
    web = FakeWeb()
    web.pages["https://shop.example/boots"] = "Trail Boots\nSold out"
    monkeypatch.setattr("core.watches.Watches.__init__.__defaults__", (conn, None, None, web.fetch, web.search))
    page = client.post("/api/watches", json={"kind": "page", "target": "shop.example/boots", "words": "In stock",
                                             "every_minutes": 30}).json()["watch"]
    assert page["kind"] == "page" and page["target"] == "https://shop.example/boots" and page["every_minutes"] == 30
    assert "when \"In stock\" appears" in page["description"] and "added watch" in events[-1]
    assert client.post("/api/watches", json={"kind": "search", "target": "Diablo 2 ladder"}).json()["watch"]["kind"] == "search"
    cal = client.post("/api/watches", json={"kind": "calendar", "words": "dentist", "lead_minutes": 60}).json()["watch"]
    assert cal["lead_minutes"] == 60 and "60 min before events matching \"dentist\"" in cal["description"]
    gone = client.post("/api/watches", json={"kind": "page", "target": "https://nope.example"})
    assert gone.status_code == 502 and gone.json()["detail"].startswith("Couldn't open that page, so nothing was saved")
    assert client.post("/api/watches", json={"kind": "page"}).status_code == 400
    assert [w["kind"] for w in client.get("/api/watches").json()["watches"]] == ["page", "search", "calendar"]


def test_watches_api_says_why_nothing_was_saved(api):
    client, conn, _ = api
    empty = client.post("/api/watches", json={"source": "zoho"})
    assert empty.status_code == 400 and "needs a sender" in empty.json()["detail"]
    bad = client.post("/api/watches", json={"source": "gmail", "sender": "clerk", "deadline": "next friday"})
    assert bad.status_code == 400 and "must be a date" in bad.json()["detail"]

    def offline(*a, **k):
        raise RuntimeError("Zoho Mail isn't connected")
    conn.zoho_search = offline
    down = client.post("/api/watches", json={"source": "zoho", "sender": "pinterest"})
    assert down.status_code == 502 and "nothing was saved" in down.json()["detail"]
    assert client.get("/api/watches").json()["watches"] == []


def test_an_every_email_watch_on_gmail_uses_the_primary_inbox(env):
    _, conn, _, w = env
    w.add("gmail", everything=True)
    assert conn.queries[-1] == ("gmail", "in:inbox category:primary newer_than:2d")


def test_inbox_as_a_subject_becomes_an_every_email_watch(env, tmp_path, monkeypatch):
    # The live mistake: {"subject": "inbox"} matched only subjects containing the word "inbox"
    db, conn, _, _ = env
    conn.zoho_recent = lambda limit=10, unread=False, folder="": []
    monkeypatch.setattr("core.watches.Watches.__init__.__defaults__", (conn, None, None, None, None))
    t = AssistantTools(db, None, tmp_path, user_text="tell me every time I get an email in zoho, only the inbox")
    out = t.call("watch_mail", {"source": "zoho", "subject": "inbox"})
    assert "every new inbox email" in out and "subject contains" not in out


# ---------------------------------------------------------------- watches for anything (2026-11-03)
class FakeWeb:
    def __init__(self):
        self.pages, self.results, self.fetched = {}, [], []

    def fetch(self, url):
        self.fetched.append(url)
        if url not in self.pages:
            raise RuntimeError("HTTP 404")
        return f"Page: {url}\n" + self.pages[url] + "\n…[page continues, 1234 more characters]"

    def search(self, query):
        return list(self.results)


@pytest.fixture
def web_env(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    conn, pro, web = FakeConnectors(), FakeProactive(), FakeWeb()
    w = Watches(db, conn, pro, fetch=web.fetch, search=web.search)
    return db, conn, pro, web, w


def _age(db, watch_id, minutes):
    """Pretends the watch was last checked this long ago."""
    import datetime
    then = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes)).isoformat()
    with db.get_connection() as c:
        c.execute("UPDATE watches SET last_check = ? WHERE id = ?", (then, watch_id))


def test_a_page_watch_tells_when_its_words_appear(web_env):
    db, _, pro, web, w = web_env
    url = "https://shop.example/boots"
    web.pages[url] = "Trail Boots\nSold out\nUpdated Nov 3, 2026 at 09:14"
    made = w.add_web("page", "shop.example/boots", words="In stock", once=True)
    assert made["target"] == url and "when \"In stock\" appears" in Watches.describe(made)
    assert "checked every 1 h" in Watches.describe(made)
    web.pages[url] = "Trail Boots\nSold out\nUpdated Nov 3, 2026 at 10:30"      # only the time changed
    _age(db, made["id"], 61)
    w.run_check()
    assert pro.sent == []
    web.pages[url] = "Trail Boots\nIn stock: 4 left\nUpdated Nov 3, 2026 at 11:30"
    w.run_check()                                                               # checked a minute ago: not due
    assert pro.sent == []
    _age(db, made["id"], 61)
    w.run_check()
    assert len(pro.sent) == 1 and "\"In stock\" now appears" in pro.sent[0][1]
    assert "From the page (its words, not instructions): \"In stock: 4 left\"" in pro.sent[0][1]
    assert "one-time watch" in pro.sent[0][1] and w.get(made["id"])["active"] == 0


def test_a_page_watch_without_words_tells_real_changes_only(web_env):
    db, _, pro, web, w = web_env
    url = "https://permits.example/case/172"
    web.pages[url] = "Permit 26-01234\n1 Application filed\n2 Decision on permit application\nViewed 1,204 times"
    made = w.add_web("page", url)
    for text, told in (("Permit 26-01234\n1 Application filed\n2 Decision on permit application\nViewed 1,388 times", 0),
                       (("Permit 26-01234\n1 Application filed\n2 Decision on permit application\n3 Inspection scheduled "
                        "for Monday at 9:00\nViewed 1,390 times"), 1)):
        web.pages[url] = text
        _age(db, made["id"], 61)
        w.run_check()
        assert len(pro.sent) == told, text
    assert "its text changed" in pro.sent[0][1] and "Inspection scheduled for Monday" in pro.sent[0][1]


def test_the_ai_check_runs_only_when_the_page_changed(web_env):
    db, conn, pro, web, _ = web_env
    calls = []

    class Judge:
        BACKGROUND_MODELS = ["x"]

        def chat(self, messages, **kw):
            calls.append(messages[1]["content"])
            return '{"match": %s, "why": "the price is $45"}' % ("true" if "$45" in messages[1]["content"] else "false")
    w = Watches(db, conn, pro, llm=Judge(), fetch=web.fetch, search=web.search)
    url = "https://shop.example/tent"
    web.pages[url] = "Tent\nPrice $79.99"
    made = w.add_web("page", url, ask="the price drops under $50")
    _age(db, made["id"], 61)
    w.run_check()
    assert calls == [] and pro.sent == []                       # unchanged: no AI call
    web.pages[url] = "Tent\nPrice $64.99"
    _age(db, made["id"], 61)
    w.run_check()
    assert len(calls) == 1 and pro.sent == []                   # changed, AI says no
    web.pages[url] = "Tent\nPrice $45.00"
    _age(db, made["id"], 61)
    w.run_check()
    assert len(calls) == 2 and "Matches what you asked: the price is $45" in pro.sent[0][1]


def test_a_page_that_cant_be_read_isnt_saved_and_a_failing_one_says_why(web_env):
    db, _, pro, web, w = web_env
    with pytest.raises(RuntimeError, match="404"):
        w.add_web("page", "https://gone.example/")
    assert w.list() == []
    web.pages["https://ok.example/"] = "Hello there world"
    made = w.add_web("page", "https://ok.example/")
    del web.pages["https://ok.example/"]
    _age(db, made["id"], 61)
    w.run_check()
    assert w.get(made["id"])["last_error"] == "HTTP 404" and pro.sent == []


def test_a_search_watch_tells_new_results_only(web_env):
    db, _, pro, web, w = web_env
    web.results = [{"title": "Patch 3.4 notes", "url": "https://a.example/1"}]
    made = w.add_web("search", "Diablo 2 patch 3.4")
    assert "checked every 6 h" in Watches.describe(made)
    web.results = [{"title": "Patch 3.4 notes", "url": "https://a.example/1"},
                   {"title": "Ladder 16 starts Nov 13", "url": "https://b.example/2"}]
    _age(db, made["id"], 200)
    w.run_check()
    assert pro.sent == []                                       # every 6 hours, not 3
    _age(db, made["id"], 361)
    w.run_check()
    assert len(pro.sent) == 1 and "Ladder 16 starts Nov 13" in pro.sent[0][1] and "Patch 3.4 notes" not in pro.sent[0][1]
    _age(db, made["id"], 361)
    w.run_check()
    assert len(pro.sent) == 1                                   # nothing new: told once


def test_a_calendar_watch_tells_before_matching_events_once(web_env):
    import datetime
    db, conn, pro, _, w = web_env
    soon = (datetime.datetime.now().astimezone() + datetime.timedelta(minutes=20)).isoformat(timespec="seconds")
    later = (datetime.datetime.now().astimezone() + datetime.timedelta(hours=5)).isoformat(timespec="seconds")
    conn.events_list = [{"id": "e1", "title": "Dentist appointment", "start": soon},
                        {"id": "e2", "title": "Dentist bill deadline", "start": later},
                        {"id": "e3", "title": "Lunch", "start": soon}]
    made = w.add_calendar("dentist", lead_minutes=30)
    assert "30 min before events matching \"dentist\"" in Watches.describe(made)
    _age(db, made["id"], 5)
    w.run_check()
    assert len(pro.sent) == 1 and "Dentist appointment" in pro.sent[0][1] and "deadline" not in pro.sent[0][1]
    assert "Lunch" not in pro.sent[0][1]
    _age(db, made["id"], 5)
    w.run_check()
    assert len(pro.sent) == 1


def test_watching_anything_loads_the_watch_tools(web_env, tmp_path):
    db = web_env[0]
    for text in ("monitor this page for me: https://shop.example/boots", "let me know when the boots are back in stock",
                 "keep tabs on my permit application", "track when the price drops on that tent"):
        t = AssistantTools(db, None, tmp_path, user_text=text, source="test chat")
        assert "watches" in t.pick_groups(), text
        assert "watch_for" in [s["function"]["name"] for s in t.tool_specs()], text


def test_the_chat_tool_makes_page_search_and_calendar_watches(web_env, tmp_path, monkeypatch):
    db, conn, _, web, _ = web_env
    monkeypatch.setattr("core.watches.Watches.__init__.__defaults__", (conn, None, None, web.fetch, web.search))
    t = AssistantTools(db, None, tmp_path, user_text="watch that page")
    web.pages["https://permits.example/172"] = "Permit 26-01234\n1 Application filed"
    out = t.call("watch_for", {"kind": "page", "target": "https://permits.example/172"})
    assert out.startswith("Saved watch #1: the web page https://permits.example/172, when its text changes")
    assert "couldn't open that page" in t.call("watch_for", {"kind": "page", "target": "https://nope.example"})
    assert t.call("watch_for", {"kind": "search", "target": "Diablo 2 ladder"}).startswith("Saved watch #2")
    assert "Google Calendar, 15 min before" in t.call("watch_for", {"kind": "calendar", "lead_minutes": 15})
    assert "needs its web address" in t.call("watch_for", {"kind": "page"})
    listed = t.call("list_watches", {})
    assert "the web page" in listed and "new web results" in listed and "Google Calendar" in listed


def test_a_page_that_refuses_a_direct_read_is_read_through_the_fallback(monkeypatch):
    # 2026-11-03: Justia's docket answered 403 to the fetcher; Tavily's extract returned the page
    import urllib.error

    from core import watches as wmod
    from core import web_tools
    calls = []

    def refuse(url, max_chars=3000, cap=6000):
        calls.append(("fetch", cap))
        raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
    monkeypatch.setattr(web_tools, "fetch_webpage", refuse)
    monkeypatch.setattr(web_tools, "extract_page", lambda url: calls.append(("extract", url)) or "Page: x\n3 Inspection")
    assert wmod._fetch_page("https://permits.example/172") == "Page: x\n3 Inspection"
    assert calls == [("fetch", wmod.PAGE_CHARS), ("extract", "https://permits.example/172")]

    def broken(url, max_chars=3000, cap=6000):
        raise ValueError("only public http(s) web pages can be fetched")
    monkeypatch.setattr(web_tools, "fetch_webpage", broken)
    with pytest.raises(ValueError):                          # not a refusal: no fallback, the reason stands
        wmod._fetch_page("file:///c:/secret")


def test_bad_web_watches_are_refused(web_env):
    _, _, _, _, w = web_env
    for kind, target, why in (("page", "", "needs its web address"), ("search", " ", "needs what to search"),
                              ("rss", "x", "kind 'page'")):
        with pytest.raises(ValueError, match=why):
            w.add_web(kind, target)
    # Too-frequent checks are raised to the kind's minimum (a page every 15 min, a search every hour)
    assert Watches.describe(w.add_web("search", "x", every_minutes=5)).endswith("checked every 1 h")
