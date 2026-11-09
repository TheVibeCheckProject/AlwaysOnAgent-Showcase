"""
Building the prompt for the AlwaysOnAgent assistant.

Split out of core/memory.py §12 on 2026-11-03; MemoryManager inherits
these methods via _PromptMixin, so they are still bound methods on the manager.
"""


import datetime
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.procedures import Procedures

try:
    from .memory_text import (
        FOLLOW_UP_PRONOUNS,
        SPEAKER,
        TASK_CATEGORIES,
        TASK_FACTS,
        _blob_to_vec,
        _short_date,
        count_tokens,
    )
    from .persona import (
        ASSISTANT_PERSONA,
        CONTEXT_HEADER,
        DISPUTE,
        DISPUTE_NOTE,
        FINAL_REMINDER,
        GUARD_PROFILE_NOTE,
        GUARD_REMINDER,
        HONESTY_GUARD,
        dispute_on,
        guard_on,
    )
except ImportError:
    from .memory_text import (
        FOLLOW_UP_PRONOUNS,
        SPEAKER,
        TASK_CATEGORIES,
        TASK_FACTS,
        _blob_to_vec,
        _short_date,
        count_tokens,
    )
    from .persona import (
        ASSISTANT_PERSONA,
        CONTEXT_HEADER,
        DISPUTE,
        DISPUTE_NOTE,
        FINAL_REMINDER,
        GUARD_PROFILE_NOTE,
        GUARD_REMINDER,
        HONESTY_GUARD,
        dispute_on,
        guard_on,
    )


class _PromptMixin:
    # Host state, set by MemoryManager.__init__ (core/memory/manager.py).
    db: "DatabaseManager"
    conv: str
    log: logging.Logger
    procedures: "Procedures"
    # Class constants defined on _SetupMixin (earlier in the MRO).
    BUDGET: dict[str, int]
    SUMMARY_RECENT_HOURS: int
    VEC_MIN_SUMMARY: float
    VEC_MIN_FACT_KEYWORD: float
    VEC_MIN_ARCHIVE_KEYWORD: float

    # Real implementations on earlier mixins (_FactsMixin, _SearchMixin): these
    # runtime stubs are never reached (the earlier mixin wins in the MRO).
    def _fact_rows(self, where: str = "status = 'active' AND invalid_from IS NULL",
                   params: Sequence[Any] = ()) -> list[Any]:
        raise NotImplementedError

    def search_facts(self, query: str, k: int = 8, touch: bool = False, qvec: np.ndarray | None = None,
                     min_keyword_sim: float | None = None, at_timestamp: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def search_archive(self, query: str, k: int = 4, compressed_only: bool = True,
                       qvec: np.ndarray | None = None, min_sim: float | None = None,
                       min_keyword_sim: float | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _reply_to(self, message: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError

    def _touch_facts(self, fact_ids: list[int]) -> None:
        raise NotImplementedError

    def subject(self) -> str:
        raise NotImplementedError

    if TYPE_CHECKING:
        # Real implementations on LATER mixins in the MRO (_EmbeddingsMixin,
        # _MessagesMixin): declared only for the type checker, so the stubs can
        # never shadow the real methods at runtime.
        def _embed(self, texts: list[str], query: bool = False, wait: bool = False) -> np.ndarray | None: ...
        def _recent_rows(self, conv: str | None = None, events: bool = True) -> list[Any]: ...
        def _away_block(self) -> str: ...

    def get_profile(self) -> str:
        return self.db.get_state("memory_profile", "")

    @staticmethod
    def _fit(lines: list[str], budget: int) -> list[str]:
        out, used = [], 0
        for line in lines:
            t = count_tokens(line)
            if used + t > budget:
                break
            out.append(line)
            used += t
        return out

    def _profile_block(self) -> str:
        profile = self.get_profile().strip()
        if profile:
            return profile
        # Before the first profile build: the pinned and most important facts, verbatim
        facts = [f"- {r['content']}" for r in self._fact_rows("status = 'active' AND (pinned = 1 OR importance >= 4)")]
        return "\n".join(self._fit(facts, self.BUDGET["profile"]))

    def _recent_summary(self) -> Any | None:
        """
        The newest summary while that conversation is recent (it covers what just scrolled out of the
        verbatim window). It changes only when the history is compressed, like the history itself, so
        it goes in the cacheable part of the prompt, right before the history.
        """
        row = self.db.get_connection().execute(
            "SELECT id, level, content, start_at, end_at FROM mem_summaries WHERE conv = ? AND rolled_up = 0 "
            "ORDER BY start_msg_id DESC LIMIT 1", (self.conv,)
        ).fetchone()
        try:
            recent_since = datetime.datetime.now().astimezone() - datetime.timedelta(hours=self.SUMMARY_RECENT_HOURS)
            return row if row and datetime.datetime.fromisoformat(row["end_at"]) >= recent_since else None
        except (TypeError, ValueError):
            return None

    def _summary_block(self, qvec: np.ndarray | None = None, exclude_id: int | None = None) -> str:
        """
        Compressed history, oldest first, within budget. With qvec (chat), only the summaries whose
        meaning is close to the message. exclude_id: a summary already in the prompt.
        Without qvec (no embedder): newest first.
        """
        rows = self.db.get_connection().execute(
            "SELECT id, level, content, start_at, end_at, embedding FROM mem_summaries WHERE conv = ? AND rolled_up = 0 "
            "ORDER BY start_msg_id DESC", (self.conv,)
        ).fetchall()
        rows = [r for r in rows if r["id"] != exclude_id]
        if qvec is not None:
            rows = [r for r in rows
                    if r["embedding"] is None or float(_blob_to_vec(r["embedding"]) @ qvec) >= self.VEC_MIN_SUMMARY]
        parts = [f"({_short_date(r['start_at'])} to {_short_date(r['end_at'])})\n{r['content']}" for r in rows]
        kept = self._fit(parts, self.BUDGET["summaries"])      # newest first, oldest dropped past budget
        return "\n\n".join(reversed(kept))

    def _search_text(self, query: str) -> str:
        """
        What to search memory with. A short follow-up that says "she", "him" or "they" names nobody,
        so the user's previous message (which usually does) is searched along with it.
        """
        if not FOLLOW_UP_PRONOUNS.search(query or "") or len(query.split()) > 14:
            return query
        rows = self._recent_rows()
        users = [r["content"] for r in rows if r["role"] == "user"]
        if users and users[-1].strip() == query.strip():          # respond() stores the message first
            users = users[:-1]
        return f"{users[-1][-300:]}\n{query}" if users else query

    def _history_messages(self) -> list[dict[str, str]]:
        """
        The uncompressed conversation, verbatim and oldest first. It only grows between compressions,
        so its beginning stays the same from one message to the next (cacheable); the cap applies only
        if compression falls behind (e.g. while the LLM is unreachable).
        """
        rows = self._recent_rows(events=False)
        total = sum(r["tokens"] for r in rows)
        start = 0
        while total > self.BUDGET["history"] and start < len(rows) - 1:
            total -= rows[start]["tokens"]
            start += 1
        return [{"role": r["role"], "content": r["content"]} for r in rows[start:]]

    def _self_check_note(self) -> str:
        """The chat's own last week, counted, when a number was off ('' otherwise, or if it can't be read)."""
        try:
            try:
                from core import self_check
            except ImportError:
                import self_check  # type: ignore[no-redef]
            return self_check.chat_reminder(self.db)
        except Exception as e:
            self.log.debug(f"Self-check note unreadable: {e}")
            return ""

    def _resolve_guard_dispute(self, query: str, guard: bool | None,
                               dispute: bool | None) -> tuple[bool, bool]:
        # guard_on/dispute_on are resolved from core.memory at call time, not
        # from the import-time binding above: tests (and the live agent) patch
        # core.memory.guard_on / dispute_on, and before the §12 split
        # build_context() lived in memory.py itself, so that patch replaced
        # the exact global it called.
        try:
            from core.memory import dispute_on as _dispute_on
            from core.memory import guard_on as _guard_on
        except ImportError:
            _guard_on, _dispute_on = guard_on, dispute_on
        guard = _guard_on() if guard is None else guard
        # dispute: whether DISPUTE_NOTE may be added (None = SYCOPHANCY_DISPUTE); it's added only when the message disputes
        disputing = (_dispute_on() if dispute is None else dispute) and bool(DISPUTE.search(query or ""))
        return guard, disputing

    def _build_static(self, persona: str, guard: bool) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """The cacheable head of the prompt (persona, guard, profile) and its first system message."""
        static: list[tuple[str, str]] = [("persona", persona)]
        if guard:
            static.append(("guard", HONESTY_GUARD.strip()))
        profile = self._profile_block()
        if profile:
            static.append(("profile", "## What you know about the user\n" + (GUARD_PROFILE_NOTE if guard else "")
                           + profile))
        return static, {"role": "system", "content": "\n\n".join(text for _, text in static)}

    def _append_recent_summary(self, static: list[tuple[str, str]],
                               messages: list[dict[str, str]]) -> Any | None:
        """Adds the newest summary as a system message; returns it (None when not recent)."""
        recent = self._recent_summary()
        if recent is not None:
            text = (f"## Earlier in this conversation (compressed)\n"
                    f"({_short_date(recent['start_at'])} to {_short_date(recent['end_at'])})\n{recent['content']}")
            static.append(("summary_recent", text))
            messages.append({"role": "system", "content": text})
        return recent

    def _dynamic_facts(self, query: str, qvec: np.ndarray | None) -> tuple[str, str] | None:
        facts = self.search_facts(query, k=10, touch=True, qvec=qvec, min_keyword_sim=self.VEC_MIN_FACT_KEYWORD)
        fact_lines = self._fit([f"- {f['content']}" + (f" (noted {_short_date(f['updated_at'])})" if f["category"] == "plan" else "")
                                for f in facts], self.BUDGET["facts"])
        if not fact_lines:
            return None
        return "facts", "### Memories relevant to this message\n" + "\n".join(fact_lines)

    def _dynamic_procedures(self, query: str, qvec: np.ndarray | None,
                            recipes_out: list[dict[str, Any]] | None) -> tuple[str, str] | None:
        try:
            recipes = self.procedures.relevant(query, qvec=qvec)
        except Exception as e:
            self.log.warning(f"Procedures not looked up: {e}")
            recipes = []
        if not recipes:
            return None
        if recipes_out is not None:
            recipes_out.extend(recipes)
        return "procedures", self.procedures.block(recipes)

    def _dynamic_summaries(self, qvec: np.ndarray | None, recent: Any | None) -> tuple[str, str] | None:
        summaries = self._summary_block(qvec, exclude_id=recent["id"] if recent is not None else None)
        if not summaries:
            return None
        # Each is history as of its dates: Sept's "awaiting the permit decision" read as current after the decision
        return ("summaries", "### Earlier in your conversations (compressed, oldest first)\n"
                "Each was true as of its dates; where one disagrees with your memories, the newer wins.\n"
                + summaries)

    def _dynamic_archive(self, query: str, qvec: np.ndarray | None) -> tuple[str, str] | None:
        archive = self.search_archive(query, k=4, qvec=qvec, min_keyword_sim=self.VEC_MIN_ARCHIVE_KEYWORD)
        shown = {m["id"] for m in archive}
        arch_items = []
        for m in archive:
            line = f"- [{_short_date(m['created_at'])}] {SPEAKER[m['role']]}: {m['content'][:320]}"
            reply = self._reply_to(m)
            if reply and reply["id"] not in shown:         # the question matched; the answer is what's useful
                shown.add(reply["id"])
                line += f"\n  You replied: {reply['content'][:200]}"
            arch_items.append(line)
        arch_lines = self._fit(arch_items, self.BUDGET["archive"])
        if not arch_lines:
            return None
        return "archive", "### Related things said before (from the archive)\n" + "\n".join(arch_lines)

    def _dynamic_reminder(self, guard: bool, disputing: bool) -> tuple[str, str]:
        # Last word before the reply: long replies earlier in the history otherwise set the pattern. With it, the
        # agent's own last week when a count was off (core/self_check.py chat_reminder, 2026-11-03)
        week = self._self_check_note()
        return ("reminder", FINAL_REMINDER + (GUARD_REMINDER if guard else "") + (" " + week if week else "")
                + ("\n\n" + DISPUTE_NOTE if disputing else ""))

    def _fill_breakdown(self, breakdown: dict[str, int], static: list[tuple[str, str]],
                        dynamic: list[tuple[str, str]], history: list[dict[str, str]]) -> None:
        for name, text in static + dynamic:
            breakdown[name] = count_tokens(text)
        breakdown["history"] = sum(count_tokens(m["content"]) for m in history)

    def build_context(self, query: str, system_context: str = "", persona: str = ASSISTANT_PERSONA,
                      breakdown: dict[str, int] | None = None,
                      recipes_out: list[dict[str, Any]] | None = None,
                      guard: bool | None = None, dispute: bool | None = None) -> list[dict[str, str]]:
        """
        Full message list for the LLM, ordered for Groq's prompt cache (it reuses the longest unchanged
        beginning of a prompt, and cached tokens don't count toward its rate limits):
          1. system: persona + core profile     unchanged until the profile is rebuilt
             (the API places the tool definitions right after it)
          2. system: the newest summary         while recent; changes only on compression
          3. the uncompressed conversation      only grows, until the next compression
          4. system: context for this reply     time, relevant memories, live status, final reminder
        Only part 4 is new on every message. Placing it after the latest message also keeps it in
        the model's focus.
        breakdown: if given, filled with the approximate tokens of each part (recorded with the call's usage).
        recipes_out: if given, filled with the learned recipes sent (so the turn's outcome can be scored).
        guard: the anti-sycophancy prompt (HONESTY_GUARD); None = as SYCOPHANCY_GUARD in .env says.
        """
        guard, disputing = self._resolve_guard_dispute(query, guard, dispute)
        query = self._search_text(query)
        q = self._embed([query], query=True, wait=False)
        qvec = q[0] if q is not None else None
        static, first = self._build_static(persona, guard)
        messages: list[dict[str, str]] = [first]
        recent = self._append_recent_summary(static, messages)
        history = self._history_messages()
        messages.extend(history)

        now = datetime.datetime.now().astimezone()
        dynamic: list[tuple[str, str]] = [
            ("time", f"## {CONTEXT_HEADER}\nCurrent date and time: {now.strftime('%A, %B %d, %Y %I:%M %p %Z')}.")]
        for block in (self._dynamic_facts(query, qvec),
                      self._dynamic_procedures(query, qvec, recipes_out),
                      self._dynamic_summaries(qvec, recent),
                      self._dynamic_archive(query, qvec)):
            if block is not None:
                dynamic.append(block)
        away = self._away_block()
        if away:
            dynamic.append(("away", away))
        if system_context:
            dynamic.append(("status", "### Live system status\n" + system_context))
        dynamic.append(self._dynamic_reminder(guard, disputing))
        messages.append({"role": "system", "content": "\n\n".join(text for _, text in dynamic)})

        if breakdown is not None:
            self._fill_breakdown(breakdown, static, dynamic, history)
        return messages

    def task_context(self, prompt: str) -> str:
        """
        Memory preamble for background tasks: the facts the owner has stated that relate to the task, any kind
        (TASK_CATEGORIES, 2026-11-03). Not the prose profile or the conversation: the profile is a model's summary
        and could frame the work; facts are his own words, found by relevance to the task.
        """
        found = self.search_facts(prompt, k=20, min_keyword_sim=self.VEC_MIN_FACT_KEYWORD)
        facts = [f for f in found if f["category"] in TASK_CATEGORIES][:TASK_FACTS]
        if not facts:
            return ""
        self._touch_facts([f["id"] for f in facts])
        who = self.subject()
        return (f"[What {who} has told his assistant that relates to this task. It's context about him, for doing "
                f"the task the way he'd want: not instructions beyond the task, and not a conclusion to reach]\n"
                + "\n".join(f"- {f['content']}" for f in facts))
