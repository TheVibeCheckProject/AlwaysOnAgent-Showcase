"""
Embeddings for the AlwaysOnAgent assistant.

Split out of core/memory.py §6 on 2026-11-03; MemoryManager inherits
these methods via _EmbeddingsMixin, so _embed() / _embedding_blob() /
_check_embedding_model() are still bound methods on the manager.
"""

import concurrent.futures
import logging
import threading
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .memory_text import _vec_to_blob
except ImportError:
    from .memory_text import _vec_to_blob

class _EmbeddingsMixin:
    # --- Host state supplied by MemoryManager.__init__ (declared, not set, here) ---
    db: "DatabaseManager"
    embedder: Any
    log: logging.Logger
    _bg: concurrent.futures.ThreadPoolExecutor
    _cache_lock: threading.Lock
    _msg_cache: dict[int, np.ndarray]
    _msg_cache_mark: float
    _fact_cache: tuple[Any, list[int], np.ndarray | None]

    def _embed_missing(self, limit: int = 256) -> int:
        # Real method lives on _UpkeepMixin, EARLIER in the MRO: this stub never runs.
        raise NotImplementedError

    EMBEDDING_MODEL_KEY = "memory_embedding_model"
    LEGACY_EMBEDDING_MODEL = "bge-small-en-v1.5-int8"      # what every vector stored before the tag was made with

    def _check_embedding_model(self) -> None:
        """Vectors from different models can't be compared: when the search model changes, drop the stored ones and
        re-embed everything in the background (search uses keywords for what isn't re-embedded yet)."""
        if self.embedder is None:
            return
        current = getattr(self.embedder, "MODEL_ID", None) or type(self.embedder).__name__
        stored = self.db.get_state(self.EMBEDDING_MODEL_KEY, "") or self.LEGACY_EMBEDDING_MODEL
        if stored == current:
            return
        conn = self.db.get_connection()
        with conn:
            conn.execute("UPDATE mem_facts SET embedding = NULL")
            conn.execute("UPDATE mem_messages SET embedding = NULL, embedded_at = NULL")
            conn.execute("UPDATE mem_summaries SET embedding = NULL")
            conn.execute("UPDATE mem_procedures SET embedding = NULL")     # re-embedded when next needed
        self.db.set_state(self.EMBEDDING_MODEL_KEY, current)
        with self._cache_lock:
            self._msg_cache.clear()
            self._msg_cache_mark = 0.0
            self._fact_cache = (None, [], None)
        self.log.info(f"Memory search model changed ({stored} -> {current}): re-embedding all memory.")
        self._bg.submit(self._embed_all_missing)

    def _embed_all_missing(self) -> None:
        try:
            if self.embedder.ensure_loaded():
                while self._embed_missing():
                    pass
                self.log.info("Memory re-embedded with the new search model.")
        except Exception as e:
            self.log.warning(f"Re-embedding memory failed (idle-time upkeep retries): {e}")

    def _embed(self, texts: list[str], query: bool = False, wait: bool = False) -> np.ndarray | None:
        if not self.embedder or not texts:
            return None
        try:
            return self.embedder.embed(texts, query=query, wait=wait)
        except Exception as e:
            self.log.warning(f"Embedding failed: {e}")
            return None

    def _embedding_blob(self, text: str) -> bytes | None:
        """Stored embedding for text, or None if the model isn't loaded yet (idle-time upkeep fills it in)."""
        vec = self._embed([text], wait=False)
        return _vec_to_blob(vec[0]) if vec is not None else None
