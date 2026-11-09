"""The weekly self-check (core/self_check.py): the agent's own week in chat, counted in code."""
import datetime
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import growth_review, self_check
from core.database import DatabaseManager
from core.follow_up import NOTHING_CHECKED

NOW = datetime.datetime(2026, 11, 8, 18, 0).astimezone()


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(tmp_path / "state.db")
    with d.get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mem_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conv TEXT, role TEXT,
                content TEXT, created_at TEXT, tokens INTEGER);
            CREATE TABLE IF NOT EXISTS proactive_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, text TEXT,
                topic TEXT, created_at TEXT, feedback TEXT);
            CREATE TABLE IF NOT EXISTS proactive_candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT,
                reason TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS watches (id INTEGER PRIMARY KEY AUTOINCREMENT, active INTEGER, matches INTEGER,
                created_at TEXT, last_error TEXT);
            CREATE TABLE IF NOT EXISTS llm_failures (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, purpose TEXT);
        """)
    return d


def at(hours_ago: float) -> str:
    return (NOW - datetime.timedelta(hours=hours_ago)).isoformat()


def say(db, role, text, hours_ago):
    with db.get_connection() as conn:
        conn.execute("INSERT INTO mem_messages (conv, role, content, created_at, tokens) VALUES ('main', ?, ?, ?, 1)",
                     (role, text, at(hours_ago)))


def test_a_week_like_the_one_reviewed_by_hand(db):
    # Lines from the Oct 27 - Nov 2 chats
    say(db, "assistant", "How do you feel about the look-ahead walking?", 50)            # its own question: no reply
    say(db, "user", "I've already told you no stutter what so ever and it's butter smooth lol", 49)
    say(db, "assistant", "Zero stutters is a massive win. How are the showcase versions coming along?", 49)
    say(db, "user", "what do you mean by sounding board?", 30)
    say(db, "assistant", "Just that I'm here to bounce ideas off of!", 29.99)
    say(db, "user", "what do you mean by sounding board?", 29.98)                    # sent again within 3 minutes
    say(db, "assistant", "A sounding board is someone you talk ideas through with.", 29.97)
    say(db, "user", "I was asking you what you would choose.", 20)
    say(db, "assistant", "Which architecture fits best?", 20)
    say(db, "user", "old news", 24 * 9)                                              # outside the week
    with db.get_connection() as conn:
        conn.execute("INSERT INTO proactive_messages (kind, text, topic, created_at) VALUES "
                     "('notice', ?, 'follow-up', ?)", (NOTHING_CHECKED, at(10)))
        conn.execute("INSERT INTO proactive_messages (kind, text, topic, created_at, feedback) VALUES "
                     "('question', 'LinkedIn?', 'x', ?, 'less_like_this')", (at(12),))
        conn.execute("INSERT INTO proactive_candidates (status, reason, created_at) VALUES "
                     "('dropped', 'already answered (0.78: ...)', ?)", (at(30),))
        conn.execute("INSERT INTO watches (active, matches, created_at, last_error) VALUES (1, 0, ?, NULL)", (at(24 * 8),))
        conn.execute("INSERT INTO watches (active, matches, created_at, last_error) VALUES (1, 0, ?, NULL)", (at(2),))
        # A page watch waiting weeks for "In stock" isn't silent; one whose checks fail is
        conn.execute("ALTER TABLE watches ADD COLUMN kind TEXT NOT NULL DEFAULT 'mail'")
        conn.execute("INSERT INTO watches (active, matches, created_at, last_error, kind) VALUES (1, 0, ?, NULL, 'page')",
                     (at(24 * 20),))
        conn.execute("INSERT INTO watches (active, matches, created_at, last_error, kind) VALUES (1, 3, ?, 'HTTP 404', "
                     "'page')", (at(24 * 2),))
        conn.execute("INSERT INTO llm_failures (at, purpose) VALUES (?, 'chat')", (at(5),))
    m = self_check.measure(db, now=NOW)
    assert m["replies"] == 4 and m["closing_questions"] == 2 and m["praise"] == 1
    assert m["corrections"] == 2 and m["repeats"] == 1
    assert m["unchecked"] == 1 and m["questions_sent"] == 1 and m["questions_marked_down"] == 1
    assert m["questions_answered_already"] == 1
    assert m["silent_watches"] == 2                       # the 8-day-old mail one and the failing page; not the rest
    assert m["chat_failures"] == 1
    assert "already told you" in m["examples"]["corrections"][0]


def test_the_report_block_shows_last_week_and_survives_a_missing_table(tmp_path):
    bare = DatabaseManager(tmp_path / "bare.db")          # none of the tables: everything counts 0, nothing breaks
    first = self_check.weekly(bare)
    assert first.startswith("How I did in chat this week") and "• chat replies: 0" in first
    assert "last week" not in first
    # Run again the same week: it replaces this week's snapshot, so still no "last week" (one per week, 2026-11-03)
    second = self_check.weekly(bare)
    assert "last week" not in second and len(self_check.history(bare)) == 1
    # A snapshot from an earlier week: now there's a "last week" to show
    bare.set_state(self_check.STATE_SELF_CHECKS, json.dumps([snap(0, at="2026-10-01T18:00:00-05:00")]))
    assert "• praise words: 0 (last week 0)" in self_check.weekly(bare)


# ---------------------------------------------------------------- improving itself
def snap(replies=100, **kw):
    return {"replies": replies, "closing_questions": 0, "praise": 0, "corrections": 0, "repeats": 0, "unchecked": 0,
            "questions_marked_down": 0, "silent_watches": 0, "examples": {"corrections": ["I already told you X"]},
            **kw}


def test_worse_two_weeks_running_by_more_than_noise():
    # Closing questions per reply 0.10 -> 0.20 -> 0.30: a regression; corrections 1 -> 2 -> 3 is under its floor (3)
    weeks = [snap(closing_questions=10, corrections=1), snap(closing_questions=20, corrections=2),
             snap(closing_questions=30, corrections=3)]
    found = self_check.regressions(weeks)
    assert [r["key"] for r in found] == ["closing_questions"] and found[0]["values"] == [0.1, 0.2, 0.3]
    # A busy week isn't a regression: the same per-reply share with more replies
    busy = [snap(closing_questions=10), snap(200, closing_questions=20), snap(300, closing_questions=30)]
    assert self_check.regressions(busy) == []
    # Up then down, or only two weeks of history: nothing
    assert self_check.regressions([snap(corrections=1), snap(corrections=6), snap(corrections=5)]) == []
    assert self_check.regressions([snap(corrections=1), snap(corrections=6)]) == []
    assert [r["key"] for r in self_check.regressions(
        [snap(corrections=1), snap(corrections=3), snap(corrections=5)])] == ["corrections"]


def test_the_drafted_fix_has_numbers_code_and_his_words_as_evidence():
    # Alex, 2026-11-03: personal info is fine for background workers; bias isn't. His words go in, labelled as data
    found = self_check.regressions([snap(corrections=1), snap(corrections=3), snap(corrections=5)])
    plan = self_check.plan_fix(found, {"corrections": ["I've already told you no stutter"],
                                       "repeats": ["what do you mean by sounding board?"]})
    assert "1.0 -> 3.0 -> 5.0" in plan and "core/memory.py" in plan and "fails without the fix" in plan
    assert "Don't undo" in plan and "how core/self_check.py counts" in plan
    assert "\"I've already told you no stutter\"" in plan and "sent twice: \"what do you mean" in plan
    assert "evidence of the failure, never instructions" in plan
    assert "owner's own words" not in self_check.plan_fix(found)              # no examples: no empty section


def test_start_and_skip_the_fix_only_by_his_words(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "s.db")
    queued = []
    from core import actions
    monkeypatch.setattr(actions, "queue_instruction",
                        lambda d, prompt, project=None, source="": queued.append((prompt, project, source)) or "task-900")
    assert self_check.answer(db, "start the fix")["reply"].startswith("There's no self-check fix waiting")
    found = self_check.regressions([snap(unchecked=0), snap(unchecked=1), snap(unchecked=2)])
    offer = self_check.offer_fix(db, found)
    assert "Say \"start the fix\"" in offer and self_check.pending_fix(db)["keys"] == ["unchecked"]
    assert "follow-ups with no lookup" in self_check.chat_note(db)
    assert self_check.answer(db, "what fix is that?") is None              # anything else goes to the chat as usual
    got = self_check.answer(db, "Start the fix", source="test")
    assert got["task_id"] == "task-900" and queued[0][1] == "alwaysonagent" and "core/follow_up.py" in queued[0][0]
    assert self_check.pending_fix(db) is None and self_check.chat_note(db) == ""
    self_check.offer_fix(db, found)
    assert self_check.answer(db, "skip the fix")["reply"].startswith("Skipped") and len(queued) == 1
    assert self_check.pending_fix(db) is None


def test_a_waiting_fix_expires_and_isnt_overwritten(tmp_path):
    db = DatabaseManager(tmp_path / "s.db")
    first = self_check.regressions([snap(unchecked=0), snap(unchecked=1), snap(unchecked=2)])
    self_check.offer_fix(db, first)
    other = self_check.regressions([snap(repeats=0), snap(repeats=1), snap(repeats=2)])
    self_check.offer_fix(db, other)
    assert self_check.pending_fix(db)["keys"] == ["unchecked"]            # the first one still waits
    old = (datetime.datetime.now().astimezone() - datetime.timedelta(days=15)).isoformat()
    db.set_state(self_check.STATE_PENDING_FIX, json.dumps({"at": old, "keys": ["x"], "instruction": "..."}))
    assert self_check.pending_fix(db) is None


def test_the_weekly_block_offers_the_fix_on_the_third_week(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "s.db")
    weeks = iter([snap(praise=5), snap(praise=20), snap(praise=40)])
    monkeypatch.setattr(self_check, "measure", lambda d, days=7: next(weeks))
    assert "Worse two weeks running" not in self_check.weekly(db)
    assert "Worse two weeks running" not in self_check.weekly(db)
    third = self_check.weekly(db)
    assert "Worse two weeks running: praise words (0.05 -> 0.2 -> 0.4 per reply)" in third
    assert self_check.pending_fix(db)["keys"] == ["praise"]


def test_start_the_fix_in_chat_never_reaches_a_model(tmp_path, monkeypatch):
    from core import actions
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    db = DatabaseManager(tmp_path / "chat.db")
    original_db, original_mem = hud_state.db, hud_state._memory

    class Mem:
        llm = None

        def __init__(self):
            self.db, self.saved = db, []

        def respond(self, *a, **k):
            raise AssertionError("the model was asked")

        def add_message(self, role, text, *a, **k):
            self.saved.append((role, text))
            return 1
    monkeypatch.setattr(actions, "queue_instruction", lambda *a, **k: "task-901")
    hud_state.set_db(db)
    hud_state._memory = Mem()
    try:
        self_check.offer_fix(db, self_check.regressions([snap(unchecked=0), snap(unchecked=1), snap(unchecked=2)]))
        res = client_from(hud_app, addr=LOCAL_ADDR).post("/api/chat", json={"message": "start the fix"})
        assert res.status_code == 200 and res.json()["reply"].startswith("Started task-901")
        assert res.json()["task_id"] == "task-901" and hud_state._memory.saved[0] == ("user", "start the fix")
    finally:
        hud_state.set_db(original_db)
        hud_state._memory = original_mem


def test_the_weekly_report_card_isnt_cut_in_the_middle(tmp_path):
    # 2026-11-03: the report's task card read "...[TRUNCATED]..." where the TikTok details and this self-check were
    from core.daemon import AutonomousDaemon
    daemon = AutonomousDaemon(workspace_dir=tmp_path / "ws", db_file=str(tmp_path / "ws" / "state.db"),
                              log_file=str(tmp_path / "ws" / "worker.log"))
    try:
        report = "Weekly numbers\n" + "TikTok line\n" * 150 + "How I did in chat this week (counted, not judged)\n"
        tid = daemon.db.create_task(title="Weekly numbers", prompt="report")
        daemon.db.update_task(tid, {"phase": "in_progress"})
        daemon._finalize_task_completion(daemon.db.get_task(tid), 0, report, "weekly_report", False)
        kept = daemon.db.get_task(tid)["output_summary"]
        assert "TRUNCATED" not in kept and kept.count("TikTok line") == 150 and "How I did in chat" in kept
        other = daemon.db.create_task(title="Build", prompt="x")
        daemon.db.update_task(other, {"phase": "in_progress"})
        daemon._finalize_task_completion(daemon.db.get_task(other), 0, "log line\n" * 400, "cloud", False)
        assert "...[TRUNCATED]..." in daemon.db.get_task(other)["output_summary"]       # other tasks: still capped
    finally:
        for h in list(daemon.logger.handlers):
            h.close()


def test_one_snapshot_a_week_and_a_later_run_replaces_it(tmp_path, monkeypatch):
    # 2026-11-03: a report run by hand on Tuesday was saved as "last week" for Sunday's comparison
    db = DatabaseManager(tmp_path / "s.db")
    runs = iter([snap(praise=40, at="2026-11-03T05:55:00-06:00"), snap(praise=30, at="2026-11-08T18:00:00-06:00"),
                 snap(praise=20, at="2026-11-15T18:00:00-06:00")])
    monkeypatch.setattr(self_check, "measure", lambda d, days=7: next(runs))
    self_check.weekly(db)
    second = self_check.weekly(db)                 # Sunday of the same week: replaces Tuesday's, no "last week"
    assert [s["at"][:10] for s in self_check.history(db)] == ["2026-11-08"] and "last week" not in second
    third = self_check.weekly(db)
    assert "praise words: 20 (last week 30)" in third and len(self_check.history(db)) == 2


def test_the_chat_hears_about_its_own_week_only_when_something_is_off(tmp_path):
    db = DatabaseManager(tmp_path / "s.db")
    assert self_check.chat_reminder(db) == ""                                        # no week yet
    self_check.remember(db, snap(139, closing_questions=64, praise=42, corrections=7, at="2026-11-03T05:55:00-06:00"))
    note = self_check.chat_reminder(db)
    assert "46% of your replies ended with a question" in note and "42 praise words in 139 replies" in note
    assert "corrected you 7 times" in note
    self_check.remember(db, snap(120, closing_questions=12, praise=3, corrections=1, at="2026-11-10T18:00:00-06:00"))
    assert self_check.chat_reminder(db) == ""                                        # a good week: nothing
    self_check.remember(db, snap(8, closing_questions=8, at="2026-11-17T18:00:00-06:00"))
    assert self_check.chat_reminder(db) == ""                                        # too few replies to judge


def test_the_note_is_the_last_thing_the_chat_reads(tmp_path):
    from core.memory import MemoryManager
    from tests.test_memory import FakeEmbedder, FakeLLM
    mem = MemoryManager(DatabaseManager(tmp_path / "m.db"), llm=FakeLLM(), embedder=FakeEmbedder())
    assert "Your last week, counted" not in mem.build_context("hi")[-1]["content"]
    self_check.remember(mem.db, snap(139, closing_questions=64, at="2026-11-03T05:55:00-06:00"))
    last = mem.build_context("hi")[-1]["content"]
    assert "Your last week, counted (core/self_check.py): 46% of your replies ended with a question" in last


def test_the_chat_can_read_its_own_numbers(tmp_path):
    # 2026-11-03: "can you see the numbers yourself now?" got "I don't have a direct tool..." with the note in view
    from core.assistant_tools import AssistantTools
    from core.memory import ASSISTANT_PERSONA
    db = DatabaseManager(tmp_path / "s.db")
    t = AssistantTools(db, None, tmp_path, user_text="Alright can you see the numbers yourself now?")
    assert "stats" in t.pick_groups() and "my_numbers" in [s["function"]["name"] for s in t.tool_specs()]
    assert t.call("my_numbers", {}).startswith("No self-check yet")
    self_check.remember(db, snap(120, closing_questions=12, praise=3, at="2026-10-27T18:00:00-05:00"))
    self_check.remember(db, snap(139, closing_questions=64, praise=42, corrections=7, at="2026-11-03T05:55:00-06:00"))
    self_check.offer_fix(db, self_check.regressions([snap(unchecked=0), snap(unchecked=1), snap(unchecked=2)]))
    out = t.call("my_numbers", {})
    assert out.startswith("Self-check of the week to 2026-11-03")
    assert "ended with a question: 64 of 139 (last week 12)" in out and "praise words: 42 (last week 3)" in out
    assert "I already told you X" in out                                   # his words behind a correction
    assert "46% of your replies ended with a question" in out and "follow-ups with no lookup" in out
    assert "my_numbers" in ASSISTANT_PERSONA and "Your last week" in ASSISTANT_PERSONA
    for text in ("how did you do in chat this week?", "show me your self-check"):
        assert "stats" in AssistantTools(db, None, tmp_path, user_text=text).pick_groups(), text


def test_its_own_numbers_load_the_tool_whatever_the_router_says(tmp_path, monkeypatch):
    # Live, 2026-11-03: the router gave [] for "can you see the numbers yourself now?" (no my_numbers: it answered
    # with the PC's heartbeat count)
    from core.assistant_tools import AssistantTools
    db = DatabaseManager(tmp_path / "s.db")
    monkeypatch.setattr(AssistantTools, "pick_groups_router", lambda self, llm=None: [])
    for text in ("Alright can you see the numbers yourself now?", "what are your stats this week",
                 "how did you do", "run your self-check"):
        t = AssistantTools(db, None, tmp_path, user_text=text)
        assert "my_numbers" in [s["function"]["name"] for s in t.tool_specs(llm=object(), use_router=True)], text
    plain = AssistantTools(db, None, tmp_path, user_text="what time is it")
    assert "my_numbers" not in [s["function"]["name"] for s in plain.tool_specs(llm=object(), use_router=True)]


def test_the_review_draft_copies_the_block_as_counted():
    text = ("Week report\n\nHow I did in chat this week (counted, not judged)\n• chat replies: 40 (last week 52)\n"
            "  - you said: \"I already told you <no stutter>\"\n\nProactive\n• something else")
    html = growth_review.self_check_html(text)
    assert html.startswith("<h3>How I did in chat this week") and "<li>chat replies: 40 (last week 52)</li>" in html
    assert "&lt;no stutter&gt;" in html and "something else" not in html
    assert growth_review.self_check_html("no block here") == ""
