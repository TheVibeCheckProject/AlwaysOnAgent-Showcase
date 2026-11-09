"""
Tests for the away shift worker (core/away_shift.py; docs/PROACTIVE_RESEARCH.md 1a, 1l).
"""

import datetime
import json
import time
from unittest.mock import patch

from core import away_shift
from core.candidates import CandidatePool
from core.database import DatabaseManager
from core.events import PRESENCE
from core.memory import MemoryManager
from tests.test_memory import FakeEmbedder, FakeLLM


class ScriptedLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.responses = {}

    def chat(self, messages, **kw):
        sys_msg = messages[0]["content"] if messages else ""
        if "things your owner cares about" in sys_msg:
            return json.dumps({"queries": [{"topic": "Styropyro", "query": "Styropyro new video 2026"}]})
        if "reviewing search results about something they follow" in sys_msg:
            return json.dumps({
                "news": "Styropyro published a new video demonstrating a 1000W diode laser array.",
                "why": "checked Styropyro while you were away",
                "subject": "Styropyro's laser array"
            })
        if "reviewing your owner's activity over the past week" in sys_msg:
            return json.dumps({
                "question": "You've been up past 2 am most nights working on the assistant. Is late night when you focus best?",
                "subject": "late night coding focus",
                "why": "noticed late night activity this week"
            })
        if "brainstorming a pool of curious thoughts" in sys_msg:
            return json.dumps({"candidates": [{
                "question": "How did you first get into building laser projects?",
                "about": "laser projects",
                "subject": "laser projects background",
                "why": "you mentioned: “laser projects”"
            }]})
        return super().chat(messages, **kw)


class MockProactive:
    def __init__(self, db):
        self.db = db
        self.pool = CandidatePool(db)

    def deliver(self, kind, text, topic="", **kw):
        return {"id": 1, "kind": kind, "text": text, "topic": topic}


def make_away_shift(tmp_path, search_results=None, away_sec=8000, is_open=False):
    db = DatabaseManager(tmp_path / "away.db")
    llm = ScriptedLLM()
    mem = MemoryManager(db, llm=llm, embedder=FakeEmbedder())
    proactive = MockProactive(db)

    def mock_search(q, limit=4):
        return search_results or [
            {"title": "Styropyro Laser Video", "snippet": "New video on high power lasers", "url": "https://youtube.com/watch?v=123"}
        ]

    clock_val = [1000000.0]

    shift = away_shift.AwayShift(
        db,
        memory=mem,
        llm=llm,
        proactive=proactive,
        open_loops=None,
        search_fn=mock_search,
        clock=lambda: clock_val[0],
        now_fn=lambda: datetime.datetime.fromtimestamp(clock_val[0]).astimezone()
    )
    return shift, db, mem, llm, proactive, clock_val


def test_not_due_while_app_is_open(tmp_path, monkeypatch):
    shift, _db, _, _, _, _ = make_away_shift(tmp_path)
    monkeypatch.setattr(PRESENCE, "is_open", lambda: True)
    monkeypatch.setattr(PRESENCE, "away_seconds", lambda: 10000.0)
    assert not shift.due()


def test_starts_two_hours_after_app_closes(tmp_path, monkeypatch):
    shift, db, _, _, _, _ = make_away_shift(tmp_path)
    monkeypatch.setattr(PRESENCE, "is_open", lambda: False)

    # 1 hour away -> not due
    monkeypatch.setattr(PRESENCE, "away_seconds", lambda: 3600.0)
    assert not shift.due()

    # 2 hours away -> due
    monkeypatch.setattr(PRESENCE, "away_seconds", lambda: 7200.0)
    assert shift.due()

    # Session started just now -> not due until 2 hours pass
    now = datetime.datetime.now().astimezone()
    db.set_state(away_shift.STATE_LAST_SESSION, now.isoformat())
    assert not shift.due(now=now + datetime.timedelta(hours=1))
    assert shift.due(now=now + datetime.timedelta(hours=2, minutes=5))


def test_sessions_at_least_two_hours_apart(tmp_path, monkeypatch):
    shift, db, _, _, _, _ = make_away_shift(tmp_path)
    monkeypatch.setattr(PRESENCE, "is_open", lambda: False)
    monkeypatch.setattr(PRESENCE, "away_seconds", lambda: 8000.0)

    t0 = datetime.datetime(2026, 10, 13, 10, 0).astimezone()
    assert shift.due(now=t0)
    db.set_state(away_shift.STATE_LAST_SESSION, t0.isoformat())

    # 1.5 hours later -> not due
    assert not shift.due(now=t0 + datetime.timedelta(hours=1.5))
    # 2.1 hours later -> due
    assert shift.due(now=t0 + datetime.timedelta(hours=2.1))


def test_daily_search_cap_holds(tmp_path):
    shift, _db, _, _, _, _clock_val = make_away_shift(tmp_path)
    t = datetime.datetime(2026, 10, 13, 14, 0).astimezone()

    assert shift.search_budget_remaining(t) == away_shift.DAILY_SEARCH_CAP

    # Use 14 searches
    shift.record_searches(14, t)
    assert shift.search_budget_remaining(t) == 1

    # 15th search succeeds
    res = shift.capped_search("test query 1", now=t)
    assert len(res) >= 1
    assert shift.search_budget_remaining(t) == 0

    # 16th search is blocked by cap
    res2 = shift.capped_search("test query 2", now=t)
    assert res2 == []
    assert shift.searches_today(t) == away_shift.DAILY_SEARCH_CAP


def test_session_with_nothing_due_makes_no_searches(tmp_path, monkeypatch):
    from core import research_worker as rw
    searches_made = []

    def counting_search(q, limit=4):
        searches_made.append(q)
        return []

    shift, db, _mem, _, _, _ = make_away_shift(tmp_path)
    shift.search_fn = counting_search

    # No facts, no messages, no loops, and research worker not due (ran recently)
    now = datetime.datetime.now().astimezone()
    db.set_state(rw.STATE_LAST_RESEARCH, now.isoformat())
    shift.run_session(now=now)

    assert searches_made == []
    assert shift.searches_today(now) == 0


def test_activity_events_published_during_session(tmp_path):
    shift, _db, mem, _, _, _ = make_away_shift(tmp_path)
    mem.add_fact("Alex enjoys DIY electronics and high voltage experiments.", "preference")

    with patch("core.away_shift.publish_activity") as mock_pub:
        shift.run_session()
        assert mock_pub.called
        call_states = [c[0][0] for c in mock_pub.call_args_list]
        assert "started" in call_states
        assert "step" in call_states
        assert "finished" in call_states


def test_candidates_added_to_pool_by_away_steps(tmp_path):
    shift, _db, mem, _, proactive, _ = make_away_shift(tmp_path)
    mem.add_fact("Alex watches Styropyro videos.", "preference")
    mem.add_fact("Alex is working on laser projects.", "project")
    for i in range(10):
        mem.add_message("user", f"Testing coding late night at 2am message {i}")

    now = datetime.datetime(2026, 10, 13, 14, 0).astimezone()
    shift.run_session(now=now)

    pending = proactive.pool.pending()
    sources = {c["source"] for c in pending}
    assert any(s in ("interest", "pattern", "curiosity") for s in sources)


def test_chat_path_and_proactive_cycle_not_blocked_during_session(tmp_path):
    shift, _db, _mem, _, _proactive, _ = make_away_shift(tmp_path)

    # Simulate slow search function
    def slow_search(q, limit=4):
        time.sleep(0.05)
        return []

    shift.search_fn = slow_search

    # Launch session asynchronously via executor
    fut = shift.start()
    assert shift.is_running()

    # The status line is immediately available and non-blocking
    status = shift.status_line()
    assert "Background work in progress" in status

    # Wait for completion
    fut.result(timeout=5)
    assert not shift.is_running()
    shift.shutdown(wait=True)


def test_reports_name_pending_findings_by_topic_and_survive_unscored_ones(tmp_path):
    """2026-10-31 review, defects 4-5: the reports read "subject" (no such column: it's `topic`) and formatted a NULL
    score with :.0f, which crashed details_report while any pending candidate wasn't scored yet."""
    shift, db, _mem, _llm, proactive, _clock_val = make_away_shift(tmp_path)
    proactive.pool.add("curiosity", "question", "How did you get into lasers?", topic="laser projects background")
    db.set_state(away_shift.STATE_LAST_SESSION, shift.now_fn().isoformat())
    report = shift.details_report()                            # raised TypeError before the fix
    assert "laser projects background (score: not scored yet)" in report
    assert "laser projects background" in shift.status_line()
    shift.shutdown(wait=True)
