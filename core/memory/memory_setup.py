"""
Setup for the AlwaysOnAgent assistant.

Split out of core/memory.py §4 (minus __init__, which stays in core/memory/manager.py) on 2026-11-03; MemoryManager inherits
these methods via _SetupMixin, so they are still bound methods on the manager.
"""


import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core.database import get_iso_now
    from core.llm_usage import usage_purpose
except ImportError:
    from database import get_iso_now  # type: ignore[no-redef]
    from llm_usage import usage_purpose  # type: ignore[no-redef]


class _SetupMixin:
    # Host state, set by MemoryManager.__init__ (core/memory/manager.py).
    db: "DatabaseManager"
    llm: Any
    log: logging.Logger

    BUDGET: ClassVar[dict[str, int]] = {"profile": 350, "summaries": 900, "facts": 500, "archive": 400, "history": 3200}
    # A chat turn's whole budget for models and tools: DeskAI waits 260 s, then resends (audit 2.4)
    TURN_SECONDS = 150
    COMPRESS_TRIGGER = 2000      # uncompressed recent tokens that trigger compression
    KEEP_RECENT = 800            # verbatim tokens kept after compressing
    ROLLUP_KEEP = 4              # unrolled summaries allowed per level before merging
    ROLLUP_MERGE = 3             # how many of the oldest get merged one level up
    MAX_LEVEL = 5
    VEC_MIN_FACT = 0.45          # semantic-only hits below this similarity are noise
    VEC_MIN_ARCHIVE = 0.55
    # In chat, keyword matches must also be at least this close in meaning (a shared common word isn't relevance)
    VEC_MIN_FACT_KEYWORD = 0.40
    VEC_MIN_ARCHIVE_KEYWORD = 0.45
    VEC_MIN_SUMMARY = 0.50       # older summaries are sent only when this close to the message
    SUMMARY_RECENT_HOURS = 6     # the newest summary is always sent while its conversation is this recent
    VEC_MIN_SCRUB = 0.40         # candidates shown to the LLM when erasing something (it decides)
    SCRUB_BATCH_CHARS = 4000     # items per erase call, so its answer (every rewrite in full) fits
    EXTRACT_ALL_FACTS_UPTO = 60  # below this many facts, extraction sees all of them
    DUPLICATE_SIM = 0.93         # a new fact this close to an existing one is the same fact
    VEC_MIN_PROCEDURE = 0.50     # a learned recipe goes with a request only this close to it (or in its area)
    # An update must be about the same thing as the fact it replaces. Measured with bge-small on
    # 2026-09-23: real updates ("posts at 9am" -> "posts at 10am") scored >= 0.71, mix-ups (the
    # extractor pointing a TikTok change at a wedding date) <= 0.69. Below this, the old fact is
    # kept and the new one saved alongside it: clutter at worst, never a lost memory.
    UPDATE_MIN_SIM = 0.70
    FADE_AFTER_DAYS = 90

    # The local model for background memory work (docs/IDEAS.md #10). MEMORY_LOCAL_MODE:
    #   off      cloud only
    #   shadow   the cloud answers and is applied; the local model answers the same call afterwards and both go to
    #            data/shadow/memory.jsonl, to compare before trusting it
    #   primary  the local model first, the cloud behind it
    # Never local, in any mode: "scrub" (erasing is permanent). An extraction the local model answered that
    # deletes or forgets anything is redone on the cloud, and only the cloud's answer is applied.
    # Default off since 2026-10-10: judged blind on 64 shadow pairs, qwen3:4b lost 26-5 to the cloud and did harm 16
    # times to the cloud's 1 (deleted a true fact, saved one-off requests as recipes, garbled the profile). IDEAS #10.
    LOCAL_MODEL = os.environ.get("MEMORY_LOCAL_MODEL", "ollama:qwen3:4b-instruct")
    LOCAL_MODE = os.environ.get("MEMORY_LOCAL_MODE", "off")
    CLOUD_ONLY_PURPOSES: ClassVar[set[str]] = {"scrub"}
    SHADOW_PURPOSES: ClassVar[set[str]] = {"extract", "summarize", "rollup", "profile", "procedure"}

    def _local_ready(self) -> bool:
        keys = getattr(self.llm, "keys", None)
        return isinstance(keys, dict) and bool(keys.get("ollama")) and self.LOCAL_MODE in ("shadow", "primary")

    def _cloud_models(self) -> list[str] | None:
        models = getattr(self.llm, "BACKGROUND_MODELS", None)
        return [m for m in models if not m.startswith("ollama:")] if models else None

    def _bg_chat(self, messages: list[dict[str, str]], purpose: str, cloud_only: bool = False, **kw) -> str | None:
        """LLM call for background memory work, on models chat doesn't use first (separate rate limits)."""
        models = self._cloud_models()
        local = self._local_ready() and not cloud_only and purpose not in self.CLOUD_ONLY_PURPOSES
        if models:
            kw["models"] = ([self.LOCAL_MODEL] + models) if local and self.LOCAL_MODE == "primary" else models
        with usage_purpose(purpose):
            out = self.llm.chat(messages, **kw)
        self._last_bg_route = getattr(self.llm, "last_route", None)      # which model's answer is applied
        if local and self.LOCAL_MODE == "shadow" and purpose in self.SHADOW_PURPOSES and models:
            self._shadow(messages, purpose, out, kw)
        return out

    def _shadow(self, messages: list[dict[str, str]], purpose: str, cloud_out: str | None, kw: dict[str, Any]) -> None:
        """The local model answers the same call; nothing it says is applied. Both answers are logged."""
        cloud_route = getattr(self.llm, "last_route", None)
        started = time.time()
        try:
            with usage_purpose(f"shadow_{purpose}"):
                local_out = self.llm.chat(messages, **{**kw, "models": [self.LOCAL_MODEL]})
        except Exception as e:
            local_out = None
            self.log.debug(f"Shadow call failed: {e}")
        entry = {"at": get_iso_now(), "purpose": purpose, "cloud_route": cloud_route, "cloud": cloud_out,
                 "local_route": getattr(self.llm, "last_route", None), "local": local_out,
                 "local_seconds": round(time.time() - started, 1),
                 "input": messages[-1]["content"][-4000:]}
        try:
            folder = Path(getattr(self.db, "db_path", "data/state.db")).parent / "shadow"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "memory.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if path.stat().st_size > 20 * 1024 * 1024:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                path.write_text("".join(lines[len(lines) // 2:]), encoding="utf-8")
        except Exception as e:
            self.log.debug(f"Shadow log not written: {e}")
