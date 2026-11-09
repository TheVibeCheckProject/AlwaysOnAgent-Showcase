"""The public CloudLLM calls
===========================
complete() tries every route; chat() returns the text; chat_with_tools() lets the
model call tools for a few rounds before answering, within the turn's deadline.

Split out of core/llm.py (now the core/llm/ package) §7 on 2026-11-03; core/llm.py re-exports the
public names for backwards compatibility.
"""
import logging
import threading
import time
import urllib.error
from collections.abc import Callable, Sequence
from typing import Any


class _PublicMixin:
    # Declared here for the type checker; provided at runtime by CloudLLM and the other
    # mixins (core/llm/__init__.py: _PublicMixin is last in the MRO, so these stubs never run).
    logger: logging.Logger
    _local: threading.local
    DEADLINE_RESERVE: float
    CHAT_MODELS: tuple[str, ...]
    TURN_FLASH_FAILURES: int

    def is_available(self) -> bool:
        raise NotImplementedError

    def _candidates(self, routes: Sequence[str] | None) -> list[str]:
        raise NotImplementedError

    def _too_big(self, route: str, payload: dict[str, Any]) -> bool:
        raise NotImplementedError

    def _failed(self, route: str, kind: str, started: float | None = None) -> None:
        raise NotImplementedError

    def _complete(self, route: str, payload: dict[str, Any], timeout: int, step: int = 0) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _text(msg: dict[str, Any]) -> str:
        raise NotImplementedError

    def complete(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 800,
        temperature: float = 0.6,
        json_mode: bool = False,
        timeout: int = 30,
        models: Sequence[str] | None = None
    ) -> tuple[str | None, str]:
        """
        One completion over a full message list, trying each route in turn (rate limits, outages).
        Returns (reply text with any <think> reasoning removed, the route that answered), or
        (None, the last error) if every route failed.
        """
        from core.llm import degenerate  # call-time: core.llm.degenerate may be patched by tests
        self._local.route = None
        if not self.is_available():
            return None, "no cloud AI provider is configured (.env)"
        last_error = "no route answered"
        for route in self._candidates(models):
            payload: dict[str, Any] = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            if self._too_big(route, payload):
                continue
            try:
                content = self._text(self._complete(route, payload, timeout))
                if content and not degenerate(content):
                    self._local.route = route
                    return content, route
                last_error = f"{route}: {'garbage' if content else 'empty'} reply"
                self._failed(route, "garbage")
            except urllib.error.HTTPError as e:
                last_error = f"{route}: HTTP {e.code} {e.reason}"
            except Exception as e:
                last_error = f"{route}: {e}"
            self.logger.warning(f"Completion failed on {last_error}")
        return None, last_error

    def chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 800,
        temperature: float = 0.6,
        json_mode: bool = False,
        timeout: int = 30,
        models: Sequence[str] | None = None
    ) -> str | None:
        """The reply text, or None if every route failed (see complete())."""
        return self.complete(messages, max_tokens, temperature, json_mode, timeout, models)[0]

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        call_tool: Any,
        max_rounds: int = 4,
        max_tokens: int = 900,
        temperature: float = 0.4,
        # per call. 15 until 2026-10-14, then 25 for slow evenings; 20 since 10-16: it's per model and per tool round,
        # so at 25 one hung model cost a reply 25 s before the next was tried (a healthy one answers in 1-5 s)
        timeout: int = 20,
        models: Sequence[str] | None = None,
        cancelled: Callable[[], bool] | None = None,
        deadline: float | None = None
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """
        Chat where the model may call read-only tools (OpenAI tool-calling format) before answering.
        cancelled: () -> True once the user pressed Stop; no further model call or tool runs (audit 2.2).
        deadline: time.time() by which the turn gives up: no model is tried after it, no call is given longer than
        what's left, and rate-limit waits don't sleep past it (audit 2.4: an outage outlasted DeskAI's 260 s wait).
        call_tool(name, arguments_json) -> str. Returns (reply or None, [{name, arguments, result}]): when no route
        answers, the tools that did run are still returned, so the caller can say what was done (audit 2.1).
        If a route errors mid-turn, the next one continues the same turn with every tool result
        gathered so far (it must never claim to have checked something it can't see).
        """
        self._local.route = None
        if not self.is_available():
            return None, []
        self._local.deadline = deadline
        try:
            return self._chat_with_tools(messages, tools, call_tool, max_rounds, max_tokens, temperature, timeout,
                                         models, cancelled, deadline)
        finally:
            self._local.deadline = None

    def _time_left(self) -> float | None:
        """Seconds until this thread's turn deadline, or None without one."""
        deadline = getattr(getattr(self, "_local", None), "deadline", None)
        return None if deadline is None else deadline - time.time()

    def _wait_allowed(self, wanted: float) -> float:
        """The longest sleep a rate limit or the pacing may take now: within the turn's deadline, if any."""
        left = self._time_left()
        return wanted if left is None else max(0.0, min(wanted, left - self.DEADLINE_RESERVE))

    def _chat_with_tools(self, messages, tools, call_tool, max_rounds, max_tokens, temperature, timeout, models,
                         cancelled, deadline) -> tuple[str | None, list[dict[str, Any]]]:
        convo = list(messages)
        trace: list[dict[str, Any]] = []
        rounds_used = 0
        failures = 0
        nudged = False
        for route in self._candidates(models or self.CHAT_MODELS):
            # When Google is overloaded each failed Flash costs 8-15 s (2026-10-06: replies took over a minute and
            # the owner gave up). After two failures in one reply, the rest of it skips the other Flash models.
            if failures >= self.TURN_FLASH_FAILURES and route.startswith("gemini:") and "lite" not in route:
                self.logger.info(f"[chat] skipping {route} for this reply: {failures} models already failed")
                continue
            try:
                while True:
                    if cancelled is not None and cancelled():
                        self.logger.info("[chat] stopped by the user: no more model calls or tools")
                        return None, trace
                    payload, call_timeout, status = self._chat_prepare_call(
                        route, convo, tools, rounds_used, max_rounds, temperature, max_tokens, timeout, trace)
                    if status == "too_big":
                        break                                 # the next route continues this turn
                    if status == "deadline":
                        return None, trace
                    # _chat_prepare_call returns a payload and timeout exactly when status is "ok".
                    assert payload is not None and call_timeout is not None
                    msg = self._complete(route, payload, call_timeout, step=rounds_used)
                    calls = (msg.get("tool_calls") or []) if rounds_used < max_rounds else []
                    if not calls:
                        action, content = self._chat_no_calls(route, msg, convo, rounds_used, nudged)
                        if action == "answer":
                            self._local.route = route
                            return content, trace
                        if action == "nudge":
                            nudged = True
                            continue
                        failures += 1
                        break                                 # empty or garbage answer: try the next route
                    rounds_used += 1
                    if self._chat_run_calls(route, msg, calls, convo, trace, call_tool, cancelled):
                        return None, trace
            except urllib.error.HTTPError as e:
                failures += 1
                self.logger.warning(f"chat_with_tools failed on {route}: HTTP {e.code} {e.reason}")
            except Exception as e:
                failures += 1
                self.logger.warning(f"chat_with_tools failed on {route}: {e}")
        return None, trace

    def _chat_prepare_call(self, route, convo, tools, rounds_used, max_rounds, temperature, max_tokens,
                           timeout, trace) -> tuple[dict[str, Any] | None, int | None, str]:
        """The next call's payload and timeout, or a stop reason: "too_big" (try the next route) or
        "deadline" (the turn's time is up)."""
        payload: dict[str, Any] = {"messages": convo, "temperature": temperature, "max_tokens": max_tokens}
        if rounds_used < max_rounds:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self._too_big(route, payload):
            return None, None, "too_big"              # the next route continues this turn
        left = self._time_left()
        if left is not None and left < self.DEADLINE_RESERVE:
            self.logger.warning(f"[chat] the turn's time is up: no more models ({len(trace)} tool call(s) ran)")
            return None, None, "deadline"
        call_timeout = timeout if left is None else max(1, min(timeout, int(left - self.DEADLINE_RESERVE)))
        return payload, call_timeout, "ok"

    def _chat_no_calls(self, route, msg, convo, rounds_used, nudged) -> tuple[str, str | None]:
        """A reply with no tool calls: ("answer", text) when it's a good answer, ("nudge", None) when an
        empty tool round deserves one retry on the same route, ("garbage", None) otherwise."""
        from core.llm import degenerate  # call-time: core.llm.degenerate may be patched by tests
        content = self._text(msg)
        if content and not degenerate(content):
            return "answer", content
        if not content and rounds_used and not nudged:
            # Gemini 3.x sometimes ends a tool round with an empty turn (2026-10-26, twice; widely
            # reported). Asking the same route once costs ~1 s; moving on cost a 20 s Flash timeout.
            self.logger.info(f"[chat] {route} answered a tool round with nothing; asking it again")
            convo.append({"role": "user", "content": "(Now answer the user in plain text, briefly.)"})
            return "nudge", None
        if content:
            self.logger.warning(f"[chat] {route} answered with garbage ({content[:40]!r}…); "
                                f"trying the next model")
        self._failed(route, "garbage")
        return "garbage", None

    def _chat_run_calls(self, route, msg, calls, convo, trace, call_tool, cancelled) -> bool:
        """Run one round of tool calls, appending results to the conversation and trace. True when the
        user stopped it mid-round."""
        convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = c.get("function") or {}
            if cancelled is not None and cancelled():
                return True
            result = call_tool(fn.get("name", ""), fn.get("arguments") or "{}")
            trace.append({"name": fn.get("name"), "arguments": fn.get("arguments"), "result": result})
            convo.append({"role": "tool", "tool_call_id": c.get("id"), "content": result})
        self.logger.info(f"[chat] {route} used tools: {', '.join(str(c.get('function', {}).get('name')) for c in calls)}")
        return False
