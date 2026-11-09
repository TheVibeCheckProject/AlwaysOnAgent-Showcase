"""Usage records for the CloudLLM calls
======================================
last_route (what answered this thread's last call) and the usage log behind the
model-health checks.

Split out of core/llm.py (now the core/llm/ package) §5 on 2026-11-03; core/llm.py re-exports the
public names for backwards compatibility.
"""
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.llm_usage import UsageLog

class _UsageMixin:
    # Host state, set by CloudLLM.__init__ (core/llm/__init__.py): annotation-only so mypy
    # sees them without shadowing anything at runtime.
    _local: threading.local
    usage_log: Optional["UsageLog"]

    @property
    def last_route(self) -> str | None:
        """The route that answered this thread's last chat()/chat_with_tools() call (None if none did)."""
        return getattr(self._local, "route", None)

    # ------------------------------------------------------------------ requests
    def _record_usage(self, provider: str, model: str, data: dict[str, Any], resp: Any, started: float,
                      step: int = 0) -> None:
        if self.usage_log is None:
            return
        headers = getattr(resp, "headers", None) or {}
        remaining = headers.get("x-ratelimit-remaining-tokens") or headers.get("x-ratelimit-remaining-tokens-minute")
        self.usage_log.record(model, data.get("usage"), step=step, duration_ms=int((time.time() - started) * 1000),
                              remaining_tokens=int(remaining) if remaining and remaining.isdigit() else None,
                              provider=provider)
