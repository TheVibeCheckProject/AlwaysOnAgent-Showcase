"""
Proactiveness: the agent reaches out first (core/proactive.py, docs/PROACTIVE.md)
================================================================================
A fake AI and a fake push stand in for the real ones; the clock is fixed per test.
"""

import datetime
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

from core import proactive as pro
from core.assistant_tools import AssistantTools
from core.database import DatabaseManager


def at(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    # Local wall-clock time with that date's own offset. 2026-11-01: a fixed offset taken from "now" (CST, -6)
    # stamped October dates that were really CDT (-5), so the code's local conversion moved them an hour and
    # the outage test counted one pin run too many on the day daylight saving ended.
    return datetime.datetime(2026, 10, day, hour, minute).astimezone()


def test_the_brief_greeting_comes_from_the_clock():
    # 2026-10-28 03:00 and 11-02 02:03: "Good night, Alex" opened a brief that went on to "still to come today"
    assert [pro.greeting(at(28, h)) for h in (2, 3, 7, 13, 20, 23)] == [
        "Hi", "Hi", "Good morning", "Good afternoon", "Good evening", "Hi"]
    assert "Illinois Lottery" in pro.ROUTINE_MAIL


class FakeLLM:
    """Answers by what the prompt asks for; records every call."""

    def __init__(self, question="What do you usually want help with in the mornings?", notices=None):
        self.calls, self.question, self.notices = [], question, notices or []

    def chat(self, messages, **kw):
        system = messages[0]["content"]
        self.calls.append(system[:40])
        if "start-of-day brief" in system:
            return json.dumps({"brief": "☀️ Quiet day: no schedules and nothing urgent in your email."})
        if "deciding whether to message" in system:
            return json.dumps({"messages": self.notices})
        if "genuinely curious" in system:
            return json.dumps({"question": self.question, "topic": self.question[:20]})
        return "{}"


class Pushes(list):
    def __call__(self, kind, title, body, **kw):
        self.append({"kind": kind, "title": title, "body": body, **kw})
        return 1


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(tmp_path / "state.db")
    with d.get_connection() as c:          # the conversation table memory normally creates
        c.execute("CREATE TABLE IF NOT EXISTS mem_messages (id INTEGER PRIMARY KEY, conv TEXT, role TEXT, "
                  "content TEXT, created_at TEXT, tokens INTEGER)")
    return d


def engine(db, now, llm=None, pushes=None):
    return pro.Proactive(db, llm=llm or FakeLLM(), send_push=pushes if pushes is not None else Pushes(),
                         now=lambda: now)


def owner_says(db, when, text="sure"):
    with db.get_connection() as c:
        c.execute("INSERT INTO mem_messages (conv, role, content, created_at, tokens) VALUES ('c', 'user', ?, ?, 1)",
                  (text, when.isoformat()))


def test_nothing_happens_in_quiet_hours_he_set_or_when_off(db):
    """Quiet hours are off by default (2026-10-27, Alex: no regular sleep pattern, quiet hours don't matter to him);
    any he sets are kept."""
    assert pro.Proactive(db).settings()["quiet_hours"] == "off" and not pro.Proactive(db).quiet(at(5, 3))
    pro.Proactive(db).change(quiet_hours="22:00-08:00")
    llm = FakeLLM()
    assert engine(db, at(5, 23, 30), llm).run_cycle() == []
    assert engine(db, at(5, 7, 59), llm).run_cycle() == []
    pro.Proactive(db).change(quiet_hours="off", enabled=False)
    assert engine(db, at(5, 12), llm).run_cycle() == []
    assert llm.calls == []


class Presence:
    """events.PRESENCE's interface: is the app open, and for how long has it been closed."""

    def __init__(self, open_=False, away=10 ** 6):
        self.open, self.away = open_, away

    def is_open(self):
        return self.open

    def away_seconds(self):
        return 0 if self.open else self.away


def presence_engine(db, now, presence, llm=None, pushes=None):
    e = engine(db, now, llm, pushes)
    e.presence = presence
    return e


def test_the_brief_is_written_ahead_and_sent_when_he_shows_up(db):
    """'auto' (the default): due 18 h after the last; written while he's away, sent the moment he's around."""
    llm, pushes, away = FakeLLM(), Pushes(), Presence(open_=False)
    assert not [m for m in presence_engine(db, at(5, 3), away, llm, pushes).run_cycle() if m["kind"] == "brief"]
    assert any(c.startswith("You write the owner's short start-of-day") for c in llm.calls)   # staged
    staged_calls = len(llm.calls)
    here = Presence(open_=True)
    sent = presence_engine(db, at(5, 4), here, llm, pushes).run_cycle()
    assert [m["kind"] for m in sent][:1] == ["brief"]
    assert sum(c.startswith("You write the owner") for c in llm.calls[staged_calls:]) == 0   # the staged one, no wait
    # not again until BRIEF_EVERY has passed, whatever the clock says
    assert not [m for m in presence_engine(db, at(5, 15), here, llm, pushes).run_cycle() if m["kind"] == "brief"]
    assert [m for m in presence_engine(db, at(5, 23), here, llm, pushes).run_cycle() if m["kind"] == "brief"]


def test_what_isnt_urgent_waits_for_him_and_comes_one_at_a_time(db):
    """2026-10-27: the away shift's open loops asked at 00:47, the research digest went at 02:31. Now deliver() holds
    what isn't urgent until he's around, and the held ones go one per OUTBOX_SPACING (no burst of pushes)."""
    pushes = Pushes()
    away = presence_engine(db, at(5, 1), Presence(open_=False), pushes=pushes)
    held = [away.deliver("notice", "Digest ready", "ai-research"), away.deliver("question", "Did it come through?",
                                                                                 "open-loop")]
    assert all(h["held"] and h["id"] is None for h in held) and pushes == []
    assert away.deliver("notice", "Sam replied", "watch:1", urgent=True)["id"]           # urgent goes now
    pro.Proactive(db).change(brief_time="23:59")          # keep the brief out of this test
    here = Presence(open_=True)
    first = presence_engine(db, at(5, 9), here, pushes=pushes).release_outbox(at(5, 9))
    assert [m["text"] for m in first] == ["Digest ready"]
    assert presence_engine(db, at(5, 9, 20), here).release_outbox(at(5, 9, 20)) == []      # too soon after
    second = presence_engine(db, at(5, 10), here).release_outbox(at(5, 10))
    assert [m["text"] for m in second] == ["Did it come through?"] and pro.Proactive(db).held() == []
    # a held question counts: no second one while it waits
    presence_engine(db, at(6, 1), Presence(open_=False)).deliver("question", "Another?", "x")
    assert not presence_engine(db, at(6, 1), Presence(open_=False))._question_allowed(at(6, 1), [])


def test_a_held_message_too_old_is_dropped_not_sent_stale(db):
    away = presence_engine(db, at(5, 1), Presence(open_=False))
    away.deliver("notice", "Old news", "x")
    assert presence_engine(db, at(10, 9), Presence(open_=True)).release_outbox(at(10, 9)) == []
    assert pro.Proactive(db).held() == []


def test_after_an_outage_it_says_so_and_what_was_missed(db):
    """2026-10-17 23:40 to 10-22 22:07 the Mini was off (a power cut): 10 TikTok runs and 10 pins, silently."""
    from core.schedules import ScheduleStore
    ScheduleStore(db).create("Twice Daily TikTok Affirmation", "video", "0 8,20 * * *")
    ScheduleStore(db).create("Pinterest pins", "pins", "0 12,21 * * *")
    e = engine(db, at(22, 22, 7))
    msg = e.offline_notice(at(17, 23, 40).isoformat())
    assert "I was off from Sat Oct 17 23:40" in msg["text"]
    # Oct 22's 21:00 pins are within MISSED_GRACE of 22:07: the schedule makes that one up, so it isn't "missed"
    assert "Twice Daily TikTok Affirmation ×10" in msg["text"] and "Pinterest pins ×9" in msg["text"]
    assert engine(db, at(22, 22, 7)).offline_notice(at(22, 21, 50).isoformat()) is None    # a restart, not an outage


def test_the_brief_goes_once_a_day_after_its_time_and_the_push_carries_no_text(db):
    pro.Proactive(db).change(brief_time="08:30", brief_to="chat")   # a set time keeps the clock rule; chat: as before
    pushes = Pushes()
    assert not [m for m in engine(db, at(5, 8, 15), pushes=pushes).run_cycle() if m["kind"] == "brief"]
    sent = engine(db, at(5, 8, 45), pushes=pushes).run_cycle()
    assert next(m["kind"] for m in sent) == "brief"
    push = next(p for p in pushes if p["extra"]["kind"] == "brief")      # (an 8:15 question came first)
    assert push["kind"] == "agent_message" and push["title"] == "☀️ Your brief"
    assert "Quiet day" not in push["title"] + push["body"]                      # never the content
    assert push["extra"] == {"message_id": str(sent[0]["id"]), "kind": "brief"} and push["channel"] == "assistant"
    later = engine(db, at(5, 15), pushes=pushes).run_cycle()
    assert not [m for m in later if m["kind"] == "brief"]
    briefs = [m for m in pro.Proactive(db).inbox(after=0) if m["kind"] == "brief"]
    assert len(briefs) == 1 and "Quiet day" in briefs[0]["text"]


def test_the_brief_goes_to_the_today_screen_by_default(db, tmp_path):
    """2026-10-28, Alex: move the brief to Today. It's written as before, but kept for Today's header: not in the
    chat inbox, no push, and still only once a day."""
    from core.today import Today
    pro.Proactive(db).change(brief_time="08:30")
    pushes = Pushes()
    sent = engine(db, at(5, 8, 45), pushes=pushes).run_cycle()
    assert next(m for m in sent if m["kind"] == "brief")["topic"] == "today"
    assert not [p for p in pushes if p["extra"].get("kind") == "brief"]
    assert not [m for m in pro.Proactive(db).inbox(after=0) if m["kind"] == "brief"]
    assert not [m for m in engine(db, at(5, 15), pushes=pushes).run_cycle() if m["kind"] == "brief"]
    header = Today(db, tmp_path, now=lambda: at(5, 16), location=lambda: "").header()
    assert "Quiet day" in header["brief"]["text"]
    assert Today(db, tmp_path, now=lambda: at(6, 9)).header()["brief"] is None        # yesterday's is gone by then
    with pytest.raises(ValueError):
        pro.Proactive(db).change(brief_to="email")


def test_questions_wait_for_an_answer_and_never_repeat(db):
    # Since the candidate pool (1.6) a question goes out at a good moment: a lull while he's been around
    pro.Proactive(db).change(questions_per_day="2")
    llm = FakeLLM()
    owner_says(db, at(5, 8, 40), "morning")
    first = engine(db, at(5, 9), llm).run_cycle()
    assert "question" in [m["kind"] for m in first]
    # 4 hours later but unanswered: no new question
    assert "question" not in [m["kind"] for m in engine(db, at(5, 13), llm).run_cycle()]
    owner_says(db, at(5, 13, 5), "mostly my website and TikTok")
    # answered and he's around, but the AI offers the same question again: refused
    assert "question" not in [m["kind"] for m in engine(db, at(5, 13, 30), llm).run_cycle()]
    llm.question = "Which town should I use for the weather?"
    owner_says(db, at(5, 16, 40))
    assert "question" in [m["kind"] for m in engine(db, at(5, 17), llm).run_cycle()]
    # the daily question cap (2) is now reached
    owner_says(db, at(5, 20, 10))
    llm.question = "Who is Sarah?"
    assert "question" not in [m["kind"] for m in engine(db, at(5, 20, 30), llm).run_cycle()]


def test_a_question_waits_for_a_good_moment(db):
    llm = FakeLLM()
    # never out of the blue: he hasn't been around
    assert "question" not in [m["kind"] for m in engine(db, at(5, 9), llm).run_cycle()]
    # never mid-conversation: he wrote 3 minutes ago
    owner_says(db, at(5, 10, 57), "what's on today?")
    assert "question" not in [m["kind"] for m in engine(db, at(5, 11), llm).run_cycle()]
    # a lull while he's around: now
    sent = engine(db, at(5, 11, 15), llm).run_cycle()
    q = [m for m in sent if m["kind"] == "question"]
    assert q and q[0]["text"] == llm.question
    row = db.get_connection().execute("SELECT candidate_id, subject FROM proactive_messages WHERE id = ?",
                                      (q[0]["id"],)).fetchone()
    assert row["candidate_id"] and pro.Proactive(db).pool.get(row["candidate_id"])["status"] == "sent"


def test_notices_come_from_signals_and_the_cap_holds(db, monkeypatch):
    monkeypatch.setattr(pro.Proactive, "_signals", lambda self, now: [{"type": "renewal", "detail": "GoDaddy renews Oct 7"}])
    llm = FakeLLM(notices=[{"kind": "notice", "text": "Heads up: GoDaddy renews Oct 7.", "topic": "godaddy"},
                           {"kind": "suggestion", "text": "Want me to list your renewals each week?", "topic": "renewals"},
                           {"kind": "notice", "text": "A third one", "topic": "x"}])
    pro.Proactive(db).change(daily_cap="3", questions_per_day="0")
    sent = engine(db, at(5, 10), llm).run_cycle()
    assert [m["kind"] for m in sent] == ["brief", "notice", "suggestion"]       # at most 2 notices per cycle
    assert engine(db, at(5, 11), llm).run_cycle() == []                         # cap of 3 reached


def test_no_signals_means_no_deciding_call(db, monkeypatch):
    monkeypatch.setattr(pro.Proactive, "_signals", lambda self, now: [])
    llm = FakeLLM()
    pro.Proactive(db).change(questions_per_day="0")
    engine(db, at(5, 7, 0), llm)            # (quiet) nothing
    engine(db, at(5, 10), llm).run_cycle()
    assert not any("deciding" in c for c in llm.calls)


def test_settings_through_the_chat_tool(db, tmp_path):
    tools = AssistantTools(db, None, tmp_path, user_text="send the brief at 7 and no questions")
    out = tools.call("proactive_settings", {"brief_time": "07:00", "questions_per_day": 0})
    assert "brief at 07:00" in out and "0 of them questions" in out
    assert "Error" in tools.call("proactive_settings", {"quiet_hours": "late"})
    assert "Error" in tools.call("proactive_settings", {"daily_cap": 99})


def test_renewal_dates_parse():
    assert pro._parse_day("October 12, 2026") == datetime.date(2026, 10, 12)
    assert pro._parse_day("Oct. 22, 2026") == datetime.date(2026, 10, 22)
    assert pro._parse_day("10/12/2026") == datetime.date(2026, 10, 12)
    assert pro._parse_day("soon") is None


def test_the_app_fetches_messages_from_the_inbox(db, monkeypatch):
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    p = pro.Proactive(db)
    first = p.deliver("question", "What should I call you?", "name")
    p.deliver("notice", "Your post went up.", "post")
    original = hud_state.db
    hud_state.set_db(db)
    try:
        client = client_from(hud_app, addr=LOCAL_ADDR)
        all_ = client.get("/api/inbox").json()
        assert [m["kind"] for m in all_["messages"]] == ["question", "notice"]
        newer = client.get(f"/api/inbox?after={first['id']}").json()
        assert [m["text"] for m in newer["messages"]] == ["Your post went up."] and newer["last_id"] == first["id"] + 1
        assert client.get(f"/api/inbox?after={newer['last_id']}").json() == {"messages": [], "last_id": newer["last_id"]}
    finally:
        hud_state.set_db(original)



class CuriousMemory:
    """Just enough of the memory for the curiosity step: facts and a profile."""
    def __init__(self, facts):
        self.facts = facts

    def _fact_rows(self):
        return [{"content": f} for f in self.facts]

    def get_profile(self):
        return ""

    def add_message(self, role, text):
        pass


class CuriousLLM:
    def __init__(self, answer, share=None):
        self.answer, self.share, self.prompts = answer, share, []

    def chat(self, messages, **kw):
        system = messages[0]["content"]
        self.prompts.append((system, messages[1]["content"]))
        if "genuinely curious" in system:
            return json.dumps(self.answer)
        if "looked it up" in system:
            return json.dumps(self.share or {})
        return "{}"


FACTS = ["Alex plays golf at Maple Ridge Golf Course, Sage Meadows, and Lakeside Country Club.",
         "Alex likes to watch DIY YouTube channels such as Styropyro.",
         "Alex hikes trails with his dad at the Oak County cabin.", "Alex lives in Springfield, Illinois.",
         "Alex runs Example Site.", "Alex's brother lives in New Mexico."]


def curious(db, llm, research=None):
    p = pro.Proactive(db, memory=CuriousMemory(FACTS), llm=llm, send_push=Pushes(), now=lambda: at(6, 12))
    p.research = research
    p.change(brief_time="23:59")
    owner_says(db, at(6, 11, 40), "hey")            # he's around, and it's been quiet for 20 minutes: a good moment
    return p


def test_curiosity_starts_from_something_the_owner_said(db):
    llm = CuriousLLM({"question": "What got you into Styropyro's videos, the lasers or the builds?",
                      "about": "DIY YouTube channels such as Styropyro", "kind": "why", "look_up": "", "topic": "styropyro"})
    sent = curious(db, llm).run_cycle()
    assert [m["text"] for m in sent] == ["What got you into Styropyro's videos, the lasers or the builds?"]
    prompt = next(u for s, u in llm.prompts if "genuinely curious" in s)
    assert "Styropyro" in prompt and "The owner's messages this week" in prompt


@pytest.mark.parametrize("about", ["Alex collects vintage cameras", ""])
def test_curiosity_about_nothing_he_said_is_dropped(db, about):
    llm = CuriousLLM({"question": "What are your goals this year?", "about": about, "kind": "wonder", "look_up": ""})
    assert curious(db, llm).run_cycle() == []          # made up, or generic once the agent knows him


def test_curiosity_can_look_something_up_and_share_it(db):
    queries = []
    def research(q):
        queries.append(q)
        return [{"title": "Maple Ridge Golf Course - Springfield", "url": "https://example.org/north-hills",
                 "snippet": "Maple Ridge opened in 1961 and its 9th hole crosses the creek twice."}]
    llm = CuriousLLM({"question": "Do you have a favorite hole at Maple Ridge?", "about": "Maple Ridge Golf Course",
                      "kind": "wonder", "look_up": "Maple Ridge Golf Course Springfield history", "topic": "golf"},
                     share={"text": "I read Maple Ridge opened in 1961 and its 9th crosses the creek twice. Is that "
                                    "one your favorite or your nemesis?", "url": "https://example.org/north-hills"})
    sent = curious(db, llm, research).run_cycle()
    assert queries == ["Maple Ridge Golf Course Springfield history"]
    assert sent[0]["kind"] == "question" and "opened in 1961" in sent[0]["text"]
    assert sent[0]["text"].endswith("(https://example.org/north-hills)")


def test_a_failed_look_up_still_asks(db):
    def research(q):
        raise RuntimeError("offline")
    llm = CuriousLLM({"question": "Do you have a favorite hole at Maple Ridge?", "about": "Maple Ridge Golf Course",
                      "kind": "wonder", "look_up": "Maple Ridge Golf Course Springfield"})
    assert [m["text"] for m in curious(db, llm, research).run_cycle()] == ["Do you have a favorite hole at Maple Ridge?"]


def test_what_sparked_it_may_be_worded_a_little_differently(db):
    llm = CuriousLLM({"question": "How did you and your dad find the Oak County cabin?",
                      "about": "hike trails with his dad at the Oak County cabin", "kind": "why", "look_up": ""})
    assert len(curious(db, llm).run_cycle()) == 1


def test_curiosity_moves_on_to_something_else(db):
    llm = CuriousLLM({"question": "What got you into Styropyro?", "about": "Styropyro", "kind": "why", "look_up": "",
                      "topic": "styropyro"})
    assert len(curious(db, llm).run_cycle()) == 1
    owner_says(db, at(6, 12, 30), "the lasers, honestly")
    llm.answer = {"question": "Which Styropyro build is your favorite?", "about": "Styropyro", "kind": "follow_up",
                  "look_up": "", "topic": "styropyro"}
    owner_says(db, at(6, 17, 40), "back")             # around again: only the repeat can stop it
    later = pro.Proactive(db, memory=CuriousMemory(FACTS), llm=llm, send_push=Pushes(), now=lambda: at(6, 18))
    assert later.run_cycle() == []                     # the same subject again so soon: dropped
    assert later.pool.pending() == []


def test_the_same_subject_under_another_name_is_caught(db):
    llm = CuriousLLM({"question": "What got you into Styropyro?", "about": "DIY YouTube channels such as Styropyro",
                      "kind": "why", "look_up": "", "topic": "styropyro"})
    assert len(curious(db, llm).run_cycle()) == 1
    owner_says(db, at(6, 12, 30), "the lasers, honestly")
    llm.answer = {"question": "Which Styropyro laser build is the most fun to watch?", "about": "Styropyro",
                  "kind": "follow_up", "look_up": "", "topic": "diy"}
    owner_says(db, at(6, 17, 40), "back")
    later = pro.Proactive(db, memory=CuriousMemory(FACTS), llm=llm, send_push=Pushes(), now=lambda: at(6, 18))
    assert later.run_cycle() == []
    assert later.pool.pending() == []


def test_a_name_is_matched_whole():
    facts = FACTS
    assert not pro.Proactive._same_subject_lately("Example Site", facts,
                                                  ["What footage do you catch when you check your trail cam?"])
    assert pro.Proactive._same_subject_lately("Example Site", facts,
                                              ["How did Example Site start?"])
    assert not pro.Proactive._same_subject_lately("Alex is using Claude to help", facts, ["Is Alex using the cabin?"])


def test_a_subject_asked_about_long_ago_is_not_asked_again(db):
    # Styropyro was asked on Oct 11; the 2026-10-13 samples asked again because only the last 3 were checked
    llm = CuriousLLM({"question": "What got you into Styropyro?", "about": "DIY YouTube channels such as Styropyro",
                      "kind": "why", "look_up": "", "topic": "lasers"})
    engine_ = curious(db, llm)                      # makes the proactive_messages table
    with db.get_connection() as c:
        c.execute("INSERT INTO proactive_messages (kind, text, topic, created_at) VALUES ('question', ?, 'diy', ?)",
                  ("When you are watching videos from channels like Styropyro, do you have a favorite type?",
                   at(1, 9).isoformat()))
        for n in range(5):
            c.execute("INSERT INTO proactive_messages (kind, text, topic, created_at) VALUES ('question', ?, ?, ?)",
                      (f"Unrelated question number {n}?", f"t{n}", at(2 + n, 9).isoformat()))
    owner_says(db, at(6, 11), "ok")
    assert engine_.run_cycle() == []


def test_proactive_open_loop_and_memory_check_logs_reach_the_daemons_log():
    # As "Proactive", "OpenLoops" and "MemoryReview" their lines never reached logs/worker.log (found 2026-10-13)
    import logging

    from core import memory_review, open_loops

    got = []

    class Catch(logging.Handler):
        def emit(self, record):
            got.append(record.getMessage())

    parent = logging.getLogger("AlwaysOnAgent")
    handler, old_level = Catch(), parent.level
    parent.addHandler(handler)
    parent.setLevel(logging.INFO)
    try:
        for mod in (pro, open_loops, memory_review):
            mod.logger.info(f"hello from {mod.__name__}")
    finally:
        parent.removeHandler(handler)
        parent.setLevel(old_level)
    assert got == [f"hello from {m}" for m in ("core.proactive", "core.open_loops", "core.memory_review")]


# ---------------- the candidate pool in the cycle (docs/PROACTIVE_RESEARCH.md 1b, 1n; checklist 1.6) ----------------

class RecordingLLM(FakeLLM):
    """FakeLLM that keeps every prompt, and counts ranking calls."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.prompts, self.ranked = [], 0

    def chat(self, messages, **kw):
        self.prompts.append((messages[0]["content"], messages[-1]["content"]))
        if messages[0]["content"].startswith("You choose what a personal assistant says"):
            self.ranked += 1
            return json.dumps({"order": [x["id"] for x in json.loads(messages[1]["content"])]})
        return super().chat(messages, **kw)


def test_todays_candidates_ride_in_the_brief(db):
    llm = RecordingLLM()
    p = engine(db, at(5, 8, 45), llm)
    cid = p.pool.add("interest", "notice", "Styropyro posted a new video last night.", urgency="today",
                     topic="Styropyro's videos")
    sent = p.run_cycle()
    brief = next(m for m in sent if m["kind"] == "brief")
    data = next(u for s, u in llm.prompts if "start-of-day brief" in s)
    assert "worth_mentioning_today" in data and "Styropyro posted a new video" in data
    c = p.pool.get(cid)
    assert c["status"] == "sent" and c["message_id"] == brief["id"]
    assert not [m for m in sent if m["text"] == "Styropyro posted a new video last night."]     # not a message of its own


def test_a_candidate_he_talked_about_since_is_dropped_before_sending(db):
    llm = RecordingLLM()
    p = engine(db, at(6, 12), llm)
    p.change(brief_time="23:59")
    cid = p.pool.add("curiosity", "question", "What do you usually see on the BirdBox camera?",
                     topic="BirdBox camera footage at the cabin")
    p.pool.set_score(cid, 0.9, {"rank": 1.0})
    db.set_state(pro.STATE_RANKED_UPTO, str(cid))               # already ranked
    owner_says(db, at(6, 11, 40), "got a great fox on the BirdBox camera last night")
    sent = p.run_cycle()
    assert not [m for m in sent if m["kind"] == "question"]
    c = p.pool.get(cid)
    assert c["status"] == "dropped" and c["reason"].startswith("discussed lately")


def test_the_bar_drops_with_silence_but_not_below_the_floor(db):
    from core.candidates import THRESHOLD
    p = engine(db, at(6, 12))
    assert p.threshold(at(6, 12)) == THRESHOLD                  # nothing sent yet: the plain bar
    p.deliver("question", "An earlier question?")               # sent "now" (12:00)
    assert pro.Proactive(db, now=lambda: at(6, 18, 30)).threshold(at(6, 18, 30)) == round(THRESHOLD - pro.THRESHOLD_STEP, 3)
    assert pro.Proactive(db, now=lambda: at(9, 12)).threshold(at(9, 12)) == pro.THRESHOLD_FLOOR


def test_the_ranker_is_asked_only_when_the_pool_has_something_new(db):
    llm = RecordingLLM()
    owner_says(db, at(6, 11, 40), "hey")
    p = engine(db, at(6, 12), llm)
    p.change(brief_time="23:59", questions_per_day="0")        # no new curious questions in this test
    a = p.pool.add("curiosity", "question", "What do you catch on the trail cam?", topic="trail cam footage")
    p.run_cycle()
    assert llm.ranked == 1 and p.pool.get(a)["score"] is not None
    engine(db, at(6, 12, 30), llm).run_cycle()
    assert llm.ranked == 1                                      # nothing new: the stored scores are used
    engine(db, at(6, 13), llm).pool.add("interest", "notice", "Styropyro posted a new video.", topic="Styropyro")
    engine(db, at(6, 13), llm).run_cycle()
    assert llm.ranked == 2


def test_time_sensitive_notices_go_even_when_he_is_not_around(db, monkeypatch):
    monkeypatch.setattr(pro.Proactive, "_signals", lambda self, now: [{"type": "renewal", "detail": "GoDaddy renews Oct 7"}])
    llm = FakeLLM(notices=[{"kind": "notice", "text": "Heads up: GoDaddy renews Oct 7.", "topic": "godaddy"}])
    sent = engine(db, at(5, 10), llm).run_cycle()
    note = [m for m in sent if m["kind"] == "notice"]
    assert note and note[0]["text"] == "Heads up: GoDaddy renews Oct 7."
    row = db.get_connection().execute("SELECT candidate_id FROM proactive_messages WHERE id = ?", (note[0]["id"],)).fetchone()
    assert pro.Proactive(db).pool.get(row["candidate_id"])["source"] == "signal"


# ---------------- silence check-in and back-off (docs/PROACTIVE_RESEARCH.md 1k; checklist 1.7) ----------------

def test_silence_checkin_fires_after_six_hours_of_silence(db):
    llm = RecordingLLM()
    owner_says(db, at(5, 9), "good morning")
    p = engine(db, at(5, 14), llm)
    p.change(brief_time="23:59")
    cid = p.pool.add("curiosity", "question", "What do you catch on the trail cam?",
                     topic="trail cam footage at the cabin")
    p.pool.set_score(cid, 0.9, {"rank": 1.0})
    db.set_state(pro.STATE_RANKED_UPTO, str(cid))

    # 5 hours of silence: not yet (needs 6 hours; not a good moment either)
    assert p.run_cycle() == []
    assert db.get_state(pro.STATE_SILENCE_LEVEL) in (None, "", "0")

    # 6 hours of silence: check-in fires from the pool
    p6 = engine(db, at(5, 15), llm)
    p6.change(brief_time="23:59")
    sent = p6.run_cycle()
    assert len(sent) == 1 and sent[0]["text"] == "What do you catch on the trail cam?"
    assert p.pool.get(cid)["status"] == "sent"
    assert db.get_state(pro.STATE_SILENCE_LEVEL) == "1"
    assert db.get_state(pro.STATE_SILENCE_CHECKIN_AT) == at(5, 15).isoformat()


def test_silence_checkin_backs_off_to_12_then_24_hours_when_ignored(db):
    owner_says(db, at(5, 9), "hello")
    p = engine(db, at(5, 15))
    p.change(brief_time="23:59")
    c1 = p.pool.add("curiosity", "question", "Question 1?", topic="cabin")
    p.pool.set_score(c1, 0.9)
    db.set_state(pro.STATE_RANKED_UPTO, str(c1))
    sent1 = p.run_cycle()
    assert len(sent1) == 1 and db.get_state(pro.STATE_SILENCE_LEVEL) == "1"

    # Add second candidate for next check-in
    c2 = p.pool.add("interest", "notice", "Notice 2.", topic="laser")
    p.pool.set_score(c2, 0.9)
    db.set_state(pro.STATE_RANKED_UPTO, str(c2))

    # 6 hours later (21:00) with no reply: ignored, back-off requires 12 hours from 15:00
    p_early = engine(db, at(5, 21))
    p_early.change(brief_time="23:59")
    assert p_early.run_cycle() == []

    # 17.5 hours later (08:30 next day, >= 12h, outside quiet hours): second check-in fires
    p_12h = engine(db, at(6, 8, 30))
    p_12h.change(brief_time="23:59")
    sent2 = p_12h.run_cycle()
    assert len(sent2) == 1 and sent2[0]["text"] == "Notice 2."
    assert db.get_state(pro.STATE_SILENCE_LEVEL) == "2"
    assert db.get_state(pro.STATE_SILENCE_CHECKIN_AT) == at(6, 8, 30).isoformat()

    # Add third candidate
    c3 = p.pool.add("interest", "notice", "Notice 3.", topic="golf")
    p.pool.set_score(c3, 0.9)
    db.set_state(pro.STATE_RANKED_UPTO, str(c3))

    # 10 hours later (18:30) with no reply: level 2 requires 24 hours from 08:30
    p_early2 = engine(db, at(6, 18, 30))
    p_early2.change(brief_time="23:59")
    assert p_early2.run_cycle() == []

    # 24 hours later (08:30 day 7): third check-in fires
    p_24h = engine(db, at(7, 8, 30))
    p_24h.change(brief_time="23:59")
    sent3 = p_24h.run_cycle()
    assert len(sent3) == 1 and sent3[0]["text"] == "Notice 3."
    assert db.get_state(pro.STATE_SILENCE_LEVEL) == "3"


def test_any_message_from_alex_resets_the_silence_backoff(db):
    owner_says(db, at(5, 9), "start")
    p = engine(db, at(5, 15))
    p.change(brief_time="23:59")
    c1 = p.pool.add("interest", "notice", "Notice 1.", topic="topic1")
    p.pool.set_score(c1, 0.9)
    db.set_state(pro.STATE_RANKED_UPTO, str(c1))
    assert len(p.run_cycle()) == 1
    assert db.get_state(pro.STATE_SILENCE_LEVEL) == "1"

    # Alex sends a message at 16:00
    owner_says(db, at(5, 16), "busy today")
    assert pro.Proactive(db)._silence_level() == 0

    c2 = p.pool.add("interest", "notice", "Notice 2.", topic="topic2")
    p.pool.set_score(c2, 0.9)
    db.set_state(pro.STATE_RANKED_UPTO, str(c2))

    # 4 hours after Alex's message (20:00): not yet due (needs 6 hours from 16:00)
    p_4h = engine(db, at(5, 20))
    p_4h.change(brief_time="23:59")
    assert p_4h.run_cycle() == []

    # Next morning at 08:30 (> 6 hours after Alex's message): fires with level reset to 0
    p_next = engine(db, at(6, 8, 30))
    p_next.change(brief_time="23:59")
    sent = p_next.run_cycle()
    assert len(sent) == 1 and sent[0]["text"] == "Notice 2."
    assert db.get_state(pro.STATE_SILENCE_LEVEL) == "1"


def test_silence_checkin_never_fires_during_quiet_hours(db):
    pro.Proactive(db).change(quiet_hours="22:00-08:00")    # off by default since 2026-10-27; kept when set
    owner_says(db, at(5, 17), "later")
    p_quiet = engine(db, at(5, 23))
    cid = p_quiet.pool.add("interest", "notice", "Night news.", topic="news")
    p_quiet.pool.set_score(cid, 0.9)
    db.set_state(pro.STATE_RANKED_UPTO, str(cid))

    # 6 hours of silence reached at 23:00, but it is quiet hours (22:00-08:00)
    assert p_quiet.run_cycle() == []

    # In the morning outside quiet hours: fires
    p_morning = engine(db, at(6, 8, 30))
    p_morning.change(brief_time="23:59")
    sent = p_morning.run_cycle()
    assert len(sent) == 1 and sent[0]["text"] == "Night news."


def test_silence_checkin_never_sends_when_pool_is_empty(db):
    owner_says(db, at(5, 9), "hey")
    # 8 hours of silence, but nothing in pool
    p = engine(db, at(5, 17))
    p.change(brief_time="23:59", questions_per_day="0")
    assert p.run_cycle() == []
    assert db.get_state(pro.STATE_SILENCE_LEVEL) in (None, "", "0")
    assert not db.get_state(pro.STATE_SILENCE_CHECKIN_AT)


def test_silence_checkin_can_ask_even_if_previous_question_unanswered(db):
    # A question was sent at 08:00, Alex never answered it
    p_early = engine(db, at(5, 8))
    p_early.deliver("question", "Unanswered question from morning?")

    # Alex spoke at 07:30
    owner_says(db, at(5, 7, 30), "good morning")

    # At 14:00 (6.5 hours of silence): pool has a question candidate
    p = engine(db, at(5, 14))
    p.change(brief_time="23:59")
    cid = p.pool.add("curiosity", "question", "A new check-in question?", topic="stealth")
    p.pool.set_score(cid, 0.9)
    db.set_state(pro.STATE_RANKED_UPTO, str(cid))

    # Ordinary question check fails because morning question was unanswered
    assert not p._question_allowed(at(5, 14), p._rows(at(5, 0)))

    # But silence check-in allows asking:
    sent = p.run_cycle()
    assert len(sent) == 1 and sent[0]["text"] == "A new check-in question?"
    assert db.get_state(pro.STATE_SILENCE_LEVEL) == "1"


# ---------------- implicit feedback: back-off and source threshold (1e; checklist 1.8) ----------------

def test_two_ignored_questions_halves_questions_per_day_until_alex_replies(db):
    p = engine(db, at(6, 12))
    p.change(questions_per_day="4")
    assert p._effective_questions_per_day(at(6, 12)) == 4

    # Send first question and mark it ignored
    c1 = p.pool.add("curiosity", "question", "Question 1?", topic="t1")
    p.pool.mark_sent(c1, message_id=1)
    p.pool.set_outcome(c1, "ignored")
    assert p._effective_questions_per_day(at(6, 12)) == 4

    # Send second question and mark it ignored (two in a row!)
    c2 = p.pool.add("curiosity", "question", "Question 2?", topic="t2")
    p.pool.mark_sent(c2, message_id=2)
    p.pool.set_outcome(c2, "ignored")
    assert p._consecutive_ignored_questions() == 2
    assert p._effective_questions_per_day(at(6, 12)) == 2        # 4 halved to 2

    # Two questions already asked today: blocked under halved limit
    today = [{"kind": "question"}, {"kind": "question"}]
    assert not p._question_allowed(at(6, 12), today)

    # Alex replies in chat: halving is lifted
    owner_says(db, at(6, 13), "hey there")
    assert p._consecutive_ignored_questions() == 0
    assert p._effective_questions_per_day(at(6, 13)) == 4


def test_threshold_rises_when_recent_source_messages_ignored(db):
    p = engine(db, at(6, 12))
    base = p.threshold(at(6, 12))

    # Add ignored curiosity messages to lower curiosity reply rate
    for i in range(3):
        cid = p.pool.add("curiosity", "question", f"Q{i}?", topic=f"t{i}")
        p.pool.mark_sent(cid, message_id=10 + i)
        p.pool.set_outcome(cid, "ignored")

    # The threshold for curiosity rises above base
    bar_curiosity = p.threshold(at(6, 12), source="curiosity")
    assert bar_curiosity > base

    # An untouched source stays at base
    assert p.threshold(at(6, 12), source="interest") == base


# ---------------- curate and per-kind switches; new defaults (1d; checklist 1.12) ----------------

def test_new_defaults_and_switches(db):
    p = pro.Proactive(db)
    s = p.settings()
    assert s["daily_cap"] == "10"
    assert s["questions_per_day"] == "4"
    assert s["open_loops"] == "1"
    assert s["curiosity"] == "1"
    assert s["research"] == "1"
    assert s["patterns"] == "1"

    p.change(open_loops=False, curiosity="0", research="off", patterns=0)
    assert not p.switch("open_loops")
    assert not p.switch("curiosity")
    assert not p.switch("research")
    assert not p.switch("patterns")
    assert not p.switch_for_source("open_loop")
    assert not p.switch_for_source("curiosity")
    assert not p.switch_for_source("research")
    assert not p.switch_for_source("pattern")
    assert p.switch_for_source("signal")  # unaffected


def test_switch_controls_sources_and_cycle(db):
    llm = FakeLLM()
    p = engine(db, at(6, 12), llm)
    p.change(curiosity=False)

    owner_says(db, at(6, 11, 40), "hello")
    # Curiosity is off: no curious questions created in run_cycle
    sent = p.run_cycle()
    assert not any(m["kind"] == "question" for m in sent)

    # Re-enable curiosity: question can be produced
    p.change(curiosity=True)
    sent2 = p.run_cycle()
    assert any(m["kind"] == "question" for m in sent2)


def test_curate_interests_and_exclusions(db):
    p = pro.Proactive(db)
    assert p.interests() == []
    assert p.exclusions() == []

    p.add_interest("new Styropyro videos")
    p.add_interest("new Styropyro videos")  # de-dupe
    assert p.interests() == ["new Styropyro videos"]

    p.add_exclusion("golf")
    p.add_exclusion("golf")
    assert p.exclusions() == ["golf"]

    assert p.is_excluded("When you are working on your golf game...")
    assert p.is_excluded("Let's talk about GOLF today")
    assert not p.is_excluded("Let's talk about photography")

    p.remove_interest("new Styropyro videos")
    p.remove_exclusion("golf")
    assert p.interests() == []
    assert p.exclusions() == []
    assert not p.is_excluded("When you are working on your golf game...")


def test_exclusion_drops_candidate_in_fresh_check(db):
    p = pro.Proactive(db)
    p.add_exclusion("golf")
    cid = p.pool.add("curiosity", "question", "Do you prefer the driving range or the golf course?", topic="golf game")
    c = p.pool.get(cid)
    assert p._still_fresh(c) == "excluded by owner preference"

    # Switch off also drops/marks not fresh
    p.remove_exclusion("golf")
    assert p._still_fresh(c) is None
    p.change(curiosity=False)
    assert p._still_fresh(c) == "switch for source 'curiosity' is off"


def test_research_switch_disables_lookup_sharing(db):
    p = pro.Proactive(db)
    p.research = lambda q: [{"title": "Styropyro laser", "url": "https://example.com/laser", "snippet": "High power laser"}]
    p.change(research=False)
    # When research switch is off, _share returns None without searching
    assert p._share("What kind of laser?", "laser video", "styropyro laser") is None


# ---------------- grounded notices and suggestions; guardrails (1g, 1h; checklist 1.13) ----------------

def test_decide_grounds_named_signals_and_drops_hallucinated_ones(db):
    p = pro.Proactive(db)
    signals = [{"type": "renewal", "detail": "GoDaddy renews Oct 7"}]
    dated_facts = ["Flight to Chicago on Oct 20"]
    owner_recent = ["I need to finish the client video"]

    # Valid named signal from signals
    m_valid = {"kind": "notice", "text": "GoDaddy renews Oct 7.", "topic": "godaddy", "signal": "GoDaddy renews Oct 7"}
    assert p._signal_grounded(m_valid, signals, dated_facts, owner_recent)

    # Valid named signal from dated facts
    m_fact = {"kind": "notice", "text": "Your flight is on Oct 20.", "topic": "travel", "signal": "Flight to Chicago on Oct 20"}
    assert p._signal_grounded(m_fact, signals, dated_facts, owner_recent)

    # Hallucinated named signal (does not exist in signals/facts)
    m_fake = {"kind": "notice", "text": "Package arriving today.", "topic": "package", "signal": "Amazon package delivered"}
    assert not p._signal_grounded(m_fake, signals, dated_facts, owner_recent)


def test_decide_and_question_guardrails_drop_engagement_and_sensitive_topics(db):
    p = pro.Proactive(db)

    # 1. Engagement bait dropped
    assert p._violates_guardrails("We miss you! Come back and chat.") is not None
    assert p._violates_guardrails("Don't leave, we have more ideas!") is not None

    # 2. Sensitive topics (health, salary) dropped when not raised by owner
    assert p._violates_guardrails("How is your health doing today?", recent_owner_text="busy day at work") is not None
    assert p._violates_guardrails("Have you checked your salary deposit?", recent_owner_text="busy day at work") is not None

    # 3. Sensitive topic allowed when owner explicitly raised it this week
    assert p._violates_guardrails("Hope your doctor appointment went well.", recent_owner_text="I have a doctor visit Tuesday") is None

    # 4. Normal topic has no violation
    assert p._violates_guardrails("Heads up: GoDaddy renews Friday.", recent_owner_text="routine update") is None


def test_search_blocks_personal_details():
    # Email addresses and phone numbers blocked by guardrails
    assert not pro.Proactive._search_query_safe("Alex Rivera owner@example.com")
    assert not pro.Proactive._search_query_safe("Call support at 555-123-4567")
    # General queries safe
    assert pro.Proactive._search_query_safe("Styropyro laser videos")
    assert pro.Proactive._search_query_safe("GoDaddy domain renewal policies")


def test_decide_honors_exclusions(db):
    p = pro.Proactive(db)
    p.add_exclusion("GoDaddy")
    signals = [{"type": "renewal", "detail": "GoDaddy renews Oct 7"}]

    # In _decide, even if model suggests a notice for GoDaddy, it is dropped by exclusion
    llm = FakeLLM(notices=[{"kind": "notice", "text": "GoDaddy renews Oct 7.", "topic": "godaddy", "signal": "GoDaddy renews Oct 7"}])
    p.llm = llm
    out = p._decide(signals, [], room=2, now=at(5, 10))
    assert out == []


# ---------------- inbox why, feedback endpoint, candidate inspection (checklist 1.14) ----------------

def test_inbox_why_and_feedback_and_candidates_endpoints(db):
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from

    p = pro.Proactive(db)
    cid = p.pool.add("curiosity", "question", "What laser project did you start?", topic="lasers")
    msg = p.deliver("question", "What laser project did you start?", topic="lasers", why="Because you mentioned laser hobby",
                    subject="lasers", candidate_id=cid)

    original = hud_state.db
    hud_state.set_db(db)
    try:
        client = client_from(hud_app, addr=LOCAL_ADDR)

        # 1. GET /api/inbox includes why
        res = client.get("/api/inbox").json()
        assert any(m["id"] == msg["id"] and m.get("why") == "Because you mentioned laser hobby" for m in res["messages"])

        # 2. POST /api/inbox/{id}/feedback records reaction
        fb = client.post(f"/api/inbox/{msg['id']}/feedback", json={"reaction": "less_like_this", "reason": "not interested"})
        assert fb.status_code == 200
        assert fb.json()["status"] == "ok"
        assert fb.json()["reaction"] == "less_like_this"

        # Check candidate updated and exclusion added
        assert p.pool.get(cid)["outcome"] == "less_like_this"
        assert p.is_excluded("lasers")

        # 404 on missing message
        fb_missing = client.post("/api/inbox/99999/feedback", json={"reaction": "thumbs_up"})
        assert fb_missing.status_code == 404

        # 3. GET /api/proactive/candidates
        cand_res = client.get("/api/proactive/candidates").json()
        assert "candidates" in cand_res and "counts" in cand_res
        assert any(c["id"] == cid for c in cand_res["candidates"])
    finally:
        hud_state.set_db(original)





