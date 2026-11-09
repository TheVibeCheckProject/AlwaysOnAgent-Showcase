"""
Compression for the AlwaysOnAgent assistant.

Split out of core/memory.py §15 on 2026-11-03; MemoryManager inherits
these methods via _CompressionMixin, so maybe_compress() / _rollup()
are still bound methods on the manager.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core.database import get_iso_now

    from .memory_text import _short_date, count_tokens
except ImportError:
    from database import get_iso_now  # type: ignore[no-redef]

    from .memory_text import _short_date, count_tokens

class _CompressionMixin:
    # --- Host state / constants supplied by MemoryManager and earlier mixins ---
    # (all earlier in the MRO than _CompressionMixin, so the runtime stubs below
    # never shadow a real method.)
    db: "DatabaseManager"
    conv: str
    log: logging.Logger
    llm: Any
    COMPRESS_TRIGGER: int
    KEEP_RECENT: int
    ROLLUP_KEEP: int
    ROLLUP_MERGE: int
    MAX_LEVEL: int

    def _recent_rows(self, conv: str | None = None, events: bool = True) -> list[Any]:
        raise NotImplementedError

    def _bg_chat(self, messages: list[dict[str, str]], purpose: str,
                 cloud_only: bool = False, **kw: Any) -> str | None:
        raise NotImplementedError

    def subject(self) -> str:
        raise NotImplementedError

    def _embedding_blob(self, text: str) -> bytes | None:
        raise NotImplementedError

    SUMMARIZE_PROMPT = (
        "Compress this stretch of conversation between the user and their assistant into a summary "
        "the assistant will rely on later instead of the original messages. Keep: decisions, facts "
        "about the user, requests and their outcomes, commitments and open questions, names, numbers "
        "and dates (absolute), every correction the user made (what was wrong and what is right), and "
        "anything started but not yet finished or reported back. Drop pleasantries. Past tense, terse bullet points, at most {words} words.\n"
        "Be exact about who said what: the assistant's ideas or suggestions are 'the assistant "
        "suggested…', never the user's decisions unless the user agreed. Record only what is in the "
        "transcript; add no interpretation. Call the user {subject}."
    )
    ROLLUP_PROMPT = (
        "Merge these consecutive conversation summaries (oldest first) into one shorter summary. Keep "
        "what still matters: decisions, facts about the user, outcomes, open threads, the user's corrections, "
        "names, numbers, dates. Drop details that were superseded later. Terse bullet points, at most {words} words. "
        "Call the user {subject}."
    )

    @staticmethod
    def _transcript(rows: Sequence[Any]) -> str:
        who = {"user": "User", "assistant": "Assistant", "event": "Event"}
        return "\n".join(f"[{r['created_at'][:16]}] {who.get(r['role'], r['role'])}: {r['content'][:2000]}" for r in rows)

    def maybe_compress(self, conv: str | None = None) -> bool:
        """Summarizes the oldest part of the recent window once it outgrows COMPRESS_TRIGGER."""
        conv = conv or self.conv
        # The conversation only: background events were 20% of the summaries' lines on the Mini (audit 7.1). They're
        # marked compressed with the stretch they fall in (the UPDATE below covers every row up to its end)
        rows = self._recent_rows(conv, events=False)
        total = sum(r["tokens"] for r in rows)
        if total <= self.COMPRESS_TRIGGER or not self.llm:
            return False
        cut, remaining = 0, total
        while cut < len(rows) - 2 and remaining > self.KEEP_RECENT:
            remaining -= rows[cut]["tokens"]
            cut += 1
        # Don't split a question from its answer
        while cut < len(rows) - 2 and rows[cut]["role"] == "assistant":
            cut += 1
        chunk = rows[:cut]
        if not chunk:
            return False
        summary = self._bg_chat([
            {"role": "system", "content": self.SUMMARIZE_PROMPT.format(words=180, subject=self.subject())},
            {"role": "user", "content": self._transcript(chunk)}
        ], "summarize", max_tokens=500, temperature=0.2, timeout=60)
        if not summary:
            return False
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO mem_summaries (conv, level, content, start_msg_id, end_msg_id, start_at, end_at, created_at, tokens, embedding) "
                "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conv, summary, chunk[0]["id"], chunk[-1]["id"], chunk[0]["created_at"], chunk[-1]["created_at"],
                 get_iso_now(), count_tokens(summary), self._embedding_blob(summary))
            )
            conn.execute("UPDATE mem_messages SET compressed = 1 WHERE conv = ? AND id <= ? AND compressed = 0",
                         (conv, chunk[-1]["id"]))
        self.log.info(f"Compressed {len(chunk)} messages ({total - remaining} tokens) into a summary.")
        self._rollup(conv)
        return True

    def _rollup(self, conv: str) -> None:
        conn = self.db.get_connection()
        for level in range(1, self.MAX_LEVEL):
            rows = conn.execute(
                "SELECT * FROM mem_summaries WHERE conv = ? AND level = ? AND rolled_up = 0 ORDER BY start_msg_id",
                (conv, level)
            ).fetchall()
            if len(rows) <= self.ROLLUP_KEEP:
                break
            group = rows[:self.ROLLUP_MERGE]
            body = "\n\n".join(f"({_short_date(r['start_at'])} to {_short_date(r['end_at'])})\n{r['content']}" for r in group)
            merged = self._bg_chat([
                {"role": "system", "content": self.ROLLUP_PROMPT.format(words=200 + 40 * level, subject=self.subject())},
                {"role": "user", "content": body}
            ], "rollup", max_tokens=600, temperature=0.2, timeout=60)
            if not merged:
                return
            with conn:
                conn.execute(
                    "INSERT INTO mem_summaries (conv, level, content, start_msg_id, end_msg_id, start_at, end_at, created_at, tokens, embedding) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (conv, level + 1, merged, group[0]["start_msg_id"], group[-1]["end_msg_id"],
                     group[0]["start_at"], group[-1]["end_at"], get_iso_now(), count_tokens(merged),
                     self._embedding_blob(merged))
                )
                conn.executemany("UPDATE mem_summaries SET rolled_up = 1 WHERE id = ?", [(r["id"],) for r in group])
            self.log.info(f"Rolled {len(group)} level-{level} summaries into one level-{level + 1} summary.")
