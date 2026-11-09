"""
The chat turn for the AlwaysOnAgent assistant.

Split out of core/memory.py §13 on 2026-11-03; MemoryManager inherits
these methods via _ChatTurnMixin, so they are still bound methods on the manager.
"""


import concurrent.futures
import datetime
import inspect
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.procedures import Procedures

try:
    from core.database import get_iso_now
    from core.llm_usage import usage_purpose

    from .memory_text import count_tokens
    from .persona import ASSISTANT_PERSONA, NO_TOOLS_NOTE
    from .reply_guards import (
        ensure_media_shown,
        keep_promise,
        tools_ran_note,
        trim_tail_question,
        unbacked_claim_note,
    )
except ImportError:
    from database import get_iso_now  # type: ignore[no-redef]
    from llm_usage import usage_purpose  # type: ignore[no-redef]

    from .memory_text import count_tokens
    from .persona import ASSISTANT_PERSONA, NO_TOOLS_NOTE
    from .reply_guards import (
        ensure_media_shown,
        keep_promise,
        tools_ran_note,
        trim_tail_question,
        unbacked_claim_note,
    )


class _ChatTurnMixin:
    # Host state set by MemoryManager.__init__ (core/memory/manager.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime.
    db: "DatabaseManager"  # quoted: imported only under TYPE_CHECKING
    llm: Any
    log: logging.Logger
    conv: str
    _bg: concurrent.futures.ThreadPoolExecutor
    procedures: "Procedures"  # quoted: imported only under TYPE_CHECKING
    TURN_SECONDS: int  # class attribute on _SetupMixin, which precedes this mixin in the MRO

    if TYPE_CHECKING:
        # Methods on mixins that come AFTER this one in MemoryManager's MRO
        # (_MessagesMixin, _LearningMixin, _CompressionMixin): declared here only
        # for the type checker, so the stubs can never shadow the real methods
        # at runtime.
        def add_message(self, role: str, content: str, conv: str | None = None) -> int:
            raise NotImplementedError

        def learn_from_exchange(self, user_text: str, reply: str, msg_id: int | None = None) -> dict[str, int]:
            raise NotImplementedError

        def maybe_compress(self, conv: str | None = None) -> bool:
            raise NotImplementedError

    # build_context lives on _PromptMixin, which precedes this mixin in
    # MemoryManager's MRO, so this runtime stub never shadows the real method.
    def build_context(self, query: str, system_context: str = "", persona: str = ASSISTANT_PERSONA,
                      breakdown: dict[str, int] | None = None,
                      recipes_out: list[dict[str, Any]] | None = None,
                      guard: bool | None = None, dispute: bool | None = None) -> list[dict[str, str]]:
        raise NotImplementedError

    def _prepare_tool_call(self, tools: Any, user_text: str, cancelled: Callable[[], bool] | None,
                           breakdown: dict[str, int]):
        """Wire the tools object up for this turn; returns (specs, guides, initial_groups)."""
        if hasattr(tools, "user_text"):
            tools.user_text = user_text
        if cancelled is not None and hasattr(tools, "cancelled"):
            tools.cancelled = cancelled          # Stop stops the tools too, not just the reply (audit 2.2)
        specs = tools.tool_specs(llm=self.llm, use_router=True) if hasattr(tools, "tool_specs") else []
        initial_groups = list(getattr(tools, "groups", []))
        breakdown["tools"] = count_tokens(json.dumps(specs)) if specs else 0
        guides = tools.guides() if hasattr(tools, "guides") else ""
        return specs, guides, initial_groups

    def _answer_with_tools(self, messages: list[dict[str, str]], tools: Any, breakdown: dict[str, int],
                           cancelled: Callable[[], bool] | None, user_text: str):
        """Answer using the tools the model may call; returns (reply, trace)."""
        specs, guides, initial_groups = self._prepare_tool_call(tools, user_text, cancelled, breakdown)
        if guides:
            # After the history with the rest of this reply's context: the cached beginning stays the same
            messages[-1] = {**messages[-1], "content": messages[-1]["content"] + "\n\n" + guides}
            breakdown["guides"] = count_tokens(guides)
        if not specs:
            # tool_specs always sends core now; if a reply still has no tools, the model must be told so, or
            # it says "let me check the logs" and invents what they say (2026-10-14, task-196)
            self.log.warning("Chat tools: none for this reply; answering without tools")
            self._log_tool_routing([], [], [], 0, len(user_text or ""))
            messages[0]["content"] += NO_TOOLS_NOTE
            with usage_purpose("chat", detail=breakdown):
                return self.llm.chat(messages, max_tokens=900, temperature=0.4, timeout=40), []
        accepts = inspect.signature(self.llm.chat_with_tools).parameters
        extra: dict[str, Any] = {}
        if cancelled is not None and "cancelled" in accepts:
            extra["cancelled"] = cancelled
        if "deadline" in accepts:
            extra["deadline"] = time.time() + self.TURN_SECONDS
        with usage_purpose("chat", detail=breakdown):
            reply, trace = self.llm.chat_with_tools(messages, specs, tools.call, temperature=0.4, **extra)
        if trace:
            self.log.info("Chat looked up: " + ", ".join(f"{t['name']}({t['arguments']})" for t in trace))
        current_groups = list(getattr(tools, "groups", []))
        if getattr(tools, "groups", None) is not None:
            self.log.info(f"Chat tools: core + {', '.join(current_groups) or 'nothing else'} "
                          f"(~{breakdown['tools']} tokens)")
        self._log_tool_routing(initial_groups, current_groups, trace, breakdown.get("tools", 0), len(user_text or ""),
                               picker=getattr(tools, "picked_by", ""))
        return reply, trace

    def _answer_plain(self, messages: list[dict[str, str]], breakdown: dict[str, int]) -> str:
        """Answer with no tools available."""
        messages[0]["content"] += NO_TOOLS_NOTE
        with usage_purpose("chat", detail=breakdown):
            return self.llm.chat(messages, max_tokens=900, temperature=0.4, timeout=40)

    def _fallback_reply(self, reply: str | None, trace: list[dict[str, Any]],
                        cancelled: Callable[[], bool] | None) -> str | None:
        """When the model gave no reply: say what the tools did, or None if nothing was done."""
        if reply:
            return reply
        done = [t for t in trace or [] if isinstance(t, dict)
                and not str(t.get("result") or "").lstrip().startswith("Error")]
        if not done or (cancelled is not None and cancelled()):
            return None
        # The models stopped answering after tools had acted: say what was done rather than nothing, or a
        # fallback model that doesn't know (2026-10-28 audit 2.1)
        return tools_ran_note(done)

    def _finish_reply(self, reply: str, trace: list[dict[str, Any]], user_text: str, tools: Any,
                      cancelled: Callable[[], bool] | None, user_id: int,
                      recipes: list[dict[str, Any]]) -> str | None:
        """Guard the reply, store it, learn from it in the background. None if the user cancelled it."""
        reply = unbacked_claim_note(
            reply, trace,
            task_exists=lambda tid: self.db.get_task(tid) is not None,
            has_background_record=self._has_background_record
        )
        if cancelled is not None and cancelled():
            # The user stopped it in the app: not kept, and nothing is learned from it
            self.log.info("Reply cancelled by the user: not stored")
            return None
        if tools is not None and hasattr(tools, "look_into"):
            reply = keep_promise(reply, trace, lambda promised: tools.look_into(
                f"The user wrote: {user_text}\nThe assistant answered (and promised to follow up): {promised}"))
        reply = ensure_media_shown(reply, trace)
        reply = trim_tail_question(reply, self._previous_reply(), asked_card=bool(getattr(tools, "question", None)))
        self.add_message("assistant", reply)
        self._bg.submit(self._after_turn_safe, user_id, user_text, reply, recipes, trace or [])
        return reply

    def respond(self, user_text: str, system_context: str = "", tools: Any = None,
                cancelled: Callable[[], bool] | None = None) -> str | None:
        """
        Stores the user's message, answers with full memory context, stores the reply, then learns
        from the exchange in the background. tools: an AssistantTools (read-only lookups the model
        may call before answering). Returns None if the LLM is unreachable.
        """
        user_id = self.add_message("user", user_text)
        breakdown: dict[str, int] = {}
        recipes: list[dict[str, Any]] = []
        messages = self.build_context(user_text, system_context=system_context, breakdown=breakdown,
                                      recipes_out=recipes)
        if not self.llm:
            return None
        trace: list[dict[str, Any]] = []
        if tools is not None and hasattr(self.llm, "chat_with_tools"):
            reply, trace = self._answer_with_tools(messages, tools, breakdown, cancelled, user_text)
        else:
            reply = self._answer_plain(messages, breakdown)
        reply = self._fallback_reply(reply, trace, cancelled)
        if reply is None:
            return None
        return self._finish_reply(reply, trace, user_text, tools, cancelled, user_id, recipes)

    def _previous_reply(self) -> str:
        """The latest assistant message in this conversation (a chat reply or one of its own messages)."""
        try:
            r = self.db.get_connection().execute(
                "SELECT content FROM mem_messages WHERE conv = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1",
                (self.conv,)).fetchone()
            return (r[0] if r else "") or ""
        except (sqlite3.Error, AttributeError):  # DB error, or no DB attached
            return ""

    def _log_tool_routing(self, initial_groups: Sequence[str], current_groups: Sequence[str],
                          trace: Any, tool_tokens: int, message_length: int, picker: str = "") -> None:
        try:
            db_dir = Path(self.db.db_path).parent if getattr(self, "db", None) and hasattr(self.db, "db_path") else Path("data")
            log_path = db_dir / "tool_routing.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            added = [g for g in (current_groups or []) if g not in (initial_groups or [])]
            tools_called = [t.get("name") for t in (trace or []) if isinstance(t, dict) and "name" in t]
            entry = {
                "time": get_iso_now(),
                "groups": list(initial_groups or []),
                "added_groups": added,
                "tools_called": tools_called,
                "tool_tokens": int(tool_tokens or 0),
                "message_length": int(message_length or 0),
                "picker": picker or "",               # "router" or "keywords" (the router failed or is off)
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            if log_path.stat().st_size > 5 * 1024 * 1024:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                keep = lines[len(lines) // 2:]
                with open(log_path, "w", encoding="utf-8") as f:
                    f.writelines(keep)
        except Exception as e:
            self.log.debug(f"Failed to record tool routing: {e}")

    def _after_turn_safe(self, user_id: int, user_text: str, reply: str,
                         recipes: list[dict[str, Any]] | None = None,
                         trace: list[dict[str, Any]] | None = None) -> None:
        try:
            self.learn_from_exchange(user_text, reply, msg_id=user_id)
        except Exception as e:
            self.log.warning(f"Memory extraction failed: {e}")
        try:
            if recipes:
                self.procedures.record_outcome(recipes, self.procedures.chat_outcomes(recipes, trace or []))
            if trace and self.procedures.recoveries(trace):
                self.procedures.learn_from_trace(user_text, trace, msg_id=user_id)
        except Exception as e:
            self.log.warning(f"Procedure upkeep failed: {e}")
        try:
            self.maybe_compress()
        except Exception as e:
            self.log.warning(f"Memory compression failed: {e}")

    def _has_background_record(self, reply: str) -> bool:
        """True if the away shift's last session found something, or the candidate pool holds background findings
        from the last 2 days. Until 2026-10-27 this counted rows in "candidate_pool", a table that doesn't exist (it's
        proactive_candidates), behind a bare except; and any summary but "0 item(s)" counted, so the check that
        catches made-up background research could never fire."""
        try:
            rec = json.loads(self.db.get_state("away_shift_last_record", "") or "{}")
            if isinstance(rec, dict) and int(rec.get("found") or 0) > 0:
                return True
        except (ValueError, TypeError):
            pass
        since = (datetime.datetime.now().astimezone() - datetime.timedelta(days=2)).isoformat()
        try:
            row = self.db.get_connection().execute(
                "SELECT COUNT(*) FROM proactive_candidates WHERE source IN ('research', 'interest', 'curiosity', "
                "'pattern') AND created_at >= ?", (since,)).fetchone()
        except sqlite3.OperationalError as e:
            # "no such table" before the pool's first use is fine; any other SQL error is a bug to see, not hide
            if "no such table" not in str(e):
                self.log.warning(f"background record check failed: {e}")
            return False
        return bool(row and row[0] > 0)

    def wait_idle(self, timeout: float = 60.0) -> None:
        """Blocks until queued background memory work has finished (used by tests and shutdown)."""
        self._bg.submit(lambda: None).result(timeout=timeout)
