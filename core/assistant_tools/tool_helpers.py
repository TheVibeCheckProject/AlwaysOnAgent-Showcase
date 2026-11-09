"""
Small helpers the tools share.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tool_helpers.py).

Split out of core/assistant_tools.py §4 on 2026-11-03: _cap trims a tool's text
to MAX_RESULT_CHARS and scrubs secrets; close_senders finds the sender you
probably meant when a mail search by name found nothing. Imported by
core/assistant_tools.py and by the mail mixin; assistant_tools.py re-exports
them both.
"""

import difflib
import re
from typing import Any

try:
    from core.config import redact_secrets
    from core.observation_sanitizer import collapse_whitespace

    from .tool_constants import MAX_RESULT_CHARS
except ImportError:
    from config import redact_secrets  # type: ignore[no-redef]
    from observation_sanitizer import collapse_whitespace  # type: ignore[no-redef]

    from core.assistant_tools.tool_constants import MAX_RESULT_CHARS  # type: ignore[no-redef]

def _cap(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    text = redact_secrets(text or "")
    text = collapse_whitespace(text)
    if len(text) > limit:
        cut = text[:limit]
        last_break = max(cut.rfind("\n"), cut.rfind(". "))
        if last_break > int(limit * 0.75):
            text = text[:last_break + 1].strip() + f"\n…[truncated, {len(text) - last_break} more characters]"
        else:
            text = text[:limit] + f"\n…[truncated, {len(text) - limit} more characters]"
    return text


def close_senders(query: str, senders: list[dict[str, Any]], threshold: float = 0.78) -> list[tuple]:
    """Known senders whose name or address is close to a word of the search: [(score, word, sender)], best first
    (ties: the sender with more emails). Gmail matches whole words, so a misspelt or run-together name finds nothing."""
    words = [w for w in re.findall(r"[a-z0-9]+", re.sub(r"\b[a-z_]+:", " ", query.lower())) if len(w) >= 4]
    found = []
    for s in senders:
        name, addr = (s.get("name") or "").lower(), (s.get("sender") or "").lower()
        domain = addr.split("@")[-1].rsplit(".", 1)[0]
        cands = {re.sub(r"[^a-z0-9]", "", name), re.sub(r"[^a-z0-9]", "", domain)}
        cands |= set(re.findall(r"[a-z0-9]{4,}", f"{name} {addr}"))
        best, best_word = 0.0, ""
        for w in words:
            for c in filter(None, cands):
                score = 1.0 if (len(w) >= 5 and w in c) else max(
                    difflib.SequenceMatcher(None, w, c).ratio(), difflib.SequenceMatcher(None, w, c[:len(w)]).ratio())
                if score > best:
                    best, best_word = score, w
        if best >= threshold:
            found.append((best, best_word, s))
    found.sort(key=lambda f: (-f[0], -(f[2].get("emails") or 0)))
    return found


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['_cap', 'close_senders']
