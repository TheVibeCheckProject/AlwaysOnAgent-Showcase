"""
Text helpers for the AlwaysOnAgent assistant.

Split out of core/memory.py §1 on 2026-11-03 so the memory section
mixins (core/memory/*.py) can use them without a circular import back
into core.memory. the core.memory package re-exports all of these names, so
`from core.memory import count_tokens` still works.
"""

import json
import re
from typing import Any

import numpy as np

try:
    from core.config import redact_secrets
except ImportError:
    from config import redact_secrets  # type: ignore[no-redef]

try:
    import tiktoken
    _ENC: Any | None = tiktoken.get_encoding("cl100k_base")
except (ImportError, OSError, ValueError):  # offline first run, missing package
    _ENC = None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    if _ENC is not None:
        try:
            return len(_ENC.encode(text, disallowed_special=()))
        except (ValueError, TypeError):
            pass
    return max(1, len(text) // 4)


def _vec_to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def _blob_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def _short_date(iso: str | None) -> str:
    return (iso or "")[:10]


# §1 setup constants and small helpers, moved here from core/memory.py so the
# memory section mixins can use them without a circular import back into
# core.memory. the core.memory package re-exports all of these names.


MAIN_CONV = "main"
CATEGORIES = ("profile", "preference", "project", "plan", "person", "instruction", "fact")
# What background tasks see (task_context): until 2026-11-03 only project notes, instructions and preferences, kept
# from "outside services". Alex: "I'm okay with background workers getting personal info, I just didn't want them
# to experience bias ... this is a personal assistant for me and no one else, so that matters too." So every kind of
# fact he stated goes, when it relates to the task; facts are only what he said (inferences are never saved), and
# the briefing is labelled context, never a conclusion to reach.
TASK_CATEGORIES = CATEGORIES
TASK_FACTS = 10
SPEAKER = {"user": "User", "assistant": "You", "event": "Event"}
# People pronouns only: "it" / "this" / "that" are too common to mean the question depends on the last turn
FOLLOW_UP_PRONOUNS = re.compile(r"\b(she|he|her|hers|him|his|they|them|their)\b", re.IGNORECASE)
# "Today is Wednesday, September 23 2026." is true for a day, then contradicts the real date in every prompt
_DATE_STATEMENT = re.compile(
    r"^\s*(?:today|tonight|now|it|(?:the\s+)?(?:current\s+)?(?:date|time|day))(?:'s(?:\s+date\s+is)?|\s+is|\s+was)\s+"
    r"(?:now\s+|currently\s+)?(?:the\s+)?"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|"
    r"august|september|october|november|december|\d{4}-\d{2}-\d{2}|\d{1,2}(?::\d{2}|\s*(?:am|pm)))\b",
    re.IGNORECASE)


def _is_date_statement(content: str) -> bool:
    """True for a memory that only states the current date or time."""
    return bool(_DATE_STATEMENT.search(content or "")) and len((content or "").split()) <= 12

# count_tokens / _vec_to_blob / _blob_to_vec / _short_date live in this file too;
# the core.memory package re-exports them so `from core.memory import count_tokens` still works.


_STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could", "did", "do", "does", "for", "from", "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "just", "me", "my", "of", "on", "or", "our", "so", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "was", "we", "were", "what", "when", "where", "which", "who", "why", "will", "with", "would", "you", "your", "yours", "about", "also", "been", "being", "i'm", "it's", "that's", "don't", "not", "can't", "won't"} | {"don", "didn", "doesn", "isn", "wasn", "aren", "won", "can"}


def _fts_query(text: str) -> str:
    """Safe FTS5 MATCH expression: OR of the distinctive words (quoted, so no syntax injection)."""
    # Apostrophes split words the same way the FTS tokenizer does ("dog's" -> "dog", "s")
    words = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) > 2 and w not in _STOPWORDS and w not in words:
            words.append(w)
    return " OR ".join(f'"{w}"' for w in words[:24])


def _parse_json_object(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, TypeError):
                return None
    return None




def _evidence(text: str | None) -> str | None:
    """The owner's words backing a fact, as stored (secrets redacted, kept short)."""
    text = redact_secrets(" ".join(str(text or "").split()))
    return text[:300] or None
