"""
Automated AI Research Worker: periodically searches for free AI credits, developer previews,
free tiers, and free AI hackathons fitting Alex's building work (AlwaysOnAgent, The Affirmation
Project, DeskAI, AgentWork / AgentComputerUse). Digests go to Canvas (a readable draft),
Memory (an event entry), and the Proactive Inbox (a notice with the Canvas card).
Zero-cost rule: strictly free-only, no credit card on file.
"""

import datetime
import html as html_lib
import json
import logging
import re
from collections.abc import Callable
from typing import Any

try:
    from core.canvas import Canvas
    from core.config import get_config
    from core.database import DatabaseManager, get_iso_now
    from core.llm_usage import usage_purpose
    from core.web_tools import web_search
except ImportError:
    from canvas import Canvas  # type: ignore[no-redef]
    from config import get_config  # type: ignore[no-redef]
    from database import DatabaseManager, get_iso_now  # type: ignore[no-redef]
    from llm_usage import usage_purpose  # type: ignore[no-redef]
    from web_tools import web_search  # type: ignore[no-redef]

logger = logging.getLogger("ResearchWorker")

STATE_LAST_RESEARCH = "last_ai_research_at"
STATE_DIGESTS = "ai_research_digests"
STATE_SEEN_URLS = "ai_research_seen_urls"

DEFAULT_INTERVAL_DAYS = 3
MAX_SEEN_URLS = 600
KEEP_DIGESTS = 15

CATEGORIES = ("credits", "previews", "free_tiers", "hackathons")

CATEGORY_TITLES = {
    "credits": "Free AI Credits & Builder Grants",
    "previews": "Developer Previews & Early Access",
    "free_tiers": "Generous Free Tiers (Zero-Card Friendly)",
    "hackathons": "Free AI Hackathons & Competitions",
}

DEFAULT_QUERIES = [
    # Free AI Credits
    {"query": '"free AI credits" (startup OR builder OR developer OR grant) 2026', "category": "credits"},
    {"query": 'cloud AI credits "apply" (developer OR builder) free 2026', "category": "credits"},
    # Developer Previews
    {"query": '"developer preview" (AI OR LLM OR model OR API) early access 2026', "category": "previews"},
    {"query": 'AI developer beta program early access API 2026', "category": "previews"},
    # Free Tiers
    {"query": '"free tier" AI LLM API "no credit card" 2026', "category": "free_tiers"},
    {"query": 'generous free tier AI inference API limits 2026', "category": "free_tiers"},
    # Free AI Hackathons
    {"query": 'free AI hackathon 2026 (Devpost OR lablab.ai OR virtual)', "category": "hackathons"},
    {"query": 'upcoming AI hackathons with prizes free registration 2026', "category": "hackathons"},
]

ALEX_BUILDING_CONTEXT = (
    "Alex Rivera builds AlwaysOnAgent (24/7 personal autonomous AI daemon in Python with SQLite, "
    "local-first memory, and DeskAI Android companion), Example Site (example-site.com: "
    "anonymous encouragement cards with automated FFmpeg narration videos for TikTok and Pinterest automation), "
    "and AgentWork / AgentComputerUse (autonomous coding and desktop automation). "
    "Hard rules: strictly free tiers only, no credit card or billing accounts on file. "
    "Uses free cloud quotas (Gemini Flash-Lite/Flash, Groq, Cloudflare Workers AI, Mistral) and local CPU models "
    "(Ollama Qwen 0.6B embedder, Gemma 4B on an HP EliteDesk mini PC with 20 GB RAM, no GPU)."
)

WRITER_SYSTEM_PROMPT = (
    "You write {name}'s periodic AI Opportunity Radar digest for his building stack. "
    "Alex's stack: AlwaysOnAgent, Example Site, DeskAI Android app. "
    "Rule: strictly free-only, zero credit card required. "
    "Write an engaging, structured HTML fragment using ONLY: h3, p, ul, ol, li, strong, em, a. "
    "No <html>, <body>, <style>, <script>, or markdown code blocks. "
    "Group items into the 4 sections: "
    "1) Free AI Credits & Builder Grants "
    "2) Developer Previews & Early Access "
    "3) Generous Free Tiers (Zero-Card Friendly) "
    "4) Free AI Hackathons Fitting Your Stack. "
    "For each item, give: title with link (<a href=\"...\">...</a>), key offering / quota / eligibility, "
    "and a short sentence on why it fits Alex's projects. Keep it punchy and practical."
)


def research_due(db: DatabaseManager, now: datetime.datetime | None = None) -> bool:
    """True when the research cycle is enabled and the configured interval has elapsed."""
    enabled = get_config("AI_RESEARCH_ENABLED", "1").strip().lower()
    if enabled in ("0", "false", "no", "off"):
        return False

    try:
        interval_days = float(get_config("AI_RESEARCH_INTERVAL_DAYS", str(DEFAULT_INTERVAL_DAYS)))
    except ValueError:
        interval_days = float(DEFAULT_INTERVAL_DAYS)

    now = now or datetime.datetime.now().astimezone()
    last_iso = db.get_state(STATE_LAST_RESEARCH, "")
    if not last_iso:
        return True

    try:
        last_dt = datetime.datetime.fromisoformat(last_iso)
        if not last_dt.tzinfo:
            last_dt = last_dt.astimezone()
    except (TypeError, ValueError):
        return True

    elapsed_seconds = (now - last_dt).total_seconds()
    if elapsed_seconds < interval_days * 86400:
        return False

    # Avoid firing during night quiet hours (22:00 to 08:30) unless significantly overdue (> 1 day past due)
    return not ((now.hour < 8 or now.hour == 8 and now.minute < 30 or now.hour >= 22) and elapsed_seconds < (interval_days + 1) * 86400)


def _parse_search_results(raw_output: str, category: str) -> list[dict[str, str]]:
    """Extracts structured items from web_search text output."""
    items = []
    current_title = ""
    current_url = ""
    current_snippet: list[str] = []

    for line in (raw_output or "").splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        m_head = re.match(r"^\d+\.\s+(.+)$", line_s)
        if m_head:
            if current_title and current_url:
                items.append({
                    "title": current_title,
                    "url": current_url,
                    "snippet": " ".join(current_snippet).strip(),
                    "category": category,
                })
            current_title = m_head.group(1).strip()
            current_url = ""
            current_snippet = []
        elif current_title and not current_url and re.match(r"^https?://", line_s):
            current_url = line_s
        elif current_title:
            current_snippet.append(line_s)

    if current_title and current_url:
        items.append({
            "title": current_title,
            "url": current_url,
            "snippet": " ".join(current_snippet).strip(),
            "category": category,
        })
    return items


def gather_opportunities(
    search_fn: Callable[[str, int], str] | None = None,
    seen_urls: set[str] | None = None,
    max_per_cat: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """Runs targeted web searches for credits, previews, free tiers, and hackathons."""
    search_fn = search_fn or web_search
    seen_urls = seen_urls or set()
    current_seen: set[str] = set()

    categorized: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORIES}

    for q in DEFAULT_QUERIES:
        cat = q["category"]
        if len(categorized[cat]) >= max_per_cat:
            continue
        query_text = q["query"]
        try:
            raw = search_fn(query_text, 4)
        except Exception as e:
            logger.warning(f"Research search query failed for '{query_text}': {e}")
            continue

        parsed = _parse_search_results(raw, cat)
        for item in parsed:
            url = item["url"]
            if url in seen_urls or url in current_seen:
                continue
            # Basic sanity filter: avoid search homepages or empty snippets
            if len(item["snippet"]) < 15 or url.count("/") <= 2:
                continue
            current_seen.add(url)
            fit_notes = evaluate_fit(item)
            item["fit"] = fit_notes
            categorized[cat].append(item)
            if len(categorized[cat]) >= max_per_cat:
                break

    return categorized


def evaluate_fit(item: dict[str, Any]) -> str:
    """Provides contextual rationale on why an item fits Alex's building stack."""
    cat = item.get("category", "")
    title = (item.get("title", "") + " " + item.get("snippet", "")).lower()

    if cat == "credits":
        if any(w in title for w in ("cloud", "gpu", "inference", "api", "hosting")):
            return "Can offset model inference, media rendering, or AgentWork branch task workloads."
        return "Builder grant/credits program suitable for bootstrap experiments."
    elif cat == "previews":
        if any(w in title for w in ("gemini", "google", "flash")):
            return "Directly relevant to CloudLLM's primary Gemini route in AlwaysOnAgent."
        elif any(w in title for w in ("reasoning", "coding", "agent")):
            return "Potential candidate for AgentWork coding engine or fast chat tool calls."
        return "Early API preview to expand free provider routes in CloudLLM."
    elif cat == "free_tiers":
        if any(w in title for w in ("embedding", "vector")):
            return "Potential zero-card embedding fallback for semantic memory or tool routing."
        elif any(w in title for w in ("groq", "fast", "speed", "tokens")):
            return "Zero-card inference option fitting the agent's secondary failover tier."
        return "No credit card required; fits AlwaysOnAgent's zero-cost infrastructure."
    elif cat == "hackathons":
        if any(w in title for w in ("agent", "assistant", "mobile", "productivity")):
            return "Strong alignment with AlwaysOnAgent / DeskAI Android assistant concepts."
        elif any(w in title for w in ("multimodal", "video", "content", "creative")):
            return "Strong alignment with Example Site affirmation video automation."
        return "Free entry with developer perks, free compute credits, and builder recognition."
    return "Fits Alex's autonomous agent and open builder ecosystem."


def _empty_digest(date_label: str) -> tuple[str, str]:
    """The digest when the scan found nothing new."""
    html = (
        f"<p>No new free AI opportunities met the zero-cost criteria during the scan on {date_label}. "
        "All previously reviewed programs remain active in state memory.</p>"
    )
    return html, "AI research scan complete: no new opportunities found."


def _llm_digest(findings: dict[str, list[dict[str, Any]]], date_label: str,
                llm: Any, name: str) -> str | None:
    """The LLM-written digest, or None when the model fails or its answer is too thin."""
    try:
        prompt_data = {
            "date": date_label,
            "findings": findings,
            "context": ALEX_BUILDING_CONTEXT,
        }
        system = WRITER_SYSTEM_PROMPT.format(name=name)
        user_msg = (
            f"Date: {date_label}\n"
            "Here are the gathered opportunities from live web research:\n"
            f"{json.dumps(prompt_data, ensure_ascii=False, indent=1)}\n\n"
            "Write the structured HTML digest now."
        )
        with usage_purpose("research_worker"):
            raw = llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
                max_tokens=2500,
                temperature=0.4,
                timeout=90
            )
        if raw and len(re.sub(r"<[^>]+>", "", raw).strip()) > 100:
            return re.sub(r"^```[a-z]*\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    except Exception as e:
        logger.warning(f"LLM synthesis for research digest failed, using deterministic formatter: {e}")
    return None


def _deterministic_digest(findings: dict[str, list[dict[str, Any]]], date_label: str) -> str:
    """The plain HTML builder fallback: one section per category, one list item per opportunity."""
    lines = [
        (f"<p><strong>AI Opportunity Radar</strong> compiled on <em>{html_lib.escape(date_label)}</em>. "
        f"Filtered specifically for your building work on AlwaysOnAgent, Example Site, "
        "and DeskAI, adhering strictly to zero billing and free tiers.</p>")
    ]
    for cat in CATEGORIES:
        items = findings.get(cat, [])
        cat_title = CATEGORY_TITLES.get(cat, cat.title())
        lines.append(f"<h3>{html_lib.escape(cat_title)}</h3>")
        if not items:
            lines.append("<p><em>No new opportunities surfaced in this category during this pass.</em></p>")
            continue
        lines.append("<ul>")
        for it in items:
            t = html_lib.escape(it.get("title", "Opportunity"))
            u = html_lib.escape(it.get("url", "#"))
            s = html_lib.escape(it.get("snippet", ""))
            fit = html_lib.escape(it.get("fit", ""))
            lines.append(
                f"<li>"
                f"<a href=\"{u}\" target=\"_blank\"><strong>{t}</strong></a><br>"
                f"{s}<br>"
                f"<strong>Stack Fit:</strong> <em>{fit}</em>"
                f"</li>"
            )
        lines.append("</ul>")
    lines.append(
        "<p><strong>Next Steps:</strong> Review applicable credit grants or developer previews above. "
        "Ask in chat to draft applications, inspect terms, or register for upcoming hackathons.</p>"
    )
    return "\n".join(lines)


def build_digest_html(
    findings: dict[str, list[dict[str, Any]]],
    date_label: str,
    llm: Any = None,
    name: str = "Alex"
) -> tuple[str, str]:
    """Generates clean HTML fragment for the Canvas draft and a short summary."""
    total_count = sum(len(items) for items in findings.values())
    if total_count == 0:
        return _empty_digest(date_label)

    summary = f"Found {total_count} free AI credits, developer previews, free tiers, and hackathons fitting your building stack."
    if llm is not None:
        clean_html = _llm_digest(findings, date_label, llm, name)
        if clean_html:
            return clean_html, summary
    return _deterministic_digest(findings, date_label), summary


def get_latest_digest(db: DatabaseManager) -> dict[str, Any]:
    """Retrieves the latest compiled digest metadata and history."""
    try:
        history = json.loads(db.get_state(STATE_DIGESTS, "[]") or "[]")
    except ValueError:
        history = []
    last_run = db.get_state(STATE_LAST_RESEARCH, "")
    latest = history[-1] if history else None
    return {
        "latest": latest,
        "last_run_at": last_run,
        "total_digests": len(history),
        "history": history,
    }


def _load_seen_urls(db: DatabaseManager) -> set[str]:
    """The URLs already told about, from state (empty on a bad read)."""
    try:
        return set(json.loads(db.get_state(STATE_SEEN_URLS, "[]") or "[]"))
    except ValueError:
        return set()


def _save_canvas(db: DatabaseManager, llm: Any, doc_title: str, html_content: str,
                 log: logging.Logger) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Saves the digest as a Canvas document; (doc, card), both empty when it fails."""
    canvas = Canvas(db, llm)
    try:
        doc = canvas.create_from(
            title=doc_title,
            kind="document",
            fragment=html_content,
            note="written by automated AI research worker"
        )
        return doc, {"id": doc["id"], "title": doc["title"], "kind": doc["kind"], "version": doc["version"]}
    except Exception as e:
        log.error(f"Failed to create canvas document for AI research digest: {e}")
        return None, {}


def _record_memory(memory: Any, doc_title: str, total_count: int, name: str,
                   doc: dict[str, Any] | None, log: logging.Logger) -> None:
    """Records the digest event in long-term memory (skipped when there is no memory)."""
    if memory is None:
        return
    try:
        mem_text = (
            f"AI research worker compiled opportunity digest: '{doc_title}'. "
            f"Found {total_count} opportunities across free credits, developer previews, free tiers, and hackathons "
            f"fitting {name}'s building work. Canvas #{doc['id'] if doc else 'n/a'}."
        )
        memory.record_event(mem_text)
    except Exception as e:
        log.warning(f"Could not record research event in memory: {e}")


def _deliver_notice(proactive: Any, summary: str, doc: dict[str, Any] | None, card: dict[str, Any],
                    log: logging.Logger) -> None:
    """Tells Alex about the digest: a pooled candidate when a real pool is available, else a direct notice."""
    if proactive is None:
        return
    notice_msg = f"{summary} Open the Canvas draft to explore them."
    pool = getattr(proactive, "pool", None)
    try:
        from core.candidates import CandidatePool
        has_pool = isinstance(pool, CandidatePool)
    except ImportError:
        has_pool = False

    if has_pool:
        assert pool is not None  # has_pool is True only when pool is a CandidatePool
        try:
            pool.add("research", "notice", notice_msg, topic="AI opportunities",
                     why="compiled opportunity digest on the away shift",
                     ref=f"canvas:{doc['id']}" if doc else "",
                     urgency="today")
        except Exception as e:
            log.warning(f"Could not queue research candidate in pool: {e}")
    elif card:
        try:
            proactive.deliver("notice", notice_msg, topic="ai-research", canvas=card)
        except Exception as e:
            log.warning(f"Could not deliver proactive research notice: {e}")


def _record_digest_state(db: DatabaseManager, now_iso: str, date_label: str, doc_title: str,
                         doc: dict[str, Any] | None, total_count: int, summary: str,
                         seen_urls: set[str], findings: dict[str, list[dict[str, Any]]]) -> None:
    """Updates the tracking state: last run, the seen URLs, and the digest history."""
    db.set_state(STATE_LAST_RESEARCH, now_iso)
    new_urls = {it["url"] for items in findings.values() for it in items if it.get("url")}
    updated_seen = list(seen_urls | new_urls)[-MAX_SEEN_URLS:]
    db.set_state(STATE_SEEN_URLS, json.dumps(updated_seen, ensure_ascii=False))
    try:
        digests = json.loads(db.get_state(STATE_DIGESTS, "[]") or "[]")
    except ValueError:
        digests = []
    digests.append({
        "at": now_iso,
        "date": date_label,
        "title": doc_title,
        "doc_id": doc["id"] if doc else None,
        "count": total_count,
        "summary": summary,
    })
    db.set_state(STATE_DIGESTS, json.dumps(digests[-KEEP_DIGESTS:], ensure_ascii=False))


def _finish_cycle(db: DatabaseManager, llm: Any, memory: Any, proactive: Any,
                    findings: dict[str, list[dict[str, Any]]], total_count: int, date_label: str,
                    seen_urls: set[str], log: logging.Logger, name: str) -> dict[str, Any]:
    """Steps 2-6 of the cycle: build the digest, save it to Canvas and memory, notify, update state."""
    html_content, summary = build_digest_html(findings, date_label, llm=llm, name=name)

    # 1. Save to Canvas
    doc_title = f"AI Opportunities: {date_label}"
    doc, card = _save_canvas(db, llm, doc_title, html_content, log)

    # 2. Record to Memory
    _record_memory(memory, doc_title, total_count, name, doc, log)

    # 3. Proactive Notification: pool candidate if real pool available, else direct deliver
    _deliver_notice(proactive, summary, doc, card, log)

    # 4. State updates
    _record_digest_state(db, get_iso_now(), date_label, doc_title, doc, total_count, summary, seen_urls, findings)

    log.info(f"AI research worker completed: {total_count} opportunities recorded in Canvas #{doc['id'] if doc else 'n/a'}")
    return {
        "doc": card,
        "summary": summary,
        "count": total_count,
        "opportunities": findings,
    }


def run_research_cycle(
    db: DatabaseManager,
    memory: Any = None,
    llm: Any = None,
    proactive: Any = None,
    log: logging.Logger | None = None,
    search_fn: Callable[[str, int], str] | None = None,
    force: bool = False,
    name: str = "Alex"
) -> dict[str, Any] | None:
    """Runs one research cycle: gather opportunities, build the digest, save it to Canvas and memory,
    deliver a proactive notice, and update the tracking state. None when no cycle is due."""
    log = log or logger
    if not force and not research_due(db):
        return None

    now = datetime.datetime.now().astimezone()
    date_label = f"{now:%b} {now.day}, {now.year}"
    seen_urls = _load_seen_urls(db)

    log.info("Starting automated AI research worker cycle...")
    try:
        from core.events import publish_activity
        publish_activity("step", label="Scanning for free AI opportunities", step="research_opportunities")
    except ImportError:
        pass
    findings = gather_opportunities(search_fn=search_fn, seen_urls=seen_urls)
    total_count = sum(len(items) for items in findings.values())
    if total_count == 0:
        # Nothing new: no Canvas draft, no memory event, no notice (each empty run left all three; 2026-10-28 audit 7.6)
        db.set_state(STATE_LAST_RESEARCH, get_iso_now())
        log.info("AI research worker: no new opportunities this time")
        return {"doc": {}, "summary": "No new opportunities.", "count": 0, "opportunities": findings}

    return _finish_cycle(db, llm, memory, proactive, findings, total_count, date_label, seen_urls, log, name)
