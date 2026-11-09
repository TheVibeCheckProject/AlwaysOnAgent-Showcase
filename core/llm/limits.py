"""Limits, failures and benching for the CloudLLM calls
====================================================
How it stays inside the free tiers and away from a broken route: wait out short
limits, pace requests per minute, skip a request too big for a route, cool down a
route that is out of budget, and bench one that is overloaded or timing out.

Split out of core/llm.py (now the core/llm/ package) §4 on 2026-11-03; core/llm.py re-exports the
public names for backwards compatibility.
"""
import datetime
import email.message
import json
import logging
import re
import threading
import time
import urllib.error
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.llm_usage import UsageLog

class _LimitsMixin:
    # Host state (CloudLLM __init__ / class attributes): annotation-only, no runtime value.
    DEFAULT_COOLDOWN: float
    OVERLOAD_COOLDOWN: float
    TIMEOUT_COOLDOWN: float
    BENCH_MIN_TIMEOUT: int
    logger: logging.Logger
    usage_log: "UsageLog | None"
    _cooldown_until: dict[str, float]

    if TYPE_CHECKING:
        # Members of mixins later in the MRO (_ProvidersMixin, _PublicMixin): declared only
        # for the type checker, so they can never shadow the real definitions at runtime.
        # The tuple types must match _ProvidersMixin's literals exactly (mypy infers fixed-
        # length tuples there, and a variable-length declaration is incompatible with them).
        REQUEST_TOKEN_CAPS: tuple[tuple[str, int]]
        MINUTE_LIMITS: tuple[tuple[str, int, int], tuple[str, int, None], tuple[str, int, None]]
        PACE_MAX_WAIT: float
        _PACE_LOCK: threading.Lock

        def _wait_allowed(self, wanted: float) -> float: ...

    @classmethod
    def _split(cls, route: str) -> tuple[str, str]:
        # Real definition lives on _RoutingMixin, ahead of this mixin in the MRO.
        raise NotImplementedError

    @staticmethod
    def _rate_limit_wait(err: urllib.error.HTTPError) -> float | None:
        """Seconds until the route accepts requests again, from retry-after / Groq's reset headers."""
        headers: email.message.Message[str, str] | dict[str, str] = err.headers or {}
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
        except (OSError, ValueError):
            return ""

    def _cool_down(self, route: str, seconds: float | None, error_text: str) -> None:
        """Skips a route that is out of budget until it should have recovered."""
        provider, _ = self._split(route)
        err_lower = (error_text or "").lower()
        if "neuron" in err_lower:
            # Cloudflare's free daily allocation is used up; it resets at 00:00 UTC
            now = datetime.datetime.now(datetime.UTC)
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
            except (KeyError, ValueError):
                now_utc = datetime.datetime.now(datetime.UTC)
                offset = -7 if (3, 8) <= (now_utc.month, now_utc.day) <= (11, 1) else -8
                now_pac = now_utc.astimezone(datetime.timezone(datetime.timedelta(hours=offset)))
            midnight_pac = (now_pac + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds = (midnight_pac - now_pac).total_seconds()

        else:
            seconds = seconds if seconds else self.DEFAULT_COOLDOWN
        self._cooldown_until[route] = time.time() + seconds
        self.logger.warning(f"{route} is rate limited; skipping it for {seconds / 60:.0f} min")

    def _too_big(self, route: str, payload: dict[str, Any]) -> bool:
        cap = next((n for prefix, n in self.REQUEST_TOKEN_CAPS if route.startswith(prefix)), None)
        if cap is None:
            return False
        tokens = len(json.dumps(payload.get("messages", ""))) // 4 + len(json.dumps(payload.get("tools") or "")) // 4             + int(payload.get("max_tokens") or 0)
        if tokens <= cap:
            return False
        self.logger.info(f"Skipping {route}: this request is ~{tokens} tokens, over its {cap}-token limit")
        return True

    def _pace(self, route: str, payload: dict[str, Any]) -> None:
        limit = next(((n, t) for key, n, t in self.MINUTE_LIMITS if route.startswith("gemini:") and key in route), None)
        if limit is None:
            return
        rpm, tpm = limit
        tokens = len(json.dumps(payload.get("messages", ""))) // 4 + int(payload.get("max_tokens") or 0)
        if "_pace_log" not in self.__dict__:
            self._pace_log: dict[str, list[tuple[float, int]]] = {}
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

    def _failed(self, route: str, kind: str, started: float | None = None) -> None:
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

    def _bench(self, route: str, reason: str, seconds: float | None = None) -> None:
        """Skips an overloaded or unresponsive route for a while, so the next requests go straight to one that works."""
        seconds = self.OVERLOAD_COOLDOWN if seconds is None else seconds
        self._cooldown_until[route] = time.time() + seconds
        self.logger.warning(f"{route} {reason}; skipping it for {seconds:.0f} s")
