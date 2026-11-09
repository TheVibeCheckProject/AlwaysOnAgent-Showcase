"""Text helpers (web_tools §3). Split out of core/web_tools.py 2026-11-03."""
import html
import re


def _clean(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _trim_to_sentence(text: str, max_chars: int) -> str:
    """Trims text up to max_chars without cutting mid-sentence, falling back to word boundary."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Find last sentence-ending punctuation followed by whitespace or end
    matches = list(re.finditer(r"[.!?](\s+|$)", cut))
    if matches:
        last_end = matches[-1].end()
        if last_end >= int(max_chars * 0.55):
            return text[:last_end].strip()
    # Fallback to word boundary
    last_space = cut.rfind(" ")
    if last_space > int(max_chars * 0.55):
        return text[:last_space].strip() + "…"
    return cut.strip() + "…"
