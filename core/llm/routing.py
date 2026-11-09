"""Routing for the CloudLLM calls
==============================
Which routes to try, in order; Mistral's tool-call ids; trimming a request to fit.

Split out of core/llm.py (now the core/llm/ package) §3 on 2026-11-03; core/llm.py re-exports the
public names for backwards compatibility.
"""
import hashlib
import logging
import re
import time
from collections.abc import Sequence
from typing import Any, ClassVar


class _RoutingMixin:
    # Host state (CloudLLM): annotation-only, so nothing here shadows the real attributes.
    PROVIDERS: tuple[str, ...]
    CHAT_MODELS: tuple[str, ...]
    GEMINI_REASONING: ClassVar[dict[str, str]]
    default_model: str
    keys: dict[str, str]
    logger: logging.Logger
    _cooldown_until: dict[str, float]

    def _cpu_busy(self) -> bool:
        # Host method: CloudLLM defines it, so this stub never runs on a real instance.
        raise NotImplementedError

    @classmethod
    def _split(cls, route: str) -> tuple[str, str]:
        provider, sep, model = route.partition(":")
        return (provider, model) if sep and provider in cls.PROVIDERS else ("groq", route)

    def _candidates(self, routes: Sequence[str] | None) -> list[str]:
        """Routes to try, in order: configured providers only, skipping those cooling down (unless all are)."""
        order = list(routes) if routes else [self.default_model, *self.CHAT_MODELS]
        usable: list[str] = []
        for r in order:
            provider, model = self._split(r)
            route = f"{provider}:{model}"
            if self.keys.get(provider) and route not in usable:
                usable.append(route)
        if any(r.startswith("ollama:") for r in usable) and self._cpu_busy():
            # A render or the vision model is working: the local model would slow it down; the cloud covers it
            self.logger.info("CPU busy: skipping the local model for this call")
            usable = [r for r in usable if not r.startswith("ollama:")] or usable
        now = time.time()
        ready = [r for r in usable if self._cooldown_until.get(r, 0.0) <= now]
        return ready or [r for r in usable if not r.startswith("ollama:")] or usable   # all cooling: try the cloud

    @staticmethod
    def _mistral_tool_ids(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Mistral accepts only 9-character alphanumeric tool-call ids; other providers' ids are mapped consistently."""
        def fix(call_id: Any) -> str:
            call_id = str(call_id or "")
            return call_id if re.fullmatch(r"[A-Za-z0-9]{9}", call_id) else hashlib.sha1(call_id.encode()).hexdigest()[:9]
        out = []
        for m in messages:
            if m.get("tool_calls"):
                m = {**m, "tool_calls": [{**c, "id": fix(c.get("id"))} for c in m["tool_calls"]]}
            elif m.get("role") == "tool":
                m = {**m, "tool_call_id": fix(m.get("tool_call_id"))}
            out.append(m)
        return out

    def _prepare(self, provider: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {**payload, "model": model}
        if model.startswith(("openai/gpt-oss", "@cf/openai/gpt-oss")):
            body["reasoning_effort"] = "low"     # hidden reasoning tokens count against the limits
        elif provider == "gemini" and model in self.GEMINI_REASONING:
            body["reasoning_effort"] = self.GEMINI_REASONING[model]
        if provider == "mistral":
            body["messages"] = self._mistral_tool_ids(body["messages"])
        return body
