"""
Language models for the whole agent
===================================
- CloudLLM: the free cloud providers (Gemini first, then Groq, Cloudflare Workers AI, Mistral) behind
  one OpenAI-style chat API, with failover between them. Used for chat, memory upkeep and coding
  tasks. (Video quotes are written by TiktokVideos itself.) See docs/AI_MODELS.md.
- OllamaClient: a local Ollama server, the offline fallback.

Nothing here writes files or runs tasks; that is core/task_engines.py.
"""

import datetime
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from core.config import get_config, redact_secrets
except ImportError:
    from config import get_config, redact_secrets


# Markdown's own repeated characters (table rules, separators, emphasis) never count
_RUN_OF_ONE_CHAR = re.compile(r"([^\-=_*.#~|:`])\1{39,}", re.DOTALL)


def degenerate(text: str) -> bool:
    """A broken model's output: one character over and over (2026-10-04, Cloudflare's gpt-oss-120b answered a
    weather question with hundreds of "!"). Treated like an empty reply, so the next model answers instead."""
    t = (text or "").strip()
    if _RUN_OF_ONE_CHAR.search(t.replace(" ", "")):
        return True
    chars = [c for c in t if not c.isspace() and c not in "-=_*.#~|:`"]
    return len(chars) >= 20 and max(chars.count(c) for c in set(chars)) > 0.6 * len(chars)


class CloudLLM:
    """
    Cloud inference (zero local load) across free provider tiers that speak the same OpenAI-style
    chat API:
      - Groq:                   gpt-oss-120b, gpt-oss-20b and Qwen; very fast, with a prompt cache
      - Cloudflare Workers AI:  the same gpt-oss-120b, about 45 chat messages a day on the free plan
      - Mistral (free plan, training switched off): ministral-14b for chat, Codestral for code
    A model is named by a route, "provider:model" (a bare model name means Groq). Each call walks
    a list of routes and moves on when one is rate limited or failing. A route that is out of
    budget for longer than MAX_RATE_LIMIT_WAIT is skipped until it should have recovered, so an
    exhausted daily limit costs one request instead of one per message. Providers without a key in
    .env are skipped.
    """

    PROVIDERS = ("gemini", "groq", "cloudflare", "mistral", "ollama")
    # The local model on this machine (docs/IDEAS.md #10), as "ollama:<model>" routes for background memory work.
    # Native /api/chat, not the OpenAI endpoint: only it takes thinking off and per-request CPU limits. The agent
    # never starts Ollama; when it isn't answering, or the CPU is busy (a render), the cloud routes behind it run.
    LOCAL_THREADS = int(os.environ.get("OLLAMA_NUM_THREAD", "4"))     # of the Mini's 12: renders and the API keep room
    LOCAL_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))        # extraction prompts run to ~2.5k tokens
    LOCAL_TIMEOUT = 240            # seconds: a 4B model on CPU writes ~10 tokens/s
    LOCAL_MAX_CPU = 70.0           # skip the local model while the CPU is busier than this (%)
    LOCAL_DOWN_COOLDOWN = 300.0    # Ollama not answering (e.g. before autologon after a reboot)
    # Chat: 3.5 Flash-Lite first, then 3.8 and 3.6 Flash, 3.1 Flash-Lite, Gemma 4 26B, then gpt-oss-120b (Groq,
    # then Cloudflare)...
    # 3.5 Flash-Lite FIRST (the owner's choice, 2026-10-06, from measurements; before that every Flash came first):
    # - Oct 3-6 it answered 53 chat turns to the Flash models' 11, while the Flash models failed 57 times (daily
    #   allowance of ~20, overloads, 15 s timeouts): on Oct 6 replies waited ~3.5 minutes in total on them before
    #   Flash-Lite answered anyway.
    # - scripts/eval_models.py, tool choice on the owner's kind of messages: 3.5 Flash-Lite 20/22, 0 errors,
    #   median 0.8 s (Gemma 4 26B 19/22, ~3 s; 3.1 Flash-Lite 10/22, mostly "unavailable").
    # - Its earlier miss (2026-10-05, a misspelt sender searched literally) is handled by gmail_search's
    #   close-sender matching (42ed7d4).
    # If the Flash models are benchmarked later (after a quota reset) and one is clearly better at real replies,
    # it can go back on top: measure first (docs/GEMINI_FREE_TIER.md). Pro models have no free quota.
    # Only the CURRENT Flash models (2026-10-10, the owner's call): Google's deprecations page (updated 2026-10-09)
    # routes gemini-3.7-flash to 3.8 Flash and gemini-3.5-flash to 3.6 Flash, so listing them only tried the same
    # two models twice under old names (3.7 answered 0 chats Oct 7-10, failing 13 times). Don't add a model that
    # page lists as retired or redirected; check `GET /v1beta/models` and the page before adding one.
    # Gemma 4 26B (Google's own API, with tools since 2026-10-06) is the last Google model: 14,400 a day, but 16K
    # tokens a minute is ~3 chat calls (each carries ~5K tokens of persona and tools), and on Oct 6 afternoon
    # Google answered 11 of 12 Gemma calls with HTTP 500. A 500 comes back in ~1 s, so trying it costs little.
    CHAT_MODELS = (
        "gemini:gemini-3.5-flash-lite",
        "gemini:gemini-3.8-flash",
        "gemini:gemini-3.6-flash",
        "gemini:gemini-3.1-flash-lite",       # its own 500 a day (2026-10-06)
        "gemini:gemma-4-26b-a4b-it",
        "groq:openai/gpt-oss-120b",
        "cloudflare:@cf/openai/gpt-oss-120b",
        "groq:qwen/qwen3.8-27b",
        "groq:openai/gpt-oss-20b",
        "mistral:ministral-14b-latest",
    )
    # Background memory work: Gemini first, then Groq's models, then Cloudflare. No Flash here: its 20 a day are
    # for chat (until 2026-10-06 3.8 Flash backed up the chores and they spent chat's allowance). Gemma 4 26B
    # (14,400 a day, Google's own API) beat 3.1 Flash-Lite on the chores in scripts/eval_models.py.
    BACKGROUND_MODELS = (
        "gemini:gemini-3.5-flash-lite",
        "gemini:gemma-4-26b-a4b-it",
        "gemini:gemini-3.1-flash-lite",
        "groq:openai/gpt-oss-20b",
        "groq:qwen/qwen3.8-27b",
        "groq:openai/gpt-oss-120b",
        "cloudflare:@cf/openai/gpt-oss-20b",
    )
    # Background coding tasks: Gemini first, then Codestral
    TASK_MODELS = (
        "gemini:gemini-3.5-flash-lite",
        "gemini:gemini-3.8-flash",
        "mistral:codestral-latest",
        "groq:qwen/qwen3.8-27b",
        "groq:openai/gpt-oss-120b",
        "groq:openai/gpt-oss-20b",
    )
    MAX_RATE_LIMIT_WAIT = 20.0     # seconds worth waiting for a route's per-minute budget to refill
    DEADLINE_RESERVE = 5.0         # seconds a turn keeps back from its deadline (not started near the end)
    DEFAULT_COOLDOWN = 60.0        # how long to skip a rate-limited route that gives no reset time
    # How long to skip a route that is overloaded (HTTP 5xx) or didn't answer in time. Without this, every chat
    # message retried an overloaded Gemini model first and waited up to the full timeout before falling back.
    # 5xx answers come back at once: other providers get one retry after OVERLOAD_RETRY_WAIT, Gemini none (the
    # next Flash model is tried instead; see _complete), then a short bench. Before 2026-10-04 one 503 benched gemini-3.8-flash for 5 min, and 47 of 56 chat replies (Oct
    # 1-4) went to flash-lite while 3.8 Flash was called ~8 times a day: blips, not quota (IDEAS #12).
    OVERLOAD_COOLDOWN = 60.0
    OVERLOAD_RETRY_WAIT = 2.0
    TIMEOUT_COOLDOWN = 180.0       # a hung route costs a full timeout per try, so it's skipped longer
    BENCH_MIN_TIMEOUT = 15         # a timeout shorter than this (the tool router's 5 s) doesn't bench the route
    TURN_FLASH_FAILURES = 2        # failed models in one chat reply before the rest of it skips the other Flash ones
    # Thinking level per Gemini model (sent as reasoning_effort; Google maps it to thinking_level). Unset, 3.8 and 3.6
    # Flash think at "medium": 3.8 Flash timed out at 20 s on every chat attempt in the week to 2026-10-27. 3.8 Flash's
    # lowest level is "low"; Flash-Lite's default is already "minimal". docs/archive/AUDIT_2026-10-27.md T3.
    GEMINI_REASONING = {"gemini-3.8-flash": "low", "gemini-3.6-flash": "low"}

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "qwen/qwen3.8-27b",
        logger: Optional[logging.Logger] = None,
        keys: Optional[Dict[str, str]] = None,
        cloudflare_account: Optional[str] = None
    ):
        """
        api_key: the Groq key (default: GROQ_API_KEY). keys: {provider: key} replacing the .env lookup
        entirely (tests). cloudflare_account: the Workers AI account id (default: CLOUDFLARE_ACCOUNT_ID).
        """
        if keys is None:
            keys = {
                "gemini": (os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")).strip(),
                "groq": api_key or os.environ.get("GROQ_API_KEY", ""),
                "cloudflare": os.environ.get("CLOUDFLARE_API_TOKEN", ""),
                "mistral": os.environ.get("MISTRAL_API_KEY", ""),
                "ollama": "local",          # no key; used only by routes that name it
            }
        self.keys = {p: (k or "").strip() for p, k in keys.items()}
        self.api_key = self.keys.get("groq", "")
        account = (cloudflare_account if cloudflare_account is not None
                   else os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")).strip()
        if not account:
            self.keys.pop("cloudflare", None)
        self.urls = {
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "cloudflare": f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions",
            "mistral": "https://api.mistral.ai/v1/chat/completions",
            "ollama": get_config("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/api/chat",
        }
        self.default_model = default_model if default_model != "qwen/qwen3.8-27b" else (self.CHAT_MODELS[0] if self.keys.get("gemini") else "qwen/qwen3.8-27b")
        self.logger = logger or logging.getLogger("CloudLLM")
        self.usage_log = None      # a core.llm_usage.UsageLog; set by MemoryManager so every call's tokens are recorded
        self._cooldown_until: Dict[str, float] = {}
        self._local = threading.local()     # per thread: the HUD answers several chats at once

    def is_available(self) -> bool:
        return any(self.keys.get(p) for p in self.PROVIDERS if p != "ollama")

    def _cpu_busy(self) -> bool:
        try:
            import psutil
            return psutil.cpu_percent(interval=0.3) > self.LOCAL_MAX_CPU
        except Exception:
            return False

    # ------------------------------------------------------------------ routes
    @classmethod
    def _split(cls, route: str) -> Tuple[str, str]:
        provider, sep, model = route.partition(":")
        return (provider, model) if sep and provider in cls.PROVIDERS else ("groq", route)

    def _candidates(self, routes: Optional[Sequence[str]]) -> List[str]:
        """Routes to try, in order: configured providers only, skipping those cooling down (unless all are)."""
        order = list(routes) if routes else [self.default_model, *self.CHAT_MODELS]
        usable: List[str] = []
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
    def _mistral_tool_ids(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

    def _prepare(self, provider: str, model: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = {**payload, "model": model}
        if model.startswith(("openai/gpt-oss", "@cf/openai/gpt-oss")):
            body["reasoning_effort"] = "low"     # hidden reasoning tokens count against the limits
        elif provider == "gemini" and model in self.GEMINI_REASONING:
            body["reasoning_effort"] = self.GEMINI_REASONING[model]
        if provider == "mistral":
            body["messages"] = self._mistral_tool_ids(body["messages"])
        return body

    # ------------------------------------------------------------------ rate limits
    @staticmethod
    def _rate_limit_wait(err: urllib.error.HTTPError) -> Optional[float]:
        """Seconds until the route accepts requests again, from retry-after / Groq's reset headers."""
        headers = err.headers or {}
        ra = headers.get("retry-after")
        if ra:
            try:
                return float(ra)
            except ValueError:
                pass
        reset = headers.get("x-ratelimit-reset-tokens") or ""
        m = re.fullmatch(r"(?:(\d+)m)?(?:([\d.]+)s)?(?:([\d.]+)ms)?", reset.strip())
        if m and any(m.groups()):
            mins, secs, ms = m.groups()
            return int(mins or 0) * 60 + float(secs or 0) + float(ms or 0) / 1000.0
        return None

    @staticmethod
    def _error_text(err: urllib.error.HTTPError) -> str:
        try:
            return err.read().decode("utf-8", "replace")
        except Exception:
            return ""

    def _cool_down(self, route: str, seconds: Optional[float], error_text: str) -> None:
        """Skips a route that is out of budget until it should have recovered."""
        provider, _ = self._split(route)
        err_lower = (error_text or "").lower()
        if "neuron" in err_lower:
            # Cloudflare's free daily allocation is used up; it resets at 00:00 UTC
            now = datetime.datetime.now(datetime.timezone.utc)
            midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds = (midnight - now).total_seconds()
        elif provider == "gemini" and self._daily_quota(provider, err_lower):
            # Only the per-DAY quota: per-minute limits also say "Quota exceeded" but clear in seconds, and benching
            # the best chat model until midnight for one of those would quietly downgrade chat for the day
            # Google Gemini free tier daily requests reset at 00:00 US Pacific time
            try:
                import zoneinfo
                pacific = zoneinfo.ZoneInfo("America/Los_Angeles")
                now_pac = datetime.datetime.now(pacific)
            except Exception:
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                offset = -7 if (3, 8) <= (now_utc.month, now_utc.day) <= (11, 1) else -8
                now_pac = now_utc.astimezone(datetime.timezone(datetime.timedelta(hours=offset)))
            midnight_pac = (now_pac + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds = (midnight_pac - now_pac).total_seconds()

        else:
            seconds = seconds if seconds else self.DEFAULT_COOLDOWN
        self._cooldown_until[route] = time.time() + seconds
        self.logger.warning(f"{route} is rate limited; skipping it for {seconds / 60:.0f} min")

    @property
    def last_route(self) -> Optional[str]:
        """The route that answered this thread's last chat()/chat_with_tools() call (None if none did)."""
        return getattr(self._local, "route", None)

    # ------------------------------------------------------------------ requests
    def _record_usage(self, provider: str, model: str, data: Dict[str, Any], resp: Any, started: float,
                      step: int = 0) -> None:
        if self.usage_log is None:
            return
        headers = getattr(resp, "headers", None) or {}
        remaining = headers.get("x-ratelimit-remaining-tokens") or headers.get("x-ratelimit-remaining-tokens-minute")
        self.usage_log.record(model, data.get("usage"), step=step, duration_ms=int((time.time() - started) * 1000),
                              remaining_tokens=int(remaining) if remaining and remaining.isdigit() else None,
                              provider=provider)

    def _complete(self, route: str, payload: Dict[str, Any], timeout: int, step: int = 0) -> Dict[str, Any]:
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
                text = self._error_text(e)
                if e.code in (500, 502, 503, 504):
                    # No retry on Gemini: its free tier allows 20 requests a day per Flash model and failed calls
                    # seem to count (2026-10-05: 3.7 Flash "used" 20 with no answer), so the next Flash goes now
                    if attempt == 0 and provider != "gemini":
                        self.logger.info(f"{route} is overloaded (HTTP {e.code}); retrying once in "
                                         f"{self.OVERLOAD_RETRY_WAIT:.0f}s")
                        time.sleep(self.OVERLOAD_RETRY_WAIT)
                        continue
                    self._bench(route, f"is overloaded (HTTP {e.code})")
                    self._failed(route, "overloaded", started)
                    raise
                if e.code != 429 and "neuron" not in text.lower():
                    # The provider's own reason, so a rejected request can be diagnosed from the log (2026-10-10:
                    # Mistral's 422s of Oct 6 left only "Unprocessable Entity" and couldn't be reproduced)
                    self.logger.warning(f"{route} rejected the request (HTTP {e.code}): "
                                        f"{redact_secrets(' '.join(text.split()))[:300] or e.reason}")
                    self._failed(route, "error", started)
                    raise
                wait = self._rate_limit_wait(e)
                if attempt == 0 and wait is not None and wait <= self._wait_allowed(self.MAX_RATE_LIMIT_WAIT):
                    self.logger.info(f"Rate limit on {route}: waiting {wait:.1f}s")
                    time.sleep(wait + 0.5)
                    continue
                self._cool_down(route, wait, text)
                self._failed(route, "quota_day" if self._daily_quota(provider, text) else "rate_limit", started)
                raise
            except (TimeoutError, urllib.error.URLError) as e:
                if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                    self._timed_out(route, timeout)
                    self._failed(route, "timeout", started)
                else:
                    self._failed(route, "error", started)
                raise

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

    def _too_big(self, route: str, payload: Dict[str, Any]) -> bool:
        cap = next((n for prefix, n in self.REQUEST_TOKEN_CAPS if route.startswith(prefix)), None)
        if cap is None:
            return False
        tokens = len(json.dumps(payload.get("messages", ""))) // 4 + len(json.dumps(payload.get("tools") or "")) // 4             + int(payload.get("max_tokens") or 0)
        if tokens <= cap:
            return False
        self.logger.info(f"Skipping {route}: this request is ~{tokens} tokens, over its {cap}-token limit")
        return True

    def _pace(self, route: str, payload: Dict[str, Any]) -> None:
        limit = next(((n, t) for key, n, t in self.MINUTE_LIMITS if route.startswith("gemini:") and key in route), None)
        if limit is None:
            return
        rpm, tpm = limit
        tokens = len(json.dumps(payload.get("messages", ""))) // 4 + int(payload.get("max_tokens") or 0)
        if "_pace_log" not in self.__dict__:
            self._pace_log = {}
        # The lock is held only to read and update the window, never while sleeping: until 2026-10-31 a background chore
        # waiting for room on one model held it for up to PACE_MAX_WAIT and stalled a chat call to another model
        end = time.time() + self._wait_allowed(self.PACE_MAX_WAIT)
        while True:
            with self._PACE_LOCK:
                window = self._pace_log.setdefault(route, [])
                now = time.time()
                window[:] = [(t, n) for t, n in window if now - t < 60]
                room = len(window) < rpm and (tpm is None or sum(n for _, n in window) + tokens <= tpm)
                if room or now >= end:
                    if not room:
                        self.logger.info(f"{route}: past its per-minute limit; sending anyway")
                    window.append((time.time(), tokens))
                    return
            time.sleep(0.5)

    def _complete_google(self, route: str, model: str, payload: Dict[str, Any], timeout: int,
                         step: int = 0) -> Dict[str, Any]:
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
        body: Dict[str, Any] = {"contents": contents, "generationConfig": {
            "maxOutputTokens": max(int(payload.get("max_tokens") or 0), self.GEMMA_MIN_TOKENS),
            "temperature": payload.get("temperature", 0.4)}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if payload.get("tools"):
            body["tools"] = [{"functionDeclarations": [self._google_function(t) for t in payload["tools"]]}]
        # Gemma thinks before it answers or calls a tool: a 15 s chat timeout cut healthy answers short
        timeout = max(timeout, self.GEMMA_MIN_TIMEOUT)
        req = urllib.request.Request(f"{self.GOOGLE_API}/{model}:generateContent",
                                     data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"x-goog-api-key": self.keys["gemini"],
                                              "Content-Type": "application/json", "User-Agent": "AlwaysOnAgent/1.0"})
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
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
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought") and "text" in p).strip()
        usage = data.get("usageMetadata") or {}
        self._record_usage("gemini", model, {"usage": {"prompt_tokens": usage.get("promptTokenCount", 0),
                                                       "completion_tokens": usage.get("candidatesTokenCount", 0)}},
                           None, started, step)
        msg: Dict[str, Any] = {"role": "assistant", "content": text}
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
    def _google_call_parts(self) -> Dict[str, Dict[str, Any]]:
        """Gemma's functionCall parts by tool-call id, to resend unchanged in the next round (bounded)."""
        store = self.__dict__.setdefault("_google_parts_store", {})
        while len(store) > 200:
            store.pop(next(iter(store)))
        return store

    @staticmethod
    def _google_function(tool: Dict[str, Any]) -> Dict[str, Any]:
        """One OpenAI-style tool spec as a Google functionDeclaration (no parameters key when it takes none:
        Google rejects an empty object schema)."""
        f = tool.get("function") or tool
        d: Dict[str, Any] = {"name": f["name"], "description": f.get("description", "")}
        params = f.get("parameters") or {}
        if params.get("properties"):
            d["parameters"] = params
        return d

    def _google_contents(self, msgs: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        """An OpenAI-style conversation as Google's (systemInstruction text, contents). Assistant tool calls become
        model functionCall parts, tool results user functionResponse parts; neighbours with the same role are
        merged (Google expects user and model turns to alternate)."""
        system = "\n\n".join(str(m.get("content") or "") for m in msgs if m.get("role") == "system")
        names: Dict[str, str] = {}
        contents: List[Dict[str, Any]] = []

        def add(role: str, part: Dict[str, Any]) -> None:
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

    def _complete_local(self, route: str, model: str, payload: Dict[str, Any], timeout: int,
                        step: int = 0) -> Dict[str, Any]:
        """One completion from the local Ollama model (native API), as an OpenAI-style message."""
        body: Dict[str, Any] = {
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
        out: Dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            out["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                                  "function": {"name": c["function"]["name"],
                                               "arguments": json.dumps(c["function"].get("arguments") or {})}}
                                 for i, c in enumerate(msg["tool_calls"])]
        return out

    def _failed(self, route: str, kind: str, started: Optional[float] = None) -> None:
        """Records why a route gave no answer (core/llm_usage.FAILURE_KINDS), for the model-health summary."""
        if self.usage_log is not None:
            provider, model = self._split(route)
            self.usage_log.record_failure(provider, model, kind,
                                          int((time.time() - started) * 1000) if started else None)

    @staticmethod
    def _daily_quota(provider: str, error_text: str) -> bool:
        """A 429 that lasts until the daily reset (not a per-minute limit, which also says "Quota exceeded")."""
        err = (error_text or "").lower()
        return "neuron" in err or (provider == "gemini" and any(k in err for k in ("perday", "per_day", "per day",
                                                                                     "daily")))

    def _timed_out(self, route: str, timeout: float) -> None:
        """A route didn't answer in time. Benched only if the wait was a normal one: a caller's deliberately short
        deadline (the tool router's 5 s) says nothing about whether the route can answer a chat reply. 2026-10-14:
        three router timeouts each benched 3.5 Flash-Lite for 180 s, and the chat replies right after them skipped
        their first model while every other Gemini model answered 503."""
        if timeout >= self.BENCH_MIN_TIMEOUT:
            self._bench(route, f"didn't answer within {timeout}s", self.TIMEOUT_COOLDOWN)
        else:
            self.logger.warning(f"{route} didn't answer within {timeout}s (a short deadline: not benched)")

    def _bench(self, route: str, reason: str, seconds: Optional[float] = None) -> None:
        """Skips an overloaded or unresponsive route for a while, so the next requests go straight to one that works."""
        seconds = self.OVERLOAD_COOLDOWN if seconds is None else seconds
        self._cooldown_until[route] = time.time() + seconds
        self.logger.warning(f"{route} {reason}; skipping it for {seconds:.0f} s")

    @staticmethod
    def _text(msg: Dict[str, Any]) -> str:
        return re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.DOTALL).strip()

    # ------------------------------------------------------------------ chat
    def complete(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 800,
        temperature: float = 0.6,
        json_mode: bool = False,
        timeout: int = 30,
        models: Optional[Sequence[str]] = None
    ) -> Tuple[Optional[str], str]:
        """
        One completion over a full message list, trying each route in turn (rate limits, outages).
        Returns (reply text with any <think> reasoning removed, the route that answered), or
        (None, the last error) if every route failed.
        """
        self._local.route = None
        if not self.is_available():
            return None, "no cloud AI provider is configured (.env)"
        last_error = "no route answered"
        for route in self._candidates(models):
            payload: Dict[str, Any] = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
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
        messages: List[Dict[str, Any]],
        max_tokens: int = 800,
        temperature: float = 0.6,
        json_mode: bool = False,
        timeout: int = 30,
        models: Optional[Sequence[str]] = None
    ) -> Optional[str]:
        """The reply text, or None if every route failed (see complete())."""
        return self.complete(messages, max_tokens, temperature, json_mode, timeout, models)[0]

    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        call_tool: Any,
        max_rounds: int = 4,
        max_tokens: int = 900,
        temperature: float = 0.4,
        # per call. 15 until 2026-10-14, then 25 for slow evenings; 20 since 10-16: it's per model and per tool round,
        # so at 25 one hung model cost a reply 25 s before the next was tried (a healthy one answers in 1-5 s)
        timeout: int = 20,
        models: Optional[Sequence[str]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        deadline: Optional[float] = None
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
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

    def _time_left(self) -> Optional[float]:
        """Seconds until this thread's turn deadline, or None without one."""
        deadline = getattr(getattr(self, "_local", None), "deadline", None)
        return None if deadline is None else deadline - time.time()

    def _wait_allowed(self, wanted: float) -> float:
        """The longest sleep a rate limit or the pacing may take now: within the turn's deadline, if any."""
        left = self._time_left()
        return wanted if left is None else max(0.0, min(wanted, left - self.DEADLINE_RESERVE))

    def _chat_with_tools(self, messages, tools, call_tool, max_rounds, max_tokens, temperature, timeout, models,
                         cancelled, deadline) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        convo = list(messages)
        trace: List[Dict[str, Any]] = []
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
                    payload: Dict[str, Any] = {"messages": convo, "temperature": temperature, "max_tokens": max_tokens}
                    if rounds_used < max_rounds:
                        payload["tools"] = tools
                        payload["tool_choice"] = "auto"
                    if self._too_big(route, payload):
                        break                                 # the next route continues this turn
                    left = self._time_left()
                    if left is not None and left < self.DEADLINE_RESERVE:
                        self.logger.warning(f"[chat] the turn's time is up: no more models ({len(trace)} tool call(s) ran)")
                        return None, trace
                    call_timeout = timeout if left is None else max(1, min(timeout, int(left - self.DEADLINE_RESERVE)))
                    msg = self._complete(route, payload, call_timeout, step=rounds_used)
                    calls = (msg.get("tool_calls") or []) if rounds_used < max_rounds else []
                    if not calls:
                        content = self._text(msg)
                        if content and not degenerate(content):
                            self._local.route = route
                            return content, trace
                        if not content and rounds_used and not nudged:
                            # Gemini 3.x sometimes ends a tool round with an empty turn (2026-10-26, twice; widely
                            # reported). Asking the same route once costs ~1 s; moving on cost a 20 s Flash timeout.
                            nudged = True
                            self.logger.info(f"[chat] {route} answered a tool round with nothing; asking it again")
                            convo.append({"role": "user", "content": "(Now answer the user in plain text, briefly.)"})
                            continue
                        if content:
                            self.logger.warning(f"[chat] {route} answered with garbage ({content[:40]!r}…); "
                                                f"trying the next model")
                        self._failed(route, "garbage")
                        failures += 1
                        break                                 # empty or garbage answer: try the next route
                    rounds_used += 1
                    convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                    for c in calls:
                        fn = c.get("function") or {}
                        if cancelled is not None and cancelled():
                            return None, trace
                        result = call_tool(fn.get("name", ""), fn.get("arguments") or "{}")
                        trace.append({"name": fn.get("name"), "arguments": fn.get("arguments"), "result": result})
                        convo.append({"role": "tool", "tool_call_id": c.get("id"), "content": result})
                    self.logger.info(f"[chat] {route} used tools: {', '.join(str(c.get('function', {}).get('name')) for c in calls)}")
            except urllib.error.HTTPError as e:
                failures += 1
                self.logger.warning(f"chat_with_tools failed on {route}: HTTP {e.code} {e.reason}")
            except Exception as e:
                failures += 1
                self.logger.warning(f"chat_with_tools failed on {route}: {e}")
        return None, trace


class OllamaClient:
    """The local Ollama server: the offline fallback for coding tasks and affirmations."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        default_model: str = "qwen2.5-coder:3b",
        logger: Optional[logging.Logger] = None
    ):
        self.host = host.rstrip("/")
        self.default_model = default_model
        self.logger = logger or logging.getLogger("OllamaClient")

    @classmethod
    def from_config(cls, logger: Optional[logging.Logger] = None) -> "OllamaClient":
        """The server and model set in .env (OLLAMA_HOST, OLLAMA_MODEL)."""
        return cls(host=get_config("OLLAMA_HOST", "http://localhost:11434"),
                   default_model=get_config("OLLAMA_MODEL", "qwen2.5-coder:3b"), logger=logger)

    def is_alive(self) -> Tuple[bool, List[str]]:
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", headers={"User-Agent": "AlwaysOnAgent"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                return True, models
        except Exception:
            return False, []

    def select_best_model(self, requested_model: Optional[str] = None) -> str:
        if requested_model:
            return requested_model
        alive, models = self.is_alive()
        if not alive:
            return self.default_model
        priority = ["qwen2.5-coder:3b", "qwen3:8b", "hermes3:8b", "hermes3"]
        for p in priority:
            for m in models:
                if p in m:
                    return m
        return models[0] if models else self.default_model

    def generate(self, prompt: str, timeout: int = 90, model: Optional[str] = None, temperature: float = 0.8,
                 max_tokens: int = 200) -> Optional[str]:
        """Plain text from the local model, or None if Ollama is unreachable or fails."""
        payload = {"model": model or self.select_best_model(), "prompt": prompt, "stream": False,
                   "options": {"temperature": temperature, "num_predict": max_tokens}}
        try:
            req = urllib.request.Request(f"{self.host}/api/generate", data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json", "User-Agent": "AlwaysOnAgent"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return (json.loads(resp.read().decode("utf-8")).get("response") or "").strip() or None
        except Exception as e:
            self.logger.warning(f"[ollama] Text generation failed: {e}")
            return None
