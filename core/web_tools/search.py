"""Web search and research (web_tools §6). Split out of core/web_tools.py 2026-11-03.

web_search: results with sources for the chat; web_research: several searches and pages read,
for a question that needs digging.
"""
import concurrent.futures
import logging
import re

try:
    from .providers import TOP_PAGE_CHARS, _brave, _ddg_lite, _serper, _tavily, _wikipedia
except ImportError:  # not imported as part of the core.web_tools package
    from core.web_providers import TOP_PAGE_CHARS, _brave, _ddg_lite, _serper, _tavily, _wikipedia  # type: ignore[no-redef]

try:
    from .text import _trim_to_sentence
except ImportError:  # not imported as part of the core.web_tools package
    from core.web_text import _trim_to_sentence  # type: ignore[no-redef]

from core.observation_sanitizer import collapse_whitespace, strip_tracking_params

logger = logging.getLogger("AlwaysOnAgent.WebTools")

def _wt():
    """core.web_tools, looked up at call time. Tests patch names on that module (w._get, w._geocode,
    w._assert_public_host, ...), so every call to a former web_tools function goes through it here;
    a direct import-time binding would silently ignore the patch."""
    try:
        from core import web_tools
        return web_tools
    except ImportError:  # running from core/ directly
        import web_tools  # type: ignore[no-redef]
        return web_tools


def _search_providers(query: str, limit: int, tavily_depth=None):
    """Provider fallback chain shared by web_search and web_research: Tavily, Serper, Brave,
    DuckDuckGo Lite, Wikipedia. Returns (source, answer, results)."""
    source = "DuckDuckGo"
    answer = None
    results = []

    # 1. Tavily AI Search (Highest quality AI-native search with clean content)
    if tavily_depth is None:
        ans, res = _tavily(query, limit)
    else:
        ans, res = _tavily(query, limit, search_depth=tavily_depth)
    if res:
        source, answer, results = "Tavily", ans, res

    # 2. Serper (Direct Google Search SERP)
    if not results:
        ans, res = _serper(query, limit)
        if res:
            source, answer, results = "Google", ans, res

    # 3. Brave Search API
    if not results:
        ans, res = _brave(query, limit)
        if res:
            source, answer, results = "Brave", ans, res

    # 4. DuckDuckGo Lite (Free zero-key)
    if not results:
        try:
            results = _ddg_lite(query, limit)
            source = "DuckDuckGo"
        except Exception as e:
            logger.debug(f"DuckDuckGo Lite search failed: {e}")
            results = []

    # 5. Wikipedia (Fallback)
    if not results:
        source, results = "Wikipedia", _wikipedia(query, limit)

    return source, answer, results


def _search_top_page(results) -> str:
    """The top page's text comes along: chat used to spend a whole extra model request (of 20 free a day on the
    Flash models) on fetch_webpage after most searches (2026-10-05)."""
    page = ""
    for r in [r for r in results if (r.get("url") or "").startswith("http")][:2]:
        try:
            page = _wt().fetch_webpage(r["url"], max_chars=TOP_PAGE_CHARS)
            break
        except Exception as e:
            logger.debug(f"top page fetch failed for {r['url']}: {e}")
            continue
    return page


def web_search(query: str, limit: int = 6) -> str:
    limit = max(1, min(int(limit or 6), 10))
    source, answer, results = _search_providers(query, limit)

    if not results:
        return f"No results for '{query}'."

    page = _search_top_page(results)

    lines = [f"Search results for '{query}' (source: {source}):"]
    if answer:
        lines.append(f"Direct answer: {answer.strip()}\n")
    for i, r in enumerate(results, 1):
        snippet = _trim_to_sentence(r["snippet"], 300 if page else 450)
        lines.append(f"{i}. {r['title']}\n   {strip_tracking_params(r['url'])}\n   {snippet}")
    if page:
        lines.append(f"\nText of the top result (first {TOP_PAGE_CHARS:,} characters; fetch_webpage reads more of "
                     f"it or another result):\n{page}")
    return "\n".join(lines)


def _fetch_safe(url: str) -> tuple[str, str]:
    try:
        return url, _wt().fetch_webpage(url, max_chars=4000)
    except Exception as e:
        logger.debug(f"page fetch failed for {url}: {e}")
        return url, ""


def _fetch_pages(target_results) -> dict[str, str]:
    """Fetch the target pages in parallel; returns {url: body} for the ones that came back."""
    pages_text: dict[str, str] = {}
    urls = [r["url"] for r in target_results if r.get("url") and r["url"].startswith("http")]
    if urls:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(urls), 3)) as executor:
            future_to_url = {executor.submit(_fetch_safe, u): u for u in urls}
            for fut in concurrent.futures.as_completed(future_to_url):
                try:
                    u, page_body = fut.result()
                    if page_body:
                        pages_text[u] = page_body
                except concurrent.futures.CancelledError:
                    pass
    return pages_text


def _extract_passages(target_results, pages_text) -> list[tuple[str, str, str]]:
    """Candidate passages from pages, falling back to snippet / raw_content. Returns (text, title, url)."""
    candidate_passages: list[tuple[str, str, str]] = []  # (text, title, url)
    for r in target_results:
        u = r.get("url", "")
        title = r.get("title", "")
        body = pages_text.get(u) or r.get("raw_content") or r.get("snippet", "")
        paragraphs = [p.strip() for p in re.split(r"\n{2,}|\n(?=[A-Z0-9#*-])", body) if p.strip()]
        for p in paragraphs:
            if len(p) >= 70 and not re.match(r"^(Page:|Search results|Home|Menu|Copyright)", p, re.IGNORECASE):
                candidate_passages.append((p, title, u))

    if not candidate_passages:
        for r in target_results:
            if r.get("snippet"):
                candidate_passages.append((r["snippet"], r.get("title", ""), r.get("url", "")))
    return candidate_passages


def _keyword_rank(query: str, candidate_passages) -> list[tuple[str, str, str]]:
    """Fallback ranking when the local embedder is unavailable: count query-term hits."""
    terms = [re.escape(w.lower()) for w in re.findall(r"\w+", query) if len(w) > 2]
    def _score(p_text: str) -> int:
        p_low = p_text.lower()
        return sum(len(re.findall(t, p_low)) for t in terms)
    return sorted(candidate_passages, key=lambda cp: _score(cp[0]), reverse=True)


def _rank_passages(query: str, candidate_passages) -> list[tuple[str, str, str]]:
    """Semantic reranking with local embedder (BGE-small in core/embedder.py); keyword ranking fallback."""
    ranked: list[tuple[str, str, str]] = []
    embedder_scored = False
    try:
        from core.embedder import get_embedder
        emb = get_embedder()
        if emb and emb.ensure_loaded() and candidate_passages:
            texts = [cp[0] for cp in candidate_passages]
            q_vec = emb.embed([query], query=True)
            p_vecs = emb.embed(texts, query=False)
            if q_vec is not None and p_vecs is not None and len(p_vecs) > 0:
                scores = (q_vec @ p_vecs.T)[0]
                scored = list(zip(scores, candidate_passages))
                scored.sort(key=lambda x: x[0], reverse=True)
                ranked = [cp for _, cp in scored]
                embedder_scored = True
    except Exception as e:
        logger.debug(f"embedder ranking failed, using keyword ranking: {e}")

    if not embedder_scored:
        ranked = _keyword_rank(query, candidate_passages)
    return ranked


def _dedupe_passages(ranked) -> list[tuple[str, str, str]]:
    """De-duplicate passages: at most 2 per source, at most 4 total."""
    selected_passages: list[tuple[str, str, str]] = []
    seen_urls: dict[str, int] = {}
    for p_text, p_title, p_url in ranked:
        if seen_urls.get(p_url, 0) >= 2:
            continue
        selected_passages.append((p_text, p_title, p_url))
        seen_urls[p_url] = seen_urls.get(p_url, 0) + 1
        if len(selected_passages) >= 4:
            break
    return selected_passages


def web_research(query: str, limit: int = 3) -> str:
    limit = max(1, min(int(limit or 3), 5))
    source, answer, results = _search_providers(query, limit, tavily_depth="advanced")

    if not results:
        return f"No research results for '{query}'."

    target_results = results[:limit]
    pages_text = _fetch_pages(target_results)
    candidate_passages = _extract_passages(target_results, pages_text)
    ranked = _rank_passages(query, candidate_passages)
    selected_passages = _dedupe_passages(ranked)

    lines = [f"Research dossier for '{query}' (source: {source}):"]
    if answer:
        lines.append(f"Summary / Direct Answer:\n{answer.strip()}\n")

    lines.append("Key Evidence & Cited Findings:")
    char_count = sum(len(l) for l in lines)
    for i, (p_text, p_title, p_url) in enumerate(selected_passages, 1):
        clean_u = strip_tracking_params(p_url)
        # Allow healthy paragraphs up to ~950 chars, trimmed at clean sentence boundary
        p_slice = _trim_to_sentence(p_text, 950)
        item = f"[{i}] {p_title} ({clean_u})\n{p_slice}\n"
        if char_count + len(item) > 4200 and i > 1:
            break
        lines.append(item)
        char_count += len(item)

    return collapse_whitespace("\n".join(lines))
