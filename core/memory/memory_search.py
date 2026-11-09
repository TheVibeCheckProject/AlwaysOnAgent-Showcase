"""
Search for the AlwaysOnAgent assistant.

Split out of core/memory.py §11 on 2026-11-03; MemoryManager inherits
these methods via _SearchMixin, so they are still bound methods on the manager.
"""


import datetime
import logging
import re
import threading
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core.database import get_iso_now

    from .memory_text import _STOPWORDS, _blob_to_vec, _fts_query
except ImportError:
    from database import get_iso_now  # type: ignore[no-redef]

    from .memory_text import _STOPWORDS, _blob_to_vec, _fts_query


class _SearchMixin:
    # Host state / methods supplied by MemoryManager.__init__ or other mixins
    # (see core/memory/manager.py). Annotation-only attributes declare the type
    # without creating a runtime value; methods live in TYPE_CHECKING so a stub
    # can never shadow a sibling mixin's real method at runtime (MRO rule).
    db: "DatabaseManager"
    log: logging.Logger
    conv: str
    _cache_lock: threading.Lock
    _msg_cache: dict[int, np.ndarray]
    _msg_cache_mark: float
    VEC_MIN_FACT: float
    VEC_MIN_ARCHIVE: float

    if TYPE_CHECKING:
        def _embed(self, texts: list[str], query: bool = False, wait: bool = False) -> np.ndarray | None:
            raise NotImplementedError

        def _fact_matrix(self) -> tuple[list[int], np.ndarray | None]:
            raise NotImplementedError

    @staticmethod
    def _rrf(*ranked: list[int], k: int = 60) -> dict[int, float]:
        scores: dict[int, float] = {}
        for lst in ranked:
            for rank, i in enumerate(lst):
                scores[i] = scores.get(i, 0.0) + 1.0 / (k + rank + 1)
        return scores

    @staticmethod
    def _fact_valid_filter(at_timestamp: str | None) -> tuple[str, list[str]]:
        if at_timestamp:
            return ("(f.valid_from <= ? OR f.valid_from IS NULL) AND (f.invalid_from > ? OR f.invalid_from IS NULL)",
                    [at_timestamp, at_timestamp])
        return "f.status = 'active' AND f.invalid_from IS NULL", []

    def _fact_keyword_ids(self, conn, query: str, valid_filter: str, valid_args: list[str]) -> list[int]:
        match = _fts_query(query)
        if not match:
            return []
        try:
            return [r[0] for r in conn.execute(
                f"SELECT f.id FROM mem_facts_fts JOIN mem_facts f ON f.id = mem_facts_fts.rowid "
                f"WHERE mem_facts_fts MATCH ? AND {valid_filter} ORDER BY bm25(mem_facts_fts) LIMIT 30",
                [match] + valid_args
            ).fetchall()]
        except Exception as e:
            self.log.debug(f"FTS fact search failed: {e}")
            return []

    def _fact_substring_ids(self, conn, query: str, valid_filter: str, valid_args: list[str]) -> list[int]:
        long_words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) >= 5 and w not in _STOPWORDS]
        if not long_words:
            return []
        try:
            return [r[0] for r in conn.execute(
                f"SELECT f.id FROM mem_facts_tri JOIN mem_facts f ON f.id = mem_facts_tri.rowid "
                f"WHERE mem_facts_tri MATCH ? AND {valid_filter} ORDER BY bm25(mem_facts_tri) LIMIT 30",
                [" OR ".join(f'"{w}"' for w in long_words[:12])] + valid_args
            ).fetchall()]
        except Exception as e:
            self.log.debug(f"Substring fact search failed: {e}")
            return []

    def _fact_semantic_ids(self, conn, query: str, qvec, at_timestamp, valid_filter, valid_args,
                           min_keyword_sim, kw, sub):
        """Semantic matches, plus the keyword lists filtered by meaning. Returns (sem, kw, sub)."""
        sem: list[int] = []
        if qvec is None:
            q = self._embed([query], query=True, wait=False)
            qvec = q[0] if q is not None else None
        if qvec is not None:
            if at_timestamp:
                rows = conn.execute(
                    f"SELECT id, embedding FROM mem_facts f WHERE {valid_filter} AND f.embedding IS NOT NULL",
                    valid_args
                ).fetchall()
                ids = [r["id"] for r in rows]
                mat = np.vstack([_blob_to_vec(r["embedding"]) for r in rows]) if rows else None
            else:
                ids, mat = self._fact_matrix()
            if mat is not None:
                sims = mat @ qvec
                order = np.argsort(-sims)[:30]
                sem = [ids[i] for i in order if sims[i] >= self.VEC_MIN_FACT]
                if min_keyword_sim is not None:
                    sim_of = dict(zip(ids, sims))
                    kw = [i for i in kw if i not in sim_of or sim_of[i] >= min_keyword_sim]
                    sub = [i for i in sub if i not in sim_of or sim_of[i] >= min_keyword_sim]
        return sem, kw, sub

    def search_facts(self, query: str, k: int = 8, touch: bool = False, qvec: np.ndarray | None = None,
                     min_keyword_sim: float | None = None, at_timestamp: str | None = None) -> list[dict[str, Any]]:
        """
        Hybrid keyword + semantic search over facts, best first.
        If at_timestamp is given (ISO 8601), finds facts valid at that historical point in time.
        Otherwise finds current active facts (invalid_from IS NULL).
        """
        conn = self.db.get_connection()
        valid_filter, valid_args = self._fact_valid_filter(at_timestamp)
        kw = self._fact_keyword_ids(conn, query, valid_filter, valid_args)
        sub = self._fact_substring_ids(conn, query, valid_filter, valid_args)
        sem, kw, sub = self._fact_semantic_ids(conn, query, qvec, at_timestamp, valid_filter, valid_args,
                                               min_keyword_sim, kw, sub)
        if not kw and not sub and not sem:
            return []
        scores = self._rrf(kw, sub, sem)
        rows = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT * FROM mem_facts WHERE id IN ({','.join('?' * len(scores))})", list(scores)
        ).fetchall()}
        for fid, row in rows.items():
            # Tie-breakers only: relevance decides, importance and pinning settle near-ties
            scores[fid] *= 1.0 + 0.004 * row["importance"] + (0.004 if row["pinned"] else 0.0)
        best = [rows[i] for i in sorted(rows, key=lambda i: -scores[i])[:k]]
        if touch and not at_timestamp:
            self._touch_facts([r["id"] for r in best])
        return best

    def _touch_facts(self, fact_ids: list[int]) -> None:
        """Marks facts as used (keeps them from fading)."""
        if not fact_ids:
            return
        now, conn = get_iso_now(), self.db.get_connection()
        with conn:
            conn.executemany("UPDATE mem_facts SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
                             [(now, fid) for fid in fact_ids])

    def _message_matrix(self, conv: str) -> tuple[list[int], np.ndarray | None]:
        """Embeddings of archived messages, loaded incrementally (the archive only grows)."""
        conn = self.db.get_connection()
        with self._cache_lock:
            rows = conn.execute(
                "SELECT id, embedding, embedded_at FROM mem_messages WHERE conv = ? AND embedding IS NOT NULL "
                "AND embedded_at >= ?", (conv, self._msg_cache_mark)
            ).fetchall()
            for r in rows:
                self._msg_cache[r["id"]] = _blob_to_vec(r["embedding"])
                self._msg_cache_mark = max(self._msg_cache_mark, r["embedded_at"] or 0.0)
            if not self._msg_cache:
                return [], None
            ids = list(self._msg_cache)
            return ids, np.vstack([self._msg_cache[i] for i in ids])

    def search_archive(self, query: str, k: int = 4, compressed_only: bool = True,
                       qvec: np.ndarray | None = None, min_sim: float | None = None,
                       min_keyword_sim: float | None = None) -> list[dict[str, Any]]:
        """
        Past messages and events (task results, etc.) relevant to the query (by default only ones no
        longer in the recent window).
        min_keyword_sim: also drop keyword-only matches less similar than this in meaning (a shared
        common word like "nice" isn't relevance). Messages without an embedding are kept.
        """
        conn = self.db.get_connection()
        cond = "m.conv = ? AND m.role IN ('user', 'assistant', 'event')" + (" AND m.compressed = 1" if compressed_only else "")
        kw: list[int] = []
        match = _fts_query(query)
        if match:
            try:
                kw = [r[0] for r in conn.execute(
                    f"SELECT m.id FROM mem_messages_fts JOIN mem_messages m ON m.id = mem_messages_fts.rowid "
                    f"WHERE mem_messages_fts MATCH ? AND {cond} ORDER BY bm25(mem_messages_fts) LIMIT 30",
                    (match, self.conv)
                ).fetchall()]
            except Exception as e:
                self.log.debug(f"FTS archive search failed: {e}")
        sem: list[int] = []
        if qvec is None:
            q = self._embed([query], query=True, wait=False)
            qvec = q[0] if q is not None else None
        if qvec is not None:
            ids, mat = self._message_matrix(self.conv)
            if mat is not None:
                sims = mat @ qvec
                order = np.argsort(-sims)[:60]
                threshold = self.VEC_MIN_ARCHIVE if min_sim is None else min_sim
                sem = [ids[i] for i in order if sims[i] >= threshold]
                if min_keyword_sim is not None:
                    sim_of = dict(zip(ids, sims))
                    kw = [i for i in kw if i not in sim_of or sim_of[i] >= min_keyword_sim]
        if not kw and not sem:
            return []
        scores = self._rrf(kw, sem)
        rows = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT m.id, m.role, m.content, m.created_at FROM mem_messages m "
            f"WHERE m.id IN ({','.join('?' * len(scores))}) AND {cond}", list(scores) + [self.conv]
        ).fetchall()}
        return [rows[i] for i in sorted(rows, key=lambda i: -scores[i])[:k]]

    def _reply_to(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """The assistant's answer right after an archived user message (the hit is often the question)."""
        if message.get("role") != "user":
            return None
        row = self.db.get_connection().execute(
            "SELECT id, role, content, created_at FROM mem_messages WHERE conv = ? AND id > ? ORDER BY id LIMIT 1",
            (self.conv, message["id"])
        ).fetchone()
        return dict(row) if row and row["role"] == "assistant" else None

    def recall_period(self, start: str, end: str | None = None, query: str = "", k: int = 12) -> dict[str, Any]:
        """
        What happened between two dates (YYYY-MM-DD, end inclusive): the summaries that overlap the
        period and the messages and events in it (the most relevant to query if given, else spread
        across the period).
        """
        lo = datetime.date.fromisoformat(start[:10])
        hi = datetime.date.fromisoformat((end or start)[:10])
        if hi < lo:
            lo, hi = hi, lo
        lo_s, hi_s = lo.isoformat(), (hi + datetime.timedelta(days=1)).isoformat()
        conn = self.db.get_connection()
        summaries = [dict(r) for r in conn.execute(
            "SELECT level, content, start_at, end_at FROM mem_summaries WHERE conv = ? AND rolled_up = 0 "
            "AND start_at < ? AND end_at >= ? ORDER BY start_msg_id", (self.conv, hi_s, lo_s)
        ).fetchall()]
        fact_rows = conn.execute(
            "SELECT id, content, category, valid_from, invalid_from, status FROM mem_facts "
            "WHERE (valid_from IS NULL OR valid_from < ?) "
            "AND (invalid_from IS NULL OR invalid_from >= ?) ORDER BY importance DESC LIMIT 10",
            (hi_s, lo_s)
        ).fetchall()
        period_facts = [dict(r) for r in fact_rows]
        in_range = "conv = ? AND role IN ('user', 'assistant', 'event') AND created_at >= ? AND created_at < ?"
        total = conn.execute(f"SELECT COUNT(*) FROM mem_messages WHERE {in_range}", (self.conv, lo_s, hi_s)).fetchone()[0]
        if query.strip():
            hits = self.search_archive(query, k=60, compressed_only=False)
            messages = [m for m in hits if lo_s <= m["created_at"][:10] < hi_s][:k]
        else:
            rows = conn.execute(f"SELECT id, role, content, created_at FROM mem_messages WHERE {in_range} ORDER BY id",
                                (self.conv, lo_s, hi_s)).fetchall()
            step = max(1, len(rows) // k)
            messages = [dict(r) for r in rows[::step][:k]]
        return {"start": lo.isoformat(), "end": hi.isoformat(), "summaries": summaries,
                "facts": period_facts, "messages": messages, "messages_in_period": total}
