"""Tests for the automated AI research worker (core/research_worker.py)."""

import datetime
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import research_worker as rw
from core.canvas import Canvas
from core.database import DatabaseManager
from core.memory import MemoryManager


def make_test_db():
    tmp = Path(tempfile.mkdtemp(prefix="research_test_"))
    db = DatabaseManager(tmp / "test.db")
    return db, tmp


def test_research_due_logic():
    db, _ = make_test_db()

    # 1. First run: empty state -> due
    assert rw.research_due(db) is True

    # 2. Just ran -> not due
    now = datetime.datetime(2026, 10, 13, 14, 0, tzinfo=datetime.UTC)
    db.set_state(rw.STATE_LAST_RESEARCH, now.isoformat())
    assert rw.research_due(db, now=now) is False

    # 3. 2 days later (default 3 days) -> not due
    two_days_later = now + datetime.timedelta(days=2)
    assert rw.research_due(db, now=two_days_later) is False

    # 4. 3 days and 2 hours later during active daytime (16:00 UTC) -> due
    three_days_two_hours = now + datetime.timedelta(days=3, hours=2)
    assert rw.research_due(db, now=three_days_two_hours) is True

    # 5. Due but during night quiet hours (23:00) and not > 1 day overdue -> deferred
    quiet_time = now + datetime.timedelta(days=3, hours=9)  # 23:00
    assert rw.research_due(db, now=quiet_time) is False

    # 6. Significantly overdue (> 4 days) -> fires even at night
    overdue_night = now + datetime.timedelta(days=4.5)
    assert rw.research_due(db, now=overdue_night) is True

    # 7. Disabled via config -> not due
    with patch("core.research_worker.get_config", return_value="0"):
        assert rw.research_due(db, now=three_days_two_hours) is False


def test_gather_opportunities_parsing_and_dedup():
    def mock_search(query, limit=4):
        q = query.lower()
        if "credit" in q:
            return (
                "Search results for query:\n"
                "1. Nebius AI Studio $100 Developer Grant\n"
                "   https://nebius.com/builders\n"
                "   Nebius offers $100 free inference credits for builders and open source developers.\n"
                "2. Google for Startups Cloud Program\n"
                "   https://cloud.google.com/startup\n"
                "   Eligible AI startups get up to $200k in Google Cloud and Gemini credits.\n"
            )
        elif "preview" in q:
            return (
                "Search results for query:\n"
                "1. Gemini 3.9 Developer Preview API Access\n"
                "   https://ai.google.dev/preview\n"
                "   Sign up for early developer preview of Gemini 3.9 with experimental tool calling.\n"
            )
        elif "hackathon" in q:
            return (
                "Search results for query:\n"
                "1. Global Agentic AI Hackathon 2026\n"
                "   https://lablab.ai/event/agentic-ai-2026\n"
                "   Build autonomous agents; free API credits provided for all participants with $50k prizes.\n"
            )
        return "No results."

    seen = {"https://cloud.google.com/startup"}
    categorized = rw.gather_opportunities(search_fn=mock_search, seen_urls=seen)

    assert len(categorized["credits"]) == 1
    assert categorized["credits"][0]["title"] == "Nebius AI Studio $100 Developer Grant"
    assert categorized["credits"][0]["url"] == "https://nebius.com/builders"
    assert "inference" in categorized["credits"][0]["fit"].lower() or "offset" in categorized["credits"][0]["fit"].lower()

    # Previews
    assert len(categorized["previews"]) >= 1
    assert "Gemini" in categorized["previews"][0]["title"]

    # Hackathons
    assert len(categorized["hackathons"]) >= 1
    assert "lablab.ai" in categorized["hackathons"][0]["url"]

    # Deduplication check: seen url was skipped
    all_urls = [it["url"] for cat in categorized.values() for it in cat]
    assert "https://cloud.google.com/startup" not in all_urls


def test_build_digest_html():
    findings = {
        "credits": [
            {"title": "OpenAI Builder Credits", "url": "https://platform.openai.com/credits",
             "snippet": "$500 credit grant for developers.", "category": "credits", "fit": "Can power AgentWork tests."}
        ],
        "previews": [],
        "free_tiers": [
            {"title": "Cloudflare Workers AI", "url": "https://developers.cloudflare.com/workers-ai",
             "snippet": "Free tier with 10k neurons daily.", "category": "free_tiers", "fit": "Zero-card inference."}
        ],
        "hackathons": []
    }

    # 1. Deterministic fallback
    html, summary = rw.build_digest_html(findings, "Oct 13, 2026", llm=None, name="Alex")
    assert "AI Opportunity Radar" in html
    assert "OpenAI Builder Credits" in html
    assert "Cloudflare Workers AI" in html
    assert "https://platform.openai.com/credits" in html
    assert "<script" not in html
    assert "<style" not in html
    assert "Found 2" in summary

    # 2. LLM synthesis
    mock_llm = MagicMock()
    mock_llm.chat.return_value = (
        "<h3>Free AI Credits</h3>"
        "<p>Here is a detailed report on builder grants and early previews tailored for AlwaysOnAgent and DeskAI.</p>"
    )
    llm_html, llm_summary = rw.build_digest_html(findings, "Oct 13, 2026", llm=mock_llm, name="Alex")
    assert "Here is a detailed report" in llm_html
    assert "Found 2" in llm_summary


def test_run_research_cycle_saves_canvas_memory_and_proactive():
    db, _ = make_test_db()
    memory = MemoryManager(db, llm=None, embedder=None)

    def fake_search(q, limit=4):
        return (
            "1. Groq Free Tier API Updates\n"
            "   https://groq.com/free\n"
            "   Groq provides ultra-fast Llama-3 inference with no credit card required.\n"
            "2. Autonomous Agents Hackathon\n"
            "   https://devpost.com/hackathons/agents-2026\n"
            "   Virtual hackathon for agentic workflows with free credits.\n"
        )

    mock_proactive = MagicMock()

    result = rw.run_research_cycle(
        db,
        memory=memory,
        llm=None,
        proactive=mock_proactive,
        search_fn=fake_search,
        force=True,
        name="Alex"
    )

    assert result is not None
    assert result["count"] >= 1
    doc_card = result["doc"]
    assert doc_card["id"] > 0
    assert "AI Opportunities:" in doc_card["title"]

    # 1. Canvas document verified
    canvas = Canvas(db, llm=None)
    doc = canvas.get(doc_card["id"])
    assert doc["title"] == doc_card["title"]
    assert doc["kind"] == "document"
    assert "Groq Free Tier" in doc["html"]

    # 2. Memory event verified
    events = [r[0] for r in db.get_connection().execute("SELECT content FROM mem_messages WHERE role = 'event'").fetchall()]
    assert any("AI research worker compiled opportunity digest" in ev for ev in events)

    # 3. Proactive notice delivered with canvas card
    assert mock_proactive.deliver.called
    p_args, p_kwargs = mock_proactive.deliver.call_args
    assert p_args[0] == "notice"
    assert "ai-research" in p_kwargs.get("topic", "")
    assert p_kwargs.get("canvas") == doc_card

    # 4. State updated
    latest_state = rw.get_latest_digest(db)
    assert latest_state["latest"]["doc_id"] == doc_card["id"]
    assert latest_state["latest"]["count"] == result["count"]
    assert db.get_state(rw.STATE_LAST_RESEARCH, "") != ""

    seen = json.loads(db.get_state(rw.STATE_SEEN_URLS, "[]"))
    assert "https://groq.com/free" in seen


def test_assistant_tool_research_ai_opportunities():
    db, ws = make_test_db()
    memory = MemoryManager(db, llm=None, embedder=None)

    from core.assistant_tools import AssistantTools
    tools = AssistantTools(db, memory, ws)

    with patch("core.research_worker.web_search") as mock_ws:
        mock_ws.return_value = (
            "1. Mistral Free Developer Tier\n"
            "   https://mistral.ai/free-tier\n"
            "   Ministral models available on the free tier without a credit card.\n"
        )
        reply = tools.call("research_ai_opportunities", {})
        assert "Research digest compiled with" in reply
        assert "Canvas #" in reply
        assert tools.canvas_shown is not None


def test_api_endpoints():
    db, _ws = make_test_db()
    memory = MemoryManager(db, llm=None, embedder=None)

    from interfaces.hud import state
    from interfaces.hud.companion import get_research_opportunities, run_research_now

    orig_db = state.db
    state.set_db(db)
    state.set_memory(memory)

    try:
        # Initial status
        init_status = get_research_opportunities()
        assert init_status["latest"] is None

        with patch("core.research_worker.web_search") as mock_search:
            mock_search.return_value = (
                "1. Google AI Studio Early Previews\n"
                "   https://aistudio.google.com/previews\n"
                "   Test experimental multimodal features and free API quota.\n"
            )
            run_res = run_research_now()
            assert run_res["status"] == "success"
            assert run_res["digest"]["count"] >= 1

        after_status = get_research_opportunities()
        assert after_status["latest"] is not None
        assert after_status["total_digests"] == 1
    finally:
        state.set_db(orig_db)


def test_an_empty_research_run_leaves_no_draft_event_or_notice():
    """2026-10-28 audit 7.6: every run made a Canvas draft and a memory event, even "Found 0 opportunities"."""
    db, _ = make_test_db()
    memory = MemoryManager(db, llm=None, embedder=None)
    proactive = MagicMock()
    result = rw.run_research_cycle(db, memory=memory, llm=None, proactive=proactive,
                                   search_fn=lambda q, limit=4: "", force=True, name="Alex")
    assert result["count"] == 0 and result["doc"] == {}
    assert Canvas(db, llm=None).list() == []
    assert not [m for m in memory._recent_rows() if "research worker" in m["content"]]
    assert not proactive.deliver.called and db.get_state(rw.STATE_LAST_RESEARCH)
