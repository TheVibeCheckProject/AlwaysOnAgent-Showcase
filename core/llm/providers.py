"""Calling a provider
====================
One completion from one route: the OpenAI-style providers, Gemma through Google's
own API (its calls and tool calls translated both ways), and the local Ollama model.

Split out of core/llm.py (now the core/llm/ package) §6 on 2026-11-03; core/llm.py re-exports the
public names for backwards compatibility.
"""
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import TYPE_CHECKING, Any

try:
    from core.config import redact_secrets
except ImportError:
    from config import redact_secrets  # type: ignore[no-redef]

class _ProvidersMixin:
    # Host state and helpers defined on CloudLLM or on mixins earlier in CloudLLM's MRO
    # (_RoutingMixin, _LimitsMixin, _UsageMixin), declared here so mypy can see them. The
    # stubs are never reached at runtime: the real definitions win in the MRO.
    logger: logging.Logger
    urls: dict[str, str]
    keys: dict[str, str]
    OVERLOAD_RETRY_WAIT: float
    MAX_RATE_LIMIT_WAIT: float
    TIMEOUT_COOLDOWN: float
    LOCAL_THREADS: int
    LOCAL_CTX: int
    LOCAL_TIMEOUT: int
    LOCAL_DOWN_COOLDOWN: float

    @classmethod
    def _split(cls, route: str) -> tuple[str, str]:
        raise NotImplementedError

    def _prepare(self, provider: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _pace(self, route: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def _record_usage(self, provider: str, model: str, data: dict[str, Any], resp: Any, started: float,
                      step: int = 0) -> None:
        raise NotImplementedError

    @staticmethod
    def _error_text(err: urllib.error.HTTPError) -> str:
        raise NotImplementedError

    @staticmethod
    def _rate_limit_wait(err: urllib.error.HTTPError) -> float | None:
        raise NotImplementedError

    def _cool_down(self, route: str, seconds: float | None, error_text: str) -> None:
        raise NotImplementedError

    def _failed(self, route: str, kind: str, started: float | None = None) -> None:
        raise NotImplementedError

    @staticmethod
    def _daily_quota(provider: str, error_text: str) -> bool:
        raise NotImplementedError

    def _timed_out(self, route: str, timeout: float) -> None:
        raise NotImplementedError

    def _bench(self, route: str, reason: str, seconds: float | None = None) -> None:
        raise NotImplementedError

    if TYPE_CHECKING:
        # _wait_allowed lives on _PublicMixin, which comes AFTER _ProvidersMixin in
        # CloudLLM's MRO, so a runtime stub here would shadow the real method.
        # Declare it for mypy only.
        def _wait_allowed(self, wanted: float) -> float:
            ...

    def _complete(self, route: str, payload: dict[str, Any], timeout: int, step: int = 0) -> dict[str, Any]:
        """
        POSTs one completion to a route and returns the reply message. On a rate limit with a short
        reset, waits once and retries; a longer one puts the route on cooldown and raises.
        step: how many rounds of tool results the prompt already contains (recorded with the usage).
        """
        provider, model = self._split(route)
        if provider == "ollama":
            return self._complete_local(route, model, payload, timeout, step)
        if provider == "gemini" and model.startswith("gemma"):
            return self._complete_google(route, model, payload, timeout, step)
        self._pace(route, payload)
        body = json.dumps(self._prepare(provider, model, payload)).encode("utf-8")
        for attempt in range(2):
            req = urllib.request.Request(
                self.urls[provider], data=body,
                headers={"Authorization": f"Bearer {self.keys[provider]}", "Content-Type": "application/json",
                         "User-Agent": "AlwaysOnAgent/1.0"},
                method="POST")
            started = time.time()
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self._record_usage(provider, model, data, resp, started, step)
                    return data["choices"][0]["message"]
            except urllib.error.HTTPError as e:
                if self._complete_http_error(route, provider, e, self._error_text(e), attempt, started):
                    continue
                raise
            except (TimeoutError, urllib.error.URLError) as e:
                if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                    self._timed_out(route, timeout)
                    self._failed(route, "timeout", started)
                else:
                    self._failed(route, "error", started)
                raise
        # Unreachable: _complete_http_error only retries on the first attempt, so the
        # second attempt either returns a message or raises.
        raise AssertionError("unreachable: _complete retried twice")

    def _complete_http_error(self, route: str, provider: str, e: urllib.error.HTTPError,
                             text: str, attempt: int, started: float) -> bool:
        """One HTTP error from a completion attempt: True when the caller should retry, False to raise.

        Overload (5xx) retries once except on Gemini; other rejections raise; a rate limit waits once
        when the reset is short, otherwise the route cools down and the caller raises.
        """
        if e.code in (500, 502, 503, 504):
            # No retry on Gemini: its free tier allows 20 requests a day per Flash model and failed calls
            # seem to count (2026-10-05: 3.7 Flash "used" 20 with no answer), so the next Flash goes now
            if attempt == 0 and provider != "gemini":
                self.logger.info(f"{route} is overloaded (HTTP {e.code}); retrying once in "
                                 f"{self.OVERLOAD_RETRY_WAIT:.0f}s")
                time.sleep(self.OVERLOAD_RETRY_WAIT)
                return True
            self._bench(route, f"is overloaded (HTTP {e.code})")
            self._failed(route, "overloaded", started)
            return False
        if e.code != 429 and "neuron" not in text.lower():
            # The provider's own reason, so a rejected request can be diagnosed from the log (2026-10-10:
            # Mistral's 422s of Oct 6 left only "Unprocessable Entity" and couldn't be reproduced)
            self.logger.warning(f"{route} rejected the request (HTTP {e.code}): "
                                f"{redact_secrets(' '.join(text.split()))[:300] or e.reason}")
            self._failed(route, "error", started)
            return False
        wait = self._rate_limit_wait(e)
        if attempt == 0 and wait is not None and wait <= self._wait_allowed(self.MAX_RATE_LIMIT_WAIT):
            self.logger.info(f"Rate limit on {route}: waiting {wait:.1f}s")
            time.sleep(wait + 0.5)
            return True
        self._cool_down(route, wait, text)
        self._failed(route, "quota_day" if self._daily_quota(provider, text) else "rate_limit", started)
        return False

    # Per-minute limits of the free tier (AI Studio, 2026-10-06), kept just under: (requests, tokens or None).
    # Flash-Lite peaked at 18 a minute against 15 when the background chores and chat fired together, and the
    # extra calls were refused. A call waits up to PACE_MAX_WAIT for room, then goes anyway.
    MINUTE_LIMITS = (("gemma", 28, 15000), ("flash-lite", 14, None), ("flash", 5, None))
    PACE_MAX_WAIT = 12.0
    _PACE_LOCK = threading.Lock()

    # Requests a route can never take, skipped before sending (no failed call, nothing in the failure log). Groq's
    # free tier caps a request at its per-minute token limit (8K for the gpt-oss and Qwen models): a chat request
    # with persona, memory and tools is ~10K, and on 2026-10-06 every Groq try answered 413 Payload Too Large.
    REQUEST_TOKEN_CAPS = (("groq:", 8000),)

    def _complete_google(self, route: str, model: str, payload: dict[str, Any], timeout: int,
                         step: int = 0) -> dict[str, Any]:
        """Gemma through Google's own generateContent API (the OpenAI-style route answers it with HTTP 500).
        Gemma thinks out loud before answering: it gets room for that (GEMMA_MIN_TOKENS; with 400 it was cut
        off), and its thinking parts are dropped. Returns an OpenAI-style message.

        Tools (2026-10-06, for chat): the OpenAI-style tool list becomes Google's functionDeclarations, Gemma's
        functionCall parts come back as OpenAI-style tool_calls, and the conversation's tool rounds are translated
        back (_google_contents), so chat_with_tools works the same as with every other route. Google's call parts
        may carry a signature it wants back unchanged; they're kept by tool-call id (_google_call_parts) and resent
        as they were."""
        self._pace(route, payload)
        system, contents = self._google_contents(payload.get("messages") or [])
        body: dict[str, Any] = {"contents": contents, "generationConfig": {
            "maxOutputTokens": max(int(payload.get("max_tokens") or 0), self.GEMMA_MIN_TOKENS),
            "temperature": payload.get("temperature", 0.4)}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if payload.get("tools"):
            body["tools"] = [{"functionDeclarations": [self._google_function(t) for t in payload["tools"]]}]
        # Gemma thinks before it answers or calls a tool: a 15 s chat timeout cut healthy answers short
        timeout = max(timeout, self.GEMMA_MIN_TIMEOUT)
        data, started = self._google_request(route, model, body, timeout)
        return self._google_message(data, model, started, step)

    def _google_request(self, route: str, model: str, body: dict[str, Any],
                        timeout: int) -> tuple[dict[str, Any], float]:
        """One generateContent POST; returns (parsed reply, start time). HTTP and timeout errors are
        recorded against the route before they raise."""
        req = urllib.request.Request(f"{self.GOOGLE_API}/{model}:generateContent",
                                     data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"x-goog-api-key": self.keys["gemini"],
                                              "Content-Type": "application/json", "User-Agent": "AlwaysOnAgent/1.0"})
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8")), started
        except urllib.error.HTTPError as e:
            text = self._error_text(e)
            if e.code in (500, 502, 503, 504):
                self._bench(route, f"is overloaded (HTTP {e.code})")
                self._failed(route, "overloaded", started)
            elif e.code == 429:
                self._cool_down(route, self._rate_limit_wait(e), text)
                self._failed(route, "rate_limit", started)
            else:
                self._failed(route, "error", started)
            raise
        except (TimeoutError, urllib.error.URLError) as e:
            timed_out = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
            if timed_out:
                self._timed_out(route, timeout)
            self._failed(route, "timeout" if timed_out else "error", started)
            raise

    def _google_message(self, data: dict[str, Any], model: str, started: float,
                        step: int = 0) -> dict[str, Any]:
        """A generateContent reply as an OpenAI-style message: thinking parts dropped, usage recorded,
        functionCall parts as tool_calls (kept by id to resend unchanged)."""
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought") and "text" in p).strip()
        usage = data.get("usageMetadata") or {}
        self._record_usage("gemini", model, {"usage": {"prompt_tokens": usage.get("promptTokenCount", 0),
                                                       "completion_tokens": usage.get("candidatesTokenCount", 0)}},
                           None, started, step)
        msg: dict[str, Any] = {"role": "assistant", "content": text}
        calls = []
        for p in parts:
            if "functionCall" not in p:
                continue
            call_id = f"gemma-{uuid.uuid4().hex[:12]}"
            self._google_call_parts[call_id] = p
            fc = p["functionCall"]
            calls.append({"id": call_id, "type": "function",
                          "function": {"name": fc.get("name", ""), "arguments": json.dumps(fc.get("args") or {})}})
        if calls:
            msg["tool_calls"] = calls
        return msg

    GOOGLE_API = "https://generativelanguage.googleapis.com/v1beta/models"
    GEMMA_MIN_TOKENS = 4096
    GEMMA_MIN_TIMEOUT = 30

    @property
    def _google_call_parts(self) -> dict[str, dict[str, Any]]:
        """Gemma's functionCall parts by tool-call id, to resend unchanged in the next round (bounded)."""
        store = self.__dict__.setdefault("_google_parts_store", {})
        while len(store) > 200:
            store.pop(next(iter(store)))
        return store

    @staticmethod
    def _google_function(tool: dict[str, Any]) -> dict[str, Any]:
        """One OpenAI-style tool spec as a Google functionDeclaration (no parameters key when it takes none:
        Google rejects an empty object schema)."""
        f = tool.get("function") or tool
        d: dict[str, Any] = {"name": f["name"], "description": f.get("description", "")}
        params = f.get("parameters") or {}
        if params.get("properties"):
            d["parameters"] = params
        return d

    def _google_contents(self, msgs: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """An OpenAI-style conversation as Google's (systemInstruction text, contents). Assistant tool calls become
        model functionCall parts, tool results user functionResponse parts; neighbours with the same role are
        merged (Google expects user and model turns to alternate)."""
        system = "\n\n".join(str(m.get("content") or "") for m in msgs if m.get("role") == "system")
        names: dict[str, str] = {}
        contents: list[dict[str, Any]] = []

        def add(role: str, part: dict[str, Any]) -> None:
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": role, "parts": [part]})

        for m in msgs:
            role = m.get("role")
            if role == "user":
                add("user", {"text": str(m.get("content") or "")})
            elif role == "assistant":
                if m.get("content"):
                    add("model", {"text": str(m["content"])})
                for c in m.get("tool_calls") or []:
                    fn = c.get("function") or {}
                    names[c.get("id", "")] = fn.get("name", "")
                    kept = self._google_call_parts.get(c.get("id", ""))
                    if kept is None:            # a call another route made earlier in this reply
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                        except ValueError:
                            args = {}
                        kept = {"functionCall": {"name": fn.get("name", ""), "args": args}}
                    add("model", kept)
            elif role == "tool":
                add("user", {"functionResponse": {"name": names.get(m.get("tool_call_id", ""), "tool"),
                                                  "response": {"content": str(m.get("content") or "")}}})
        return system, contents

    def _complete_local(self, route: str, model: str, payload: dict[str, Any], timeout: int,
                        step: int = 0) -> dict[str, Any]:
        """One completion from the local Ollama model (native API), as an OpenAI-style message."""
        body: dict[str, Any] = {
            "model": model, "messages": payload["messages"], "stream": False, "think": False,
            "options": {"temperature": payload.get("temperature", 0.3), "num_predict": payload.get("max_tokens", 800),
                        "num_thread": self.LOCAL_THREADS, "num_ctx": self.LOCAL_CTX},
        }
        if (payload.get("response_format") or {}).get("type") == "json_object":
            body["format"] = "json"
        if payload.get("tools"):
            body["tools"] = payload["tools"]
        req = urllib.request.Request(self.urls["ollama"], data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        started = time.time()
        wait = max(timeout, self.LOCAL_TIMEOUT)
        try:
            with urllib.request.urlopen(req, timeout=wait) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self._failed(route, "error", started)
            if e.code == 404:          # the model isn't pulled
                self._bench(route, "isn't installed in Ollama", self.LOCAL_DOWN_COOLDOWN)
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
            timed_out = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
            self._failed(route, "timeout" if timed_out else "error", started)
            self._bench(route, f"didn't answer within {wait}s" if timed_out else "isn't running",
                        self.TIMEOUT_COOLDOWN if timed_out else self.LOCAL_DOWN_COOLDOWN)
            raise
        msg = data.get("message") or {}
        usage = {"prompt_tokens": data.get("prompt_eval_count", 0), "completion_tokens": data.get("eval_count", 0)}
        self._record_usage("ollama", model, {"usage": usage}, None, started, step)
        out: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            out["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                                  "function": {"name": c["function"]["name"],
                                               "arguments": json.dumps(c["function"].get("arguments") or {})}}
                                 for i, c in enumerate(msg["tool_calls"])]
        return out

    @staticmethod
    def _text(msg: dict[str, Any]) -> str:
        return re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.DOTALL).strip()
