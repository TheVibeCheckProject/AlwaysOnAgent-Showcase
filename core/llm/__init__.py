"""
Language models for the whole agent
===================================
- CloudLLM: the free cloud providers (Gemini first, then Groq, Cloudflare Workers AI, Mistral) behind
  one OpenAI-style chat API, with failover between them. Used for chat, memory upkeep and coding
  tasks. (Video quotes are written by TiktokVideos itself.) See docs/AI_MODELS.md.
- OllamaClient: a local Ollama server, the offline fallback.

Nothing here writes files or runs tasks; that is core/task_engines/.

How a call goes: chat() / chat_with_tools() (§7) -> complete() walks the routes in order (§3), calling each
provider (§6) and moving on when one is rate limited, too slow, overloaded or broken (§4). What answered, and
what failed and why, is recorded (§5) for the model-health checks.

MAP OF THIS FILE
----------------
Each section starts with a banner like "# §2  ...". Search for the § number to jump straight to it.
Split up 2026-11-03; packaged as core/llm/ on 2026-11-04: sections §3-§7 are mixins in this
package, §8 is its own module; this file keeps §1, §2 and the CloudLLM shell (the model lists,
__init__, is_available, _cpu_busy). The old core/llm.py was a thin shim: the package took precedence over
the .py file, so `import core.llm` loaded this package. The shim was removed 2026-11-04.

  §1   Setup                          imports, and spotting a broken model's output
  §2   CloudLLM: the model lists      CHAT_MODELS, BACKGROUND_MODELS, TASK_MODELS, per-minute limits
  §3   Choosing a route               core/llm/routing.py (_RoutingMixin)
  §4   Limits, failures and benching  core/llm/limits.py (_LimitsMixin)
  §5   Usage records                  core/llm/usage_mixin.py (_UsageMixin)
  §6   Calling a provider             core/llm/providers.py (_ProvidersMixin)
  §7   The public calls               core/llm/public.py (_PublicMixin)
  §8   OllamaClient                   core/llm/ollama.py (OllamaClient, re-exported below)
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §1  SETUP
#     Imports, and degenerate(): an answer that is one character over and over is treated as no answer.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
import datetime
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from typing import ClassVar

try:
    from core.config import get_config, redact_secrets
except ImportError:
    from config import get_config, redact_secrets  # type: ignore[no-redef]


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



from .limits import _LimitsMixin
from .ollama import OllamaClient
from .providers import _ProvidersMixin
from .public import _PublicMixin
from .routing import _RoutingMixin
from .usage_mixin import _UsageMixin


class CloudLLM(_RoutingMixin, _LimitsMixin, _UsageMixin, _ProvidersMixin, _PublicMixin):
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
    # lowest level is "low"; Flash-Lite's default is already "minimal". docs/AUDIT_2026-10-27.md T3.
    GEMINI_REASONING: ClassVar[dict[str, str]] = {"gemini-3.8-flash": "low", "gemini-3.6-flash": "low"}

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "qwen/qwen3.8-27b",
        logger: logging.Logger | None = None,
        keys: dict[str, str] | None = None,
        cloudflare_account: str | None = None
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
        self._cooldown_until: dict[str, float] = {}
        self._local = threading.local()     # per thread: the HUD answers several chats at once

    def is_available(self) -> bool:
        return any(self.keys.get(p) for p in self.PROVIDERS if p != "ollama")

    def _cpu_busy(self) -> bool:
        try:
            import psutil
            return psutil.cpu_percent(interval=0.3) > self.LOCAL_MAX_CPU
        except Exception as e:
            self.logger.debug(f"CPU check unavailable: {e}")
            return False
