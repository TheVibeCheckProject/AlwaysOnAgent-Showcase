"""
Open loops (core/open_loops.py): memories waiting on something are tagged, checked against what came after, replaced
only when a quote backs it and a second AI agrees, and asked about once when nothing settles them. The case that
started it: five "waiting on the permit decision" facts beside a newer "permit approved" one.
"""

import datetime
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import open_loops as ol
from core.database import DatabaseManager
from core.memory import MemoryManager
from tests.test_memory import FakeEmbedder
from tests.test_memory import FakeLLM as MemoryLLM

TZ = datetime.datetime.now().astimezone().tzinfo
WAITING = "Alex applied for a building permit and is waiting for it to be approved."
GRANTED = "Alex's permit application was approved on October 5, 2026, so construction can start."


class LoopLLM(MemoryLLM):
    """The memory's scripted answers, plus tagging (self.tag) and checking (self.check)."""

    def __init__(self):
        super().__init__()
        self.tag = {"open": []}
        self.check = {"result": "nothing"}

    def chat(self, messages, **kw):
        system = messages[0]["content"]
        if system.startswith("You read a personal assistant's saved memories"):
            self.calls.append(messages)
            return json.dumps(self.tag)
        if system.startswith("A personal assistant saved a memory"):
            self.calls.append(messages)
            return json.dumps(self.check)
        return super().chat(messages)


class Delivered(list):
    """Like proactive.deliver: the message also joins the conversation, so a reply has its context."""
    memory = None

    def deliver(self, kind, text, topic="", canvas=None):
        self.append({"kind": kind, "text": text, "topic": topic})
        if self.memory is not None:
            self.memory.add_message("assistant", text)
        return {"id": len(self)}


class Mail:
    def __init__(self, emails=None, fail=False):
        self.emails, self.fail, self.queries = emails or [], fail, []

    def search(self, query, limit=10):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("no gmail")
        return {"emails": self.emails}


class Research:
    """The web search: scripted results (with page text), and the queries it was given."""
    def __init__(self, results=None, fail=False):
        self.results, self.fail, self.queries = results or [], fail, []

    def __call__(self, query):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("offline")
        return self.results


PERMIT_PAGE = {"title": "Permit 26-01234, 12 Elm Street - City Permit Portal",
          "url": "https://permits.example.org/record/10000001/12-elm-street/",
          "snippet": "Permit record for 12 Elm Street, 26-01234, applied June 17, 2026.",
          "page": "Oct 5, 2026 4 Decision on Application for a Building Permit. "
                  "Sep 15, 2026 3 Plans revised. Jun 17, 2026 1 APPLICATION for a building permit"}


def now():
    return datetime.datetime.now(TZ)


@pytest.fixture
def make(tmp_path):
    def build(mail=None, research=None):
        llm = LoopLLM()
        mem = MemoryManager(DatabaseManager(tmp_path / "state.db"), llm=llm, embedder=FakeEmbedder())
        sent = Delivered()
        sent.memory = mem
        return mem, llm, sent, ol.OpenLoops(mem.db, mem, sent, mail if mail is not None else Mail(),
                                            research if research is not None else Research())
    return build


def loop_row(mem, fid):
    r = mem.db.get_connection().execute("SELECT * FROM mem_open_loops WHERE fact_id = ?", (fid,)).fetchone()
    return dict(r) if r else None


def test_tagging_marks_only_what_resolves_and_never_asks_twice(make):
    mem, llm, _, loops = make()
    waiting = mem.add_fact(WAITING, "plan")[0]
    golf = mem.add_fact("Alex plays golf at Maple Ridge Golf Course.", "preference")[0]
    project = mem.add_fact("Alex runs Example Site.", "project")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the city's decision on his permit application",
                         "check_after": "2026-10-20", "mail": "permit decision"}, {"id": 999, "waiting_on": "x"}]}
    assert loops.tag_new(now()) == 1
    marked = llm.calls[-1][1]["content"].split("Memories to mark:\n")[1].split("\n\n")[0]
    sent_ids = {m["id"] for m in json.loads(marked)}
    assert sent_ids == {waiting, project}                      # preferences never resolve: not sent
    assert loop_row(mem, waiting)["status"] == "open" and loop_row(mem, waiting)["check_after"] == "2026-10-20"
    assert loop_row(mem, project)["status"] == "none" and loop_row(mem, golf) is None
    calls = len(llm.calls)
    assert loops.tag_new(now()) == 0 and len(llm.calls) == calls     # looked at once


def test_a_newer_fact_settles_it_without_a_copy(make):
    mem, llm, sent, loops = make()
    waiting = mem.add_fact(WAITING, "fact")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the permit decision", "check_after": "2026-10-09", "mail": ""}]}
    loops.tag_new(now())
    granted = mem.add_fact(GRANTED, "project")[0]
    llm.check = {"result": "settled", "content": "ignored", "quote": "permit application was approved on October 5", "ref": f"F{granted}"}
    result = loops.run(now())
    old = mem.get_fact(waiting, follow_superseded=False)
    assert old["status"] == "superseded" and old["superseded_by"] == granted
    assert len(mem._fact_rows()) == 1                          # no third fact saying the same thing
    assert loop_row(mem, waiting)["status"] == "closed" and loop_row(mem, waiting)["closed_by"] == granted
    assert result["settled"] and sent[0]["kind"] == "notice" and "The permit decision → “permit application was approved on October 5”" in sent[0]["text"]
    assert mem.db.get_state("memory_profile_dirty", "0") == "1"   # the profile is rebuilt from it


def test_the_owners_words_replace_it_with_history_kept(make):
    mem, llm, _sent, loops = make()
    waiting = mem.add_fact(WAITING, "fact")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the permit decision", "check_after": "2026-10-09", "mail": ""}]}
    msg = mem.add_message("user", "Good news, the city approved my permit on the 5th.")
    llm.check = {"result": "settled", "content": GRANTED, "quote": "the city approved my permit", "ref": f"M{msg}"}
    loops.run(now())
    active = mem._fact_rows()
    assert [r["content"] for r in active] == [GRANTED] and active[0]["source"] == "loop"
    assert mem.get_fact(waiting, follow_superseded=False)["superseded_by"] == active[0]["id"]


@pytest.mark.parametrize("why", ["quote not in evidence", "wrong ref", "second opinion disagrees", "no answer"])
def test_nothing_changes_without_backing(make, why):
    mem, llm, sent, loops = make()
    waiting = mem.add_fact(WAITING, "fact")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the permit decision", "check_after": "2099-01-01", "mail": ""}]}
    msg = mem.add_message("user", "Still nothing about the permit, it's driving me nuts.")
    llm.check = {"result": "settled", "content": GRANTED, "quote": "the city approved my permit", "ref": f"M{msg}"}
    if why == "wrong ref":
        llm.check = {"result": "settled", "content": GRANTED, "quote": "Still nothing about the permit", "ref": "M99999"}
    elif why == "second opinion disagrees":
        llm.check["quote"] = "Still nothing about the permit"
        llm.verdict = False
    elif why == "no answer":
        llm.check["quote"] = "Still nothing about the permit"
        llm.verdict = None
    loops.run(now())
    assert mem.get_fact(waiting, follow_superseded=False)["status"] == "active"
    assert loop_row(mem, waiting)["status"] == "open" and not sent


def test_asks_once_when_nothing_settles_it_then_checks_the_answer_soon(make):
    mem, llm, sent, loops = make()
    waiting = mem.add_fact(WAITING, "fact")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the city's decision on your permit application.",
                         "check_after": "2026-01-01", "mail": ""}]}
    t = now()
    assert loops.run(t)["asked"] == waiting
    assert sent[-1]["kind"] == "question" and "waiting on the city's decision on your permit application (since" in sent[-1]["text"]
    assert loops.run(t)["asked"] is None                        # not again within ASK_AGAIN_DAYS
    later = t + datetime.timedelta(hours=1)
    assert not loops.due(later)
    reply = mem.add_message("user", "Yes, it came through on October 5th.")
    assert loops.due(later)                                     # the answer is looked at next cycle, not tomorrow
    llm.check = {"result": "settled", "content": GRANTED, "quote": "it came through on October 5th", "ref": f"M{reply}"}
    loops.run(later)
    evidence = [c for c in llm.calls if c[0]["content"].startswith("A personal assistant saved")][-1][1]["content"]
    assert "assistant asked" in evidence and "it came through" in evidence   # the question and the bare answer
    assert mem._fact_rows()[0]["content"] == GRANTED
    assert loops.run(t + datetime.timedelta(days=30))["asked"] is None        # closed: never asked again


def test_loops_settled_some_other_way_are_closed(make):
    mem, llm, _sent, loops = make()
    waiting = mem.add_fact(WAITING, "fact")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the permit decision", "check_after": "2099-01-01", "mail": ""}]}
    loops.tag_new(now())
    new_id, _ = mem.supersede_fact(waiting, GRANTED)          # the owner said so in chat
    assert loops.close_settled_elsewhere() == 1
    assert loop_row(mem, waiting)["status"] == "closed" and loop_row(mem, waiting)["closed_by"] == new_id
    assert loops.open_loops() == []


def test_mail_is_evidence_and_a_mail_failure_is_harmless(make):
    email = {"id": "m1", "from": "permits@city.example.gov", "from_name": "City Permit Office", "date": "2026-10-05",
             "subject": "Decision on your permit application", "snippet": "DECISION approving permit application"}
    mail = Mail([email])
    mem, llm, sent, loops = make(mail)
    waiting = mem.add_fact(WAITING, "fact")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the permit decision", "check_after": "2099-01-01", "mail": "permit decision"}]}
    llm.check = {"result": "settled", "content": GRANTED, "quote": "DECISION approving permit application", "ref": "E1"}
    loops.run(now())
    assert mail.queries and mail.queries[0].startswith("permit decision after:")
    assert mem._fact_rows()[0]["content"] == GRANTED and "an email" in sent[0]["text"]

    mem2, llm2, _sent2, loops2 = make(Mail(fail=True))
    w2 = mem2.add_fact("Alex is waiting for a refund from GoDaddy.", "fact")[0]
    llm2.tag = {"open": [{"id": w2, "waiting_on": "the GoDaddy refund", "check_after": "2099-01-01", "mail": "refund"}]}
    loops2.run(now())
    assert loop_row(mem2, w2)["status"] == "open"


def test_memory_screen_gets_current_facts_and_the_past_apart(make):
    os.environ.setdefault("STATE_DB_PATH", str(Path(__file__).parent / "test_data" / "hud_import.db"))
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import client_from

    mem, llm, _, loops = make()
    waiting = mem.add_fact(WAITING, "fact")[0]
    other = mem.add_fact("Alex is waiting for a refund from GoDaddy.", "fact")[0]
    llm.tag = {"open": [{"id": other, "waiting_on": "the GoDaddy refund", "check_after": "2099-01-01", "mail": ""}]}
    loops.tag_new(now())
    granted, _ = mem.supersede_fact(waiting, GRANTED)
    hud_state.set_db(mem.db)
    hud_state.set_memory(mem)
    data = client_from(hud_app).get("/api/memory").json()
    assert {f["id"] for f in data["facts"]} == {granted, other}
    assert [f["id"] for f in data["past"]] == [waiting]
    assert [(l["fact_id"], l["waiting_on"]) for l in data["open_loops"]] == [(other, "the GoDaddy refund")]


def tagged_research(mem, llm, loops, research="permit 26-01234 12 Elm Street decision"):
    waiting = mem.add_fact(WAITING, "fact")[0]
    mem.add_fact("Alex applied for permit 26-01234 for 12 Elm Street with the City of Example.", "project")
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the city's decision on his permit application", "check_after": "2099-01-01",
                         "mail": "", "research": research}]}
    return waiting


def test_a_public_matter_is_researched_and_the_tagger_sees_the_other_memories(make):
    research = Research([{**PERMIT_PAGE, "page": "Decision approving Application for a Building Permit. Posted 10/05/2026"}])
    mem, llm, sent, loops = make(research=research)
    waiting = tagged_research(mem, llm, loops)
    llm.check = {"result": "settled", "content": GRANTED, "quote": "Decision approving Application for a Building Permit",
                 "ref": "R1"}
    loops.run(now())
    assert research.queries == ["permit 26-01234 12 Elm Street decision"]
    tag_input = next(c for c in llm.calls if c[0]["content"].startswith("You read"))[1]["content"]
    assert "Other memories (context only)" in tag_input and "26-01234" in tag_input   # where the case number is
    evidence = [c for c in llm.calls if c[0]["content"].startswith("A personal assistant saved")][-1][1]["content"]
    assert "W1: [search result]" in evidence and "R1: [page https://permits.example.org" in evidence
    assert mem.get_fact(waiting, follow_superseded=False)["status"] == "superseded"
    new = next(r for r in mem._fact_rows() if r["content"] == GRANTED)
    assert new["source"] == "loop" and "permits.example.org" in new["evidence"]
    assert "a web page: https://permits.example.org/record/" in sent[0]["text"]


def test_news_without_the_outcome_is_told_once_with_the_link_and_asked(make):
    mem, llm, sent, loops = make(research=Research([PERMIT_PAGE]))
    waiting = tagged_research(mem, llm, loops)
    llm.check = {"result": "news", "content": "The city posted a decision on the permit application on October 5, 2026.",
                 "quote": "Decision on Application for a Building Permit", "ref": "R1"}
    t = now()
    result = loops.run(t)
    assert result["asked"] == waiting and mem.get_fact(waiting, follow_superseded=False)["status"] == "active"
    q = sent[-1]
    assert q["kind"] == "question" and "decision on the permit application on October 5" in q["text"]
    assert PERMIT_PAGE["url"] in q["text"] and "doesn't say how it came out" in q["text"]
    n = len(sent)
    mem.db.set_state(ol.STATE_LAST, "")
    mem.db.get_connection().execute("UPDATE mem_open_loops SET checked_at = NULL, evidence_key = NULL")
    mem.db.get_connection().commit()
    loops.run(t + datetime.timedelta(days=1))
    assert len(sent) == n                                       # the same entry is never told twice


def test_the_proactive_budget_holds_news_back_until_there_is_room(make):
    mem, llm, sent, loops = make(research=Research([PERMIT_PAGE]))
    waiting = tagged_research(mem, llm, loops)
    llm.check = {"result": "news", "content": "The city posted a decision on the permit application on October 5, 2026.",
                 "quote": "Decision on Application for a Building Permit", "ref": "R1"}
    t = now()
    assert loops.run(t, room=2, may_ask=False)["sent"] == [] and not sent      # a question isn't allowed now
    assert loop_row(mem, waiting)["evidence_key"] is None                     # so it's looked at again
    mem.db.get_connection().execute("UPDATE mem_open_loops SET checked_at = NULL")
    mem.db.get_connection().commit()
    assert loops.run(t + datetime.timedelta(minutes=30), room=1, may_ask=True)["asked"] == waiting


def test_research_failing_is_harmless(make):
    mem, llm, sent, loops = make(research=Research(fail=True))
    waiting = tagged_research(mem, llm, loops)
    loops.run(now())
    assert loop_row(mem, waiting)["status"] == "open" and not sent


def test_it_runs_as_a_step_of_the_proactive_cycle(make, tmp_path):
    from core.proactive import Proactive

    mem, llm, _, _ = make()

    class QuietLLM:
        def chat(self, messages, **kw):
            return json.dumps({"messages": [], "question": None, "brief": None})

    at = datetime.datetime.combine(datetime.date.today(), datetime.time(12, 0), TZ)
    pro = Proactive(mem.db, memory=mem, llm=QuietLLM(), now=lambda: at)
    pro.change(daily_cap="6", brief_time="23:59")
    email = {"id": "m1", "from": "permits@city.example.gov", "from_name": "City Permit Office", "date": "2026-10-05",
             "subject": "Decision on your permit application", "snippet": "DECISION on permit application posted"}
    research = Research([PERMIT_PAGE])
    loops = ol.OpenLoops(mem.db, mem, pro, Mail([email]), research)
    pro.open_loops = loops
    waiting = mem.add_fact(WAITING, "fact")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the city's decision on his permit application", "check_after": "2099-01-01",
                         "mail": "permit application", "research": "12 Elm Street permit decision"}]}
    llm.check = {"result": "news", "content": "The city posted a decision on the permit application on October 5, 2026.",
                 "quote": "DECISION on permit application posted", "ref": "E1"}
    sent = pro.run_cycle()
    assert research.queries == []                                # daytime proactive check skips web search
    assert [m["topic"] for m in sent] == ["open-loop"] and sent[0]["kind"] == "question"
    assert pro.inbox()[-1]["text"].startswith("🔎 You were waiting on")
    pro.change(daily_cap="0")
    mem.db.set_state(ol.STATE_LAST, "")
    assert pro.run_cycle() == []                                # the day's cap covers it too
    assert loop_row(mem, waiting)["status"] == "open"


def test_long_pages_are_cut_to_the_parts_about_the_matter():
    header = "Permit 26-01234 for 12 Elm Street, City of Example Planning Office. " + "Filler text. " * 300
    entry = ("Monday, October 05, 2026 4 decision Decision on Application for a Building Permit Mon 10/05 1:08 PM "
             "DECISION approving 1 Application for a building permit; approving 3 Revised plans")
    page = header + entry + " More filler. " * 300
    cut = ol._excerpt(page, ["Elm", "Street", "decision", "application", "approved"])
    assert len(cut) <= ol.PAGE_CHARS + 10 and "DECISION approving 1 Application for a building permit" in cut
    assert ol._queries('["12 Elm Street permit decision", "26-01234 City of Example", "a third"]') == \
        ["12 Elm Street permit decision", "26-01234 City of Example"]
    assert ol._queries("one query") == ["one query"] and ol._queries(None) == [] and ol._queries([]) == []


def test_the_second_opinion_sees_the_passage_it_is_asked_about():
    page = "[page https://example.org/permit] Record header. " + "Applicant X. " * 150 + \
           "Mon 10/05 DECISION approving 1 Application for a building permit; approving 3 Revised" + " Tail." * 300
    shown = ol._around(page, "DECISION approving 1 Application for a building permit")
    assert shown.startswith("[page https://example.org/permit]") and "DECISION approving 1 Application for a building permit" in shown
    assert len(shown) < 2000


def test_the_memory_screen_opens_even_if_open_loops_cant_be_read(make, monkeypatch):
    os.environ.setdefault("STATE_DB_PATH", str(Path(__file__).parent / "test_data" / "hud_import.db"))
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import client_from

    mem, _llm, _, _ = make()
    mem.add_fact(WAITING, "fact")
    hud_state.set_db(mem.db)
    hud_state.set_memory(mem)

    def broken(self):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(ol.OpenLoops, "open_loops", broken)
    r = client_from(hud_app).get("/api/memory")
    assert r.status_code == 200 and r.json()["open_loops"] == [] and len(r.json()["facts"]) == 1


def test_researched_at_recorded_on_table_and_updated_after_research(make):
    research = Research([PERMIT_PAGE])
    mem, llm, _sent, loops = make(research=research)
    waiting = tagged_research(mem, llm, loops)
    t = now()
    loops.run(t, include_research=True)
    row = loop_row(mem, waiting)
    assert row["researched_at"] == t.isoformat()
    assert len(research.queries) == 1
    # Soon after (1 hour), research is not due, so no new query is made
    loops.run(t + datetime.timedelta(hours=1), include_research=True)
    assert len(research.queries) == 1


def test_cadence_48h_normal_and_24h_near_check_after(make):
    _mem, _llm, _, loops = make()
    today = datetime.date(2026, 10, 15)
    t = datetime.datetime.combine(today, datetime.time(12, 0), TZ)

    # Far check_after (10 days out): cadence is 48 hours
    loop_far = {"check_after": "2026-10-25", "research": "[\"query\"]", "researched_at": t.isoformat()}
    assert not ol.OpenLoops.is_near_check_after(loop_far, t)
    assert ol.OpenLoops.research_cadence(loop_far, t) == datetime.timedelta(hours=48)
    fact = {"id": 1, "content": "waiting on something", "created_at": t.isoformat()}
    assert not loops.research_due(loop_far, fact, t + datetime.timedelta(hours=25))
    assert loops.research_due(loop_far, fact, t + datetime.timedelta(hours=49))

    # Near check_after (2 days out): cadence is 24 hours
    loop_near = {"check_after": "2026-10-17", "research": "[\"query\"]", "researched_at": t.isoformat()}
    assert ol.OpenLoops.is_near_check_after(loop_near, t)
    assert ol.OpenLoops.research_cadence(loop_near, t) == datetime.timedelta(hours=24)
    assert not loops.research_due(loop_near, fact, t + datetime.timedelta(hours=12))
    assert loops.research_due(loop_near, fact, t + datetime.timedelta(hours=25))


def test_cheap_signals_flag_research_before_cadence(make):
    mail = Mail([{"id": "m1", "subject": "permit decision update", "snippet": "update", "date": "2026-10-15"}])
    research = Research([PERMIT_PAGE])
    mem, llm, _, loops = make(mail=mail, research=research)
    waiting = tagged_research(mem, llm, loops)
    t = now()
    loops.tag_new(t)
    # Mark as researched 2 hours ago with an existing evidence_key
    researched_time = t - datetime.timedelta(hours=2)
    with loops._conn() as conn:
        conn.execute("UPDATE mem_open_loops SET researched_at = ?, evidence_key = 'key1' WHERE fact_id = ?",
                     (researched_time.isoformat(), waiting))
    loop = loop_row(mem, waiting)
    fact = mem.get_fact(waiting)

    # 1. New email flags it
    assert loops._cheap_flagged(fact, loop, researched_time.isoformat())
    assert loops.research_due(loop, fact, t)

    # 2. Alex mentioning it in chat flags it (without mail)
    loops_no_mail = ol.OpenLoops(mem.db, mem, None, Mail([]), research)
    mem.add_message("user", "I sent the permit application earlier.")
    assert loops_no_mail._cheap_flagged(fact, loop, researched_time.isoformat())
    assert loops_no_mail.research_due(loop, fact, t)

    # 3. A newer fact about it flags it
    mem2, _, _, loops2 = make()
    w2 = tagged_research(mem2, llm, loops2)
    loops2.tag_new(t)
    researched_time2 = now()
    with loops2._conn() as conn:
        conn.execute("UPDATE mem_open_loops SET researched_at = ?, evidence_key = 'key2' WHERE fact_id = ?",
                     (researched_time2.isoformat(), w2))
    l2 = loop_row(mem2, w2)
    f2 = mem2.get_fact(w2)
    assert not loops2._cheap_flagged(f2, l2, researched_time2.isoformat())
    mem2.add_fact("The permit portal for the application was updated.", "fact")
    assert loops2._cheap_flagged(f2, l2, researched_time2.isoformat())
    assert loops2.research_due(l2, f2, t)


def test_daytime_check_skips_web_search_even_when_due(make):
    research = Research([PERMIT_PAGE])
    mem, llm, _, loops = make(research=research)
    waiting = tagged_research(mem, llm, loops)
    t = now()
    # include_research=False: zero searches, researched_at not updated
    loops.run(t, include_research=False)
    assert research.queries == []
    assert loop_row(mem, waiting)["researched_at"] is None

    # include_research=True: runs search, updates researched_at
    loops.run(t, include_research=True)
    assert len(research.queries) == 1
    assert loop_row(mem, waiting)["researched_at"] == t.isoformat()


def test_migration_adds_researched_at_column(tmp_path):
    import sqlite3
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE mem_open_loops (
            fact_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            waiting_on TEXT,
            check_after TEXT,
            mail TEXT,
            created_at TEXT NOT NULL,
            checked_at TEXT,
            asked_at TEXT,
            closed_by INTEGER,
            note TEXT,
            research TEXT,
            evidence_key TEXT,
            news TEXT
        )
    """)
    conn.commit()
    conn.close()

    mem_db = DatabaseManager(db_file)
    llm = LoopLLM()
    mem = MemoryManager(mem_db, llm=llm, embedder=FakeEmbedder())
    loops = ol.OpenLoops(mem_db, mem)
    cols = {r[1] for r in loops._conn().execute("PRAGMA table_info(mem_open_loops)")}
    assert "researched_at" in cols


def test_a_check_whose_ai_failed_is_tried_again_next_run(make):
    """2026-10-31 review, defect 3: the evidence key was saved before the AI call, so when every model failed the same
    evidence was skipped from then on. Now nothing is remembered without a verdict, and the next run asks again."""
    mem, llm, _sent, loops = make()
    waiting = mem.add_fact(WAITING, "fact")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the permit decision", "check_after": "2099-01-01", "mail": ""}]}
    msg = mem.add_message("user", "Good news, the city approved my permit on the 5th.")
    llm.check = None                                           # every model failed: no readable answer
    loops.run(now())
    assert loop_row(mem, waiting)["evidence_key"] is None and loops.check_failed
    llm.check = {"result": "settled", "content": GRANTED, "quote": "the city approved my permit", "ref": f"M{msg}"}
    loops.run(now() + ol.CHECK_EVERY + datetime.timedelta(minutes=1))     # same evidence, the next day's run
    assert mem.get_fact(waiting, follow_superseded=False)["status"] == "superseded"


def test_no_second_opinion_means_the_evidence_is_looked_at_again(make):
    mem, llm, _sent, loops = make()
    waiting = mem.add_fact(WAITING, "fact")[0]
    llm.tag = {"open": [{"id": waiting, "waiting_on": "the permit decision", "check_after": "2099-01-01", "mail": ""}]}
    msg = mem.add_message("user", "Good news, the city approved my permit on the 5th.")
    llm.check = {"result": "settled", "content": GRANTED, "quote": "the city approved my permit", "ref": f"M{msg}"}
    llm.verdict = None                                         # the second opinion couldn't be had
    loops.run(now())
    assert mem.get_fact(waiting, follow_superseded=False)["status"] == "active"
    assert loop_row(mem, waiting)["evidence_key"] is None      # not "seen": tried again next run


def test_only_one_run_at_a_time(make):
    """2026-10-31 review, defect 7: the away shift and the proactive cycle run on their own threads."""
    mem, llm, _sent, loops = make()
    mem.add_fact(WAITING, "fact")
    calls = len(llm.calls)
    loops._run_lock.acquire()                                  # the other thread's run is going
    try:
        result = loops.run(now())
    finally:
        loops._run_lock.release()
    assert result.get("busy") and not result["sent"] and len(llm.calls) == calls
    assert not loops.run(now()).get("busy")                    # free again afterwards
