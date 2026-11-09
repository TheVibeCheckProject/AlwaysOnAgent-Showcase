"""Web research for the open-loops check (core/open_loops.py §3).

Split out of core/open_loops.py on 2026-11-03; core/open_loops re-exports the public names for backwards compatibility.
Moved into core/open_loops/ on 2026-11-04.
"""

import re

from .open_loops_constants import PAGE_CHARS, PAGE_TEXT, RESEARCH_PAGES, logger


def _default_research(query: str) -> list[dict[str, str]]:
    """The agent's own web search (same providers as chat's web_search). Each result carries its page text under
    "page" when the search returned it (Tavily); otherwise the top RESEARCH_PAGES results are read."""
    from core.web_tools import fetch_webpage, search_results
    results = []
    read = 0
    for r in search_results(query, limit=5, raw=True)[2]:
        r = dict(r)
        if r.get("raw_content"):
            r["page"] = r.pop("raw_content")
        elif read < RESEARCH_PAGES and str(r.get("url") or "").startswith("http"):
            try:
                r["page"] = fetch_webpage(r["url"], max_chars=PAGE_TEXT)
                read += 1
            except Exception as e:
                logger.info(f"open loops: could not read {r.get('url')}: {e}")
        results.append(r)
    return results


def _excerpt(text: str, terms: list[str], budget: int = PAGE_CHARS) -> str:
    """The parts of a long page around the words that matter: on the permit page the decision was 1,400 characters
    in, past the page header, and a 33-page application PDF came back whole."""
    text = " ".join(str(text or "").split())
    if len(text) <= budget:
        return text
    low, spans = text.lower(), []
    for t in {t.lower() for t in terms if len(t) > 3}:
        for m in re.finditer(re.escape(t), low):
            spans.append((max(0, m.start() - 250), m.end() + 250))
    if not spans:
        return text[:budget]
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    # Where the terms crowd together first: the docket entries, not the header that names the case once
    merged.sort(key=lambda s: -sum(low.count(t.lower(), s[0], s[1]) for t in terms if len(t) > 3))
    out, used = [], 0
    for a, b in merged:
        if used >= budget:
            break
        piece = text[a:min(b, a + budget - used)]
        out.append((a, piece))
        used += len(piece)
    return " … ".join(p for _, p in sorted(out))
