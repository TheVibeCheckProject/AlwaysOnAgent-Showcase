"""
The Sunday growth review: the first workflow (docs/IDEAS.md #24)
================================================================
After the weekly numbers report (core/weekly_report.py) the agent turns the numbers into a short review the owner
reads and refines in the canvas:

  gather  this week's numbers, the last few weeks' reports, the growth plan (VibeStats' GROWTH_PLAN.md: the goal,
          the status table, what we won't do), what the agent did this week (posts, pins, failures), and last
          week's suggestions
  write   one model call: how the week went (cards sent first), what brought people, what's stuck, last week's
          suggestions and whether they moved anything, and at most 3 things to do next week
  check   every number in the review must appear in the gathered data; one retry, else the plain report stands
  draft   a canvas document ("Growth review: week to Oct 11"), refined in chat like any draft
  tell    one message in the chat with the draft card, and one push (the report task's own push is skipped)

Nothing is acted on: the suggestions are text, and the owner asks for any of them in chat as usual. If anything
here fails, the plain report goes out exactly as before.
"""

import datetime
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from core.canvas import Canvas
    from core.config import VIBESTATS_DIR
    from core.llm_usage import usage_purpose
    from core.weekly_report import NOT_SOURCES, STATE_REPORTS
except ImportError:
    from canvas import Canvas
    from config import VIBESTATS_DIR
    from llm_usage import usage_purpose
    from weekly_report import NOT_SOURCES, STATE_REPORTS

logger = logging.getLogger("GrowthReview")
STATE_REVIEWS = "growth_reviews"
KEEP_REVIEWS = 12
MAX_SUGGESTIONS = 3
PLAN_SECTIONS = ("The plan in one screen", "Status", "What we won't do")

WRITER = (
    "You write {name}'s weekly growth review for example-site.com, a site where people send free "
    "encouragement cards. The goal of the growth plan is more people sending cards. Write to {name} directly, warm "
    "and plain, at most 300 words, as an HTML fragment using only h3, p, ul, ol, li, strong and em.\n"
    "Sections, in this order (leave one out if there's nothing for it):\n"
    "- How the week went: cards sent first, then visits, TikTok, Pinterest and subscribers.\n"
    "- What brought people: the channels and sources in the data.\n"
    "- What's stuck: anything waiting or failing (from the plan's status and the agent's activity).\n"
    "- Last week's suggestions: if there were any, say plainly whether the numbers moved after them.\n"
    "- Next week: at most {max} numbered suggestions that fit the plan and its 'what we won't do' list, each "
    "doable with the agent's tools or a little of {name}'s time. Each must be a specific change or experiment "
    "aimed at cards sent and tied to a number in the data (what to try, and which number should move). Never "
    "'keep doing X' or 'check on Y': if nothing specific is worth trying, give fewer.\n"
    "Rules: use only numbers that appear in the data, exactly as given; never compute percentages, averages, "
    "totals or differences yourself (the data already has the changes). If a number is missing, say it's missing. "
    "Don't guess why something happened unless the data shows it; mark any guess as a guess. One post or one day "
    "is an example, not a pattern.Repeat every note "
    "in 'notes' where it applies (e.g. visits covering only part of the week).\n"
    'Answer as JSON: {{"html": "...", "summary": "one sentence for the chat message", '
    '"suggestions": ["...", ...]}}'
)


# ---------------------------------------------------------------------------------------------------- gather
def _plan_excerpt(path: Optional[Path] = None, limit: int = 3000) -> str:
    """The parts of the growth plan the review needs, not the whole file."""
    path = path or Path(VIBESTATS_DIR) / "GROWTH_PLAN.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    parts = []
    for section in PLAN_SECTIONS:
        m = re.search(rf"^## {re.escape(section)}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        if m:
            parts.append(f"## {section}\n{m.group(1).strip()}")
    return "\n\n".join(parts)[:limit]


def _activity(db, days: int = 7) -> List[str]:
    """What the agent did this week: its tasks, one line each (title and how it ended)."""
    since = (datetime.datetime.now().astimezone() - datetime.timedelta(days=days)).isoformat()
    lines = []
    for t in db.list_tasks(limit=200):
        if (t.get("created_at") or "") >= since and t.get("phase") in ("completed", "failed"):
            lines.append(f"{(t.get('created_at') or '')[:10]} {t.get('title')}: {t.get('phase')}")
    return lines[:40]


def _state_list(db, key: str) -> List[Dict[str, Any]]:
    try:
        return json.loads(db.get_state(key, "[]") or "[]")
    except ValueError:
        return []


def _site_for_review(site: Any, days: int) -> Tuple[Any, List[str]]:
    """The site numbers as the plain report shows them: no self, empty or localhost "sources" (they aren't where
    visitors came from), and a note when visits cover only part of the week."""
    if not isinstance(site, dict):
        return site, []
    site = dict(site)
    site["top_sources"] = [s for s in site.get("top_sources", []) if s.get("name") not in NOT_SOURCES]
    notes = []
    covered = int(site.get("hours_covered") or 0)
    if covered and covered < days * 24:
        notes.append(f"Visits cover only {covered // 24} of the {days} days (they're collected nightly), so the "
                     f"visit numbers are partial, not a drop.")
    return site, notes


def format_proactive_summary(counts: Dict[str, Any], msg_thumbs_down: int = 0) -> str:
    status = counts.get("status") or {}
    outcome = counts.get("outcome") or {}
    dropped_reasons = counts.get("dropped_reason") or {}

    sent = status.get("sent", 0)
    answered = outcome.get("answered", 0)
    thumbs_down = outcome.get("thumbs_down", 0) + outcome.get("less_like_this", 0) + outcome.get("👎", 0)
    if msg_thumbs_down > thumbs_down:
        thumbs_down = msg_thumbs_down

    sent_parts = [f"{sent} sent"]
    if answered > 0:
        sent_parts.append(f"{answered} answered")
    if thumbs_down > 0:
        sent_parts.append(f"{thumbs_down} 👎")
    sent_str = ", ".join(sent_parts)

    not_sent = sum(v for k, v in status.items() if k != "sent")

    reason_map: Dict[str, int] = {}
    for reason, count in dropped_reasons.items():
        r = (reason or "").lower()
        if "repeat" in r:
            k = "repeats"
        elif "exclude" in r:
            k = "excluded"
        elif "discuss" in r:
            k = "discussed"
        elif "ranker" in r:
            k = "ranker"
        elif "stale" in r:
            k = "stale"
        elif "score" in r or "expire" in r or "lapsed" in r:
            k = "low score"
        else:
            k = reason.strip()[:20]
        reason_map[k] = reason_map.get(k, 0) + count

    lapsed = status.get("lapsed", 0)
    if lapsed > 0:
        reason_map["low score"] = reason_map.get("low score", 0) + lapsed

    if not_sent > 0 and reason_map:
        sorted_reasons = sorted(reason_map.items(), key=lambda x: -x[1])
        breakdown = ", ".join(f"{k} {v}" for k, v in sorted_reasons if v > 0)
        breakdown_str = f" ({breakdown})" if breakdown else ""
    else:
        breakdown_str = ""

    return f"proactive: {sent_str}; {not_sent} ideas not sent{breakdown_str}"


def proactive_summary(db, days: int = 7) -> str:
    if db is None:
        return ""
    try:
        from core.candidates import CandidatePool
    except ImportError:
        try:
            from candidates import CandidatePool
        except ImportError:
            return ""
    try:
        pool = CandidatePool(db)
        counts = pool.counts(days=days)
    except Exception:
        return ""

    msg_thumbs = 0
    try:
        since = (pool.now() - datetime.timedelta(days=days)).isoformat()
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT feedback, COUNT(*) FROM proactive_messages WHERE created_at >= ? "
                "AND feedback IN ('thumbs_down', 'less_like_this', '👎') GROUP BY feedback",
                (since,)).fetchall()
            msg_thumbs = sum(r[1] for r in rows)
    except Exception:
        pass

    return format_proactive_summary(counts, msg_thumbs_down=msg_thumbs)


def gather(db, report: Dict[str, Any], days: int = 7) -> Dict[str, Any]:
    reports = _state_list(db, STATE_REPORTS)
    previous = [r.get("text", "") for r in reports[:-1][-4:]]       # the last item is this week's, just stored
    reviews = _state_list(db, STATE_REVIEWS)
    site, notes = _site_for_review(report.get("site"), days)
    pro_line = report.get("proactive") or proactive_summary(db, days)
    return {
        "notes": notes,
        "this_week": {"site": site, **({"proactive": pro_line} if pro_line else {}),
                      **{k: report.get(k) for k in ("text", "tiktok", "pins", "failed")}},
        "previous_weeks": previous,
        "growth_plan": _plan_excerpt(),
        "agent_activity": _activity(db, days),
        "last_weeks_suggestions": reviews[-1].get("suggestions", []) if reviews else [],
        **({"proactive": pro_line} if pro_line else {}),
    }


# ---------------------------------------------------------------------------------------------------- check
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set:
    return {n.replace(",", "") for n in _NUMBER.findall(text or "")}


def unsupported_numbers(html: str, data: Dict[str, Any]) -> List[str]:
    """Numbers in the review that the gathered data doesn't contain (1 to MAX_SUGGESTIONS number the list)."""
    allowed = _numbers(json.dumps(data, ensure_ascii=False)) | {str(i) for i in range(1, MAX_SUGGESTIONS + 1)}
    text = re.sub(r"<[^>]+>", " ", html or "")
    return sorted(_numbers(text) - allowed, key=lambda n: (len(n), n))


# ---------------------------------------------------------------------------------------------------- write
def _parse(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        out = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(out, dict) or len(re.sub(r"<[^>]+>", "", str(out.get("html") or "")).strip()) < 40:
        return None
    out["suggestions"] = [str(s).strip() for s in (out.get("suggestions") or []) if str(s).strip()][:MAX_SUGGESTIONS]
    out["summary"] = str(out.get("summary") or "").strip()
    return out


def write(llm, data: Dict[str, Any], name: str = "the owner") -> Optional[Dict[str, Any]]:
    """The review, checked: None if the model fails or keeps using numbers the data doesn't have."""
    system = WRITER.format(name=name, max=MAX_SUGGESTIONS)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "The data:\n" + json.dumps(data, ensure_ascii=False, indent=1)}]
    for attempt in range(2):
        with usage_purpose("growth_review"):
            raw = llm.chat(messages, max_tokens=1500, temperature=0.3, json_mode=True, timeout=90)
        review = _parse(raw)
        if review is None:
            logger.warning("Growth review: the model's answer wasn't usable")
            return None
        bad = unsupported_numbers(review["html"], data)
        if not bad:
            return review
        logger.warning(f"Growth review used numbers not in the data: {bad}" + ("; retrying" if not attempt else ""))
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": f"These numbers aren't in the data: {', '.join(bad)}. Rewrite the "
                                                 "review using only numbers that appear in the data, exactly as "
                                                 "given. Same JSON."}]
    return None


# ---------------------------------------------------------------------------------------------------- run
def _week_label(report: Dict[str, Any]) -> str:
    to = ((report.get("site") or {}).get("to") or datetime.date.today().isoformat())[:10]
    try:
        d = datetime.date.fromisoformat(to)
        return f"{d:%b} {d.day}"
    except ValueError:
        return to


def run(db, report: Dict[str, Any], llm, proactive: Any = None, name: str = "the owner",
        days: int = 7) -> Optional[Dict[str, Any]]:
    """Writes, checks, drafts and announces the review. Returns {"doc", "summary", "text"} or None (then the plain
    report stands, with its usual push)."""
    if llm is None or len(report.get("failed") or []) == 3:
        return None
    data = gather(db, report, days)
    review = write(llm, data, name)
    if review is None:
        return None
    week = _week_label(report)
    pro_line = report.get("proactive") or proactive_summary(db, days)
    html = review["html"]
    if pro_line and pro_line not in html:
        html += f"<p><em>{pro_line}</em></p>"
    doc = Canvas(db, llm).create_from(f"Growth review: week to {week}", "document", html,
                                      "written by the Sunday review")
    card = {"id": doc["id"], "title": doc["title"], "kind": doc["kind"], "version": doc["version"]}
    summary = review["summary"] or f"Your growth review for the week to {week} is ready."
    if proactive is not None:
        proactive.deliver("review", summary + " Open the draft to read it, or ask me to change anything.",
                          topic="growth-review", canvas=card)
    reviews = _state_list(db, STATE_REVIEWS)
    reviews.append({"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "week": week,
                    "doc_id": doc["id"], "summary": summary, "suggestions": review["suggestions"]})
    db.set_state(STATE_REVIEWS, json.dumps(reviews[-KEEP_REVIEWS:], ensure_ascii=False))
    rep_text = report.get("text", "")
    if pro_line and pro_line not in rep_text:
        rep_text = rep_text + f"\n\nProactive\n• {pro_line}"
    text = (f"📊 {summary}\nThe full review is in the canvas: \"{doc['title']}\".\n\n" + rep_text)
    return {"doc": card, "summary": summary, "text": text}
