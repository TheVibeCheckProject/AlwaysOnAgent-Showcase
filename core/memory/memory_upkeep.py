"""
Idle-time upkeep for the AlwaysOnAgent assistant.

Split out of core/memory.py §16 on 2026-11-03; MemoryManager inherits
these methods via _UpkeepMixin, so they are still bound methods on the manager.
"""


import datetime
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

try:
    from core.database import get_iso_now

    from .memory_text import _short_date, _vec_to_blob
except ImportError:
    from database import get_iso_now  # type: ignore[no-redef]

    from .memory_text import _short_date, _vec_to_blob

if TYPE_CHECKING:
    from core.database import DatabaseManager


class _UpkeepMixin:
    # Attributes provided by the host class (MemoryManager.__init__) or by the
    # _SetupMixin (earlier in the MRO): declared here so mypy can see them.
    db: "DatabaseManager"
    llm: Any
    embedder: Any
    log: logging.Logger
    _cache_lock: threading.Lock
    _fact_cache: tuple[Any, list[int], np.ndarray | None]
    DUPLICATE_SIM: float
    FADE_AFTER_DAYS: int

    # Methods defined on mixins EARLIER than _UpkeepMixin in MemoryManager's
    # MRO (_SetupMixin, _FactsMixin, _SecondOpinionMixin): a runtime stub is
    # safe here — the real method wins the MRO lookup at runtime.
    def second_opinion(self, question: str) -> tuple[bool | None, str]:
        raise NotImplementedError

    @staticmethod
    def verify_question(kind: str, fact: str, context: str) -> str:
        raise NotImplementedError

    def _fact_rows(self, where: str = "status = 'active' AND invalid_from IS NULL", params: Sequence[Any] = ()) -> list[Any]:
        raise NotImplementedError

    def _fact_matrix(self) -> tuple[list[int], np.ndarray | None]:
        raise NotImplementedError

    def subject(self) -> str:
        raise NotImplementedError

    def _bg_chat(self, messages: list[dict[str, str]], purpose: str, cloud_only: bool = False, **kw: Any) -> str | None:
        raise NotImplementedError

    if TYPE_CHECKING:
        # Methods defined on mixins LATER than _UpkeepMixin in the MRO
        # (_EmbeddingsMixin, _MemoryScreenMixin): declared only for mypy, so a
        # runtime stub can never shadow the sibling mixin's real method.
        def _embed(self, texts: list[str], query: bool = False, wait: bool = False) -> np.ndarray | None:
            raise NotImplementedError

        def _mark_dirty(self) -> None:
            raise NotImplementedError

    PROFILE_PROMPT = (
        "Write the assistant's core brief about the user from these memories (most important first). "
        "It is included in every conversation, and the owner reads it in the app, so it should read like "
        "a short profile a person wrote: two to four short paragraphs of plain, natural prose (who {subject} "
        "is and where they live; what they enjoy; what they're working on; how they want the assistant "
        "to work), with related facts joined into sentences. No headings, bullets or lists. Call them "
        "{subject} and use the name rather than he or she. Where memories conflict, prefer the more "
        "recent. At most 200 words, and leave out anything not broadly useful.\n"
        "Strictly restate what the memories say: every statement must be directly supported by a listed "
        "memory. Do not infer traits, motives, priorities or preferences, and do not embellish (no "
        "'actively', 'passionate', 'prioritizes' unless a memory says so). A shorter profile is better "
        "than an invented detail."
    )

    def _embed_missing(self, limit: int = 256) -> int:
        """Embeds up to limit rows per table that have no vector; returns how many it embedded (0 = none left, or
        the model isn't answering)."""
        conn = self.db.get_connection()
        done = 0
        for table in ("mem_facts", "mem_messages", "mem_summaries"):
            rows = conn.execute(f"SELECT id, content FROM {table} WHERE embedding IS NULL LIMIT ?", (limit,)).fetchall()
            if not rows:
                continue
            vecs = self._embed([r["content"] for r in rows], wait=True)
            if vecs is None:
                return done
            now = time.time()
            with conn:
                if table == "mem_messages":
                    conn.executemany("UPDATE mem_messages SET embedding = ?, embedded_at = ? WHERE id = ?",
                                     [(_vec_to_blob(v), now, r["id"]) for v, r in zip(vecs, rows)])
                else:
                    conn.executemany(f"UPDATE {table} SET embedding = ? WHERE id = ?",
                                     [(_vec_to_blob(v), r["id"]) for v, r in zip(vecs, rows)])
            done += len(rows)
        if done:
            with self._cache_lock:
                self._fact_cache = (None, [], None)
        return done

    @staticmethod
    def _still_as_read(conn: Any, ids: Sequence[int], snapshot: dict[int, Any]) -> bool:
        """True if every one of these facts is still active and unchanged since the snapshot was read."""
        now = {r["id"]: r for r in conn.execute(
            f"SELECT id, status, invalid_from, updated_at FROM mem_facts WHERE id IN ({','.join('?' * len(ids))})",
            list(ids)).fetchall()}
        return all(i in now and now[i]["status"] == "active" and now[i]["invalid_from"] is None
                   and now[i]["updated_at"] == snapshot[i]["updated_at"] for i in ids)

    def _try_merge_pair(self, conn, keep: int, drop: int, meta, distinct, removed,
                        stats: dict[str, int]) -> None:
        """Asks the second opinion, then merges drop into keep if both are unchanged since the snapshot."""
        pair = f"{min(keep, drop)}-{max(keep, drop)}"
        agree, reason = self.second_opinion(
            self.verify_question("merge", meta[drop]["content"], meta[keep]["content"]))
        if not agree:               # None (no answer): asked again at the next upkeep
            self.log.info(f"Memory: didn't merge #{drop} into #{keep}: the second opinion "
                          + (f"disagreed: {reason}" if agree is False else "couldn't be had"))
            if agree is False:
                distinct.add(pair)
                self.db.set_state("memory_distinct_pairs", json.dumps(sorted(distinct)[-500:]))
            return
        with conn:
            # The decision was made on the snapshot above, a second-opinion call ago, while the chat's
            # memory worker may have changed either fact: merge only if both are as they were (2026-10-28
            # audit 4.2: an edited fact could be deleted on its old text, or folded into a superseded one)
            if not self._still_as_read(conn, (keep, drop), meta):
                self.log.info(f"Memory: didn't merge #{drop} into #{keep}: one of them changed meanwhile")
                return
            conn.execute("UPDATE mem_facts SET importance = MAX(importance, ?), use_count = use_count + ? WHERE id = ?",
                         (meta[drop]["importance"], meta[drop]["use_count"], keep))
            try:
                # What the duplicate was waiting on is still followed, on the fact kept
                conn.execute("UPDATE mem_open_loops SET fact_id = ? WHERE fact_id = ? AND NOT EXISTS "
                             "(SELECT 1 FROM mem_open_loops WHERE fact_id = ?)", (keep, drop, keep))
            except sqlite3.OperationalError:
                pass                          # no open loops table yet
            conn.execute("DELETE FROM mem_facts WHERE id = ?", (drop,))
        removed.add(drop)
        stats["merged"] += 1

    def _merge_duplicates(self, conn, force: bool, last: str, stats: dict[str, int]) -> None:
        """Near-duplicate facts: keep the pinned / more recently updated one."""
        ids, mat = self._fact_matrix()
        if mat is None or len(ids) <= 1:
            return
        meta = {r["id"]: r for r in self._fact_rows()}
        fresh = [i for i, fid in enumerate(ids) if force or not last or (meta.get(fid) and meta[fid]["updated_at"] > last)]
        removed: set[int] = set()
        # Pairs the second opinion said aren't the same ("likes golf" / "doesn't like golf" can be this close)
        distinct = set(json.loads(self.db.get_state("memory_distinct_pairs", "[]") or "[]"))
        for i in fresh:
            if ids[i] in removed:
                continue
            sims = mat @ mat[i]
            for j in np.where(sims >= self.DUPLICATE_SIM)[0]:
                a, b = ids[i], ids[int(j)]
                if a == b or b in removed or a in removed or a not in meta or b not in meta:
                    continue
                pair = f"{min(a, b)}-{max(a, b)}"
                if pair in distinct:
                    continue
                ra, rb = meta[a], meta[b]
                keep, drop = (a, b) if (ra["pinned"], ra["updated_at"]) >= (rb["pinned"], rb["updated_at"]) else (b, a)
                self._try_merge_pair(conn, keep, drop, meta, distinct, removed, stats)

    def _fade_stale_facts(self, conn) -> int:
        """Fades unused, low-importance facts (kept and searchable, but no longer offered to the LLM)."""
        cutoff = (datetime.datetime.now().astimezone() - datetime.timedelta(days=self.FADE_AFTER_DAYS)).isoformat()
        with conn:
            cur = conn.execute(
                "UPDATE mem_facts SET status = 'faded' WHERE status = 'active' AND pinned = 0 AND importance <= 2 "
                "AND updated_at < ? AND (last_used_at IS NULL OR last_used_at < ?)", (cutoff, cutoff)
            )
        return cur.rowcount or 0

    def consolidate(self, force: bool = False) -> dict[str, int]:
        """Idle-time upkeep: embed anything missing, merge duplicates, fade stale trivia, rebuild the profile."""
        stats = {"embedded": 0, "merged": 0, "faded": 0, "profile": 0}
        conn = self.db.get_connection()

        if self.embedder and self.embedder.ensure_loaded():
            stats["embedded"] = self._embed_missing()

        last = self.db.get_state("memory_last_consolidated", "")
        self._merge_duplicates(conn, force, last, stats)

        stats["faded"] = self._fade_stale_facts(conn)
        if stats["merged"] or stats["faded"]:
            self._mark_dirty()

        if (force or self.db.get_state("memory_profile_dirty", "0") == "1") and self.llm and self.rebuild_profile():
            stats["profile"] = 1
        self.db.set_state("memory_last_consolidated", get_iso_now())
        return stats

    def rebuild_profile(self) -> bool:
        facts = self._fact_rows()[:80]
        if not facts:
            self.db.set_state("memory_profile", "")
            self.db.set_state("memory_profile_dirty", "0")
            return True
        listing = "\n".join(
            f"- [{r['category']}, importance {r['importance']}{', pinned' if r['pinned'] else ''}, {_short_date(r['updated_at'])}] {r['content']}"
            for r in facts)
        system = self.PROFILE_PROMPT.format(subject=self.subject())
        profile = self._bg_chat([
            {"role": "system", "content": system},
            {"role": "user", "content": listing}
        ], "profile", max_tokens=700, temperature=0.2, timeout=60)
        if not profile:
            return False
        self.db.set_state("memory_profile", profile.strip())
        self.db.set_state("memory_profile_dirty", "0")
        self.log.info("Rebuilt the core memory profile.")
        return True
