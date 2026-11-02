"""The Sunday growth review (core/growth_review.py, IDEAS #24): numbers checked against the data, a canvas draft
and one chat message with its card, the plain report when anything fails, last week's suggestions carried over."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import growth_review, push  # noqa: E402
from core.canvas import Canvas  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.proactive import Proactive  # noqa: E402
from core.weekly_report import STATE_REPORTS  # noqa: E402

REPORT = {
    "text": "📊 Week of Oct 5 – Oct 11\n\nWebsite\n• Visits: 154\n• Cards sent: +5 (11 in total)",
    "site": {"from": "2026-10-05", "to": "2026-10-11", "visits": 154,
             "cards_sent": {"change": 5, "total": 11}, "by_channel": [{"name": "TikTok", "visits": 61}]},
    "tiktok": {"views_total": 1630, "views_change": 911, "followers": 42},
    "pins": None, "failed": ["pinterest"],
}
GOOD = {"html": "<h3>How the week went</h3><p>Cards sent went up by 5, to 11 in total, and visits reached 154, "
                "61 of them from TikTok.</p><h3>Next week</h3><ol><li>Pin the best TikTok.</li></ol>",
        "summary": "Cards sent are up by 5 this week.", "suggestions": ["Pin the best TikTok."]}
INVENTED = dict(GOOD, html=GOOD["html"].replace("154", "154 (a 37% jump)"))


class FakeLLM:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return json.dumps(self.answers.pop(0)) if self.answers else None


def make_db(name):
    db = DatabaseManager(Path(tempfile.mkdtemp(prefix="review_")) / f"{name}.db")
    db.set_state(STATE_REPORTS, json.dumps([{"text": "last week's report: Visits: 98"}, REPORT]))
    return db


def test_numbers_must_come_from_the_data():
    data = growth_review.gather(make_db("numbers"), REPORT)
    assert growth_review.unsupported_numbers(GOOD["html"], data) == []
    assert growth_review.unsupported_numbers(INVENTED["html"], data) == ["37"]


def test_a_review_becomes_a_canvas_draft_and_one_message_with_its_card():
    db = make_db("good")
    pro = Proactive(db)
    out = growth_review.run(db, REPORT, FakeLLM(GOOD), proactive=pro, name="Alex")
    assert out and out["doc"]["title"] == "Growth review: week to Oct 11"
    doc = Canvas(db).get(out["doc"]["id"])
    assert "Cards sent went up by 5" in doc["html"]
    msg = pro.inbox()[-1]
    assert msg["kind"] == "review" and msg["canvas"]["id"] == doc["id"] and "Cards sent are up by 5" in msg["text"]
    assert out["text"].startswith("📊 Cards sent are up by 5") and REPORT["text"] in out["text"]
    # Next week's review gets these suggestions to look back on
    assert growth_review.gather(db, REPORT)["last_weeks_suggestions"] == ["Pin the best TikTok."]
    print("  - the review is a canvas draft plus one chat message carrying its card")


def test_invented_numbers_get_one_retry_then_the_plain_report_stands():
    db = make_db("invented")
    llm = FakeLLM(INVENTED, GOOD)
    assert growth_review.run(db, REPORT, llm, name="Alex") is not None and len(llm.calls) == 2
    assert "37" in llm.calls[1][-1]["content"]                       # told which number wasn't in the data
    db2 = make_db("invented-twice")
    assert growth_review.run(db2, REPORT, FakeLLM(INVENTED, INVENTED), name="Alex") is None
    assert Canvas(db2).list() == []
    assert growth_review.run(db2, dict(REPORT, failed=["site", "tiktok", "pinterest"]), FakeLLM(GOOD)) is None
    print("  - a review that keeps inventing numbers is dropped; the plain report goes out")


def test_a_task_that_announced_itself_gets_no_second_push(monkeypatch):
    db = make_db("push")
    watcher = push.TaskPushWatcher(db)
    tid = db.create_task(title="Weekly report", prompt="numbers", engine="weekly_report")
    db.update_task(tid, {"phase": "completed", "metadata": {"announced": "growth_review"}})
    sent = []
    monkeypatch.setattr(push, "send", lambda *a, **k: sent.append(a) or 1)
    assert watcher.check_once() == 0 and not sent


def test_the_review_gets_the_cleaned_site_numbers_and_a_partial_week_note():
    report = dict(REPORT, site=dict(REPORT["site"], hours_covered=48, top_sources=[
        {"name": "example-site.com", "visits": 17}, {"name": "(none)", "visits": 13},
        {"name": "google", "visits": 5}, {"name": "localhost", "visits": 1}]))
    data = growth_review.gather(make_db("cleaned"), report)
    assert [s["name"] for s in data["this_week"]["site"]["top_sources"]] == ["google"]
    assert data["notes"] and "only 2 of the 7 days" in data["notes"][0]


def test_proactive_weekly_numbers_and_line():
    db = make_db("proactive_stats")
    pro = Proactive(db)
    pool = pro.pool
    now = pool.now()

    # 9 sent: 6 answered, 1 👎, 2 ignored
    for i in range(6):
        cid = pool.add("curiosity", "question", f"q{i}", topic=f"topic{i}")
        pool.mark_sent(cid, 100 + i)
        pool.set_outcome(cid, "answered")
    cid_down = pool.add("open_loop", "notice", "notice down", topic="down topic")
    pool.mark_sent(cid_down, 200)
    pool.set_outcome(cid_down, "thumbs_down")
    for i in range(2):
        cid = pool.add("pattern", "question", f"ignored{i}", topic=f"patt{i}")
        pool.mark_sent(cid, 300 + i)
        pool.set_outcome(cid, "ignored")

    # 31 ideas not sent: 22 low score (lapsed), 5 repeats, 4 excluded
    for i in range(22):
        cid = pool.add("curiosity", "question", f"lapsed{i}", topic=f"lap{i}")
        pool.expire()
        with pool._conn() as conn:
            conn.execute("UPDATE proactive_candidates SET status = 'lapsed', reason = 'expired' WHERE id = ?", (cid,))
    for i in range(5):
        cid = pool.add("curiosity", "question", f"rep{i}", topic="rep topic")
        pool.drop(cid, f"repeat of 'old question {i}'")
    for i in range(4):
        cid = pool.add("interest", "notice", f"ex{i}", topic="excluded topic")
        pool.drop(cid, "excluded by owner preference")

    line = growth_review.proactive_summary(db)
    assert line == "proactive: 9 sent, 6 answered, 1 👎; 31 ideas not sent (low score 22, repeats 5, excluded 4)"

    data = growth_review.gather(db, REPORT)
    assert data["proactive"] == line
    assert data["this_week"]["proactive"] == line

    out = growth_review.run(db, REPORT, FakeLLM(GOOD), proactive=pro, name="Alex")
    assert out is not None
    doc = Canvas(db).get(out["doc"]["id"])
    assert line in doc["html"]
    assert line in out["text"]

