"""
Facts for the AlwaysOnAgent assistant.

Split out of core/memory.py §8 on 2026-11-03; MemoryManager inherits
these methods via _FactsMixin, so they are still bound methods on the manager.
"""


import concurrent.futures
import logging
import re
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

try:
    from core.config import redact_secrets
    from core.database import get_iso_now

    from .memory_text import CATEGORIES, _blob_to_vec, _evidence, _vec_to_blob
except ImportError:
    from config import redact_secrets  # type: ignore[no-redef]
    from database import get_iso_now  # type: ignore[no-redef]

    from .memory_text import CATEGORIES, _blob_to_vec, _evidence, _vec_to_blob

if TYPE_CHECKING:
    from core.database import DatabaseManager


class _FactsMixin:
    # Attributes provided by the host class (MemoryManager.__init__) or by the
    # _SetupMixin (earlier in the MRO): declared here so mypy can see them.
    db: "DatabaseManager"
    log: logging.Logger
    embedder: Any
    _bg: concurrent.futures.ThreadPoolExecutor
    _cache_lock: threading.Lock
    _fact_cache: tuple[Any, list[int], np.ndarray | None]
    DUPLICATE_SIM: float

    if TYPE_CHECKING:
        # Methods defined on mixins LATER than _FactsMixin in MemoryManager's
        # MRO (_SecondOpinionMixin, _EmbeddingsMixin, _MemoryScreenMixin):
        # declared only for mypy, so a runtime stub can never shadow the
        # sibling mixin's real method.
        def second_opinion(self, question: str) -> tuple[bool | None, str]:
            raise NotImplementedError

        @staticmethod
        def verify_question(kind: str, fact: str, context: str) -> str:
            raise NotImplementedError

        def _embed(self, texts: list[str], query: bool = False, wait: bool = False) -> np.ndarray | None:
            raise NotImplementedError

        def _mark_dirty(self) -> None:
            raise NotImplementedError

        def _scrub_safe(self, forgotten: str) -> None:
            raise NotImplementedError

    def _fact_rows(self, where: str = "status = 'active' AND invalid_from IS NULL", params: Sequence[Any] = ()) -> list[Any]:
        return self.db.get_connection().execute(
            f"SELECT * FROM mem_facts WHERE {where} ORDER BY pinned DESC, importance DESC, updated_at DESC", params
        ).fetchall()

    def get_fact(self, fact_id: int, follow_superseded: bool = True) -> dict[str, Any] | None:
        row = self.db.get_connection().execute("SELECT * FROM mem_facts WHERE id = ?", (fact_id,)).fetchone()
        if not row:
            return None
        res = dict(row)
        if follow_superseded and res.get("superseded_by"):
            visited = {fact_id}
            cur_id = res["superseded_by"]
            while cur_id and cur_id not in visited:
                visited.add(cur_id)
                next_row = self.db.get_connection().execute("SELECT * FROM mem_facts WHERE id = ?", (cur_id,)).fetchone()
                if not next_row:
                    break
                res = dict(next_row)
                cur_id = res.get("superseded_by")
        return res

    def _fact_matrix(self) -> tuple[list[int], np.ndarray | None]:
        """Embeddings of active facts, cached until any fact changes."""
        conn = self.db.get_connection()
        key = tuple(conn.execute(
            "SELECT COUNT(*), MAX(updated_at), SUM(id) FROM mem_facts WHERE status = 'active' AND invalid_from IS NULL AND embedding IS NOT NULL"
        ).fetchone())
        with self._cache_lock:
            if self._fact_cache[0] == key:
                return self._fact_cache[1], self._fact_cache[2]
            rows = conn.execute(
                "SELECT id, embedding FROM mem_facts WHERE status = 'active' AND invalid_from IS NULL AND embedding IS NOT NULL"
            ).fetchall()
            ids = [r["id"] for r in rows]
            mat = np.vstack([_blob_to_vec(r["embedding"]) for r in rows]) if rows else None
            self._fact_cache = (key, ids, mat)
            return ids, mat

    def _find_duplicate(self, content: str, vec: np.ndarray | None) -> int | None:
        norm = re.sub(r"\W+", " ", content.lower()).strip()
        for r in self._fact_rows():
            if re.sub(r"\W+", " ", r["content"].lower()).strip() == norm:
                return r["id"]
        if vec is not None:
            ids, mat = self._fact_matrix()
            if mat is not None:
                sims = mat @ vec
                best = int(np.argmax(sims))
                if sims[best] >= self.DUPLICATE_SIM:
                    # Close in meaning isn't the same fact: "posts at 9am" / "9pm" scored 0.969 and "brother named
                    # Jake" / "cousin named Jake" 0.922 with Qwen3, so the new one would have been dropped. A
                    # second opinion decides; without an answer it's saved (a duplicate at worst, never a loss).
                    row = next((r for r in self._fact_rows() if r["id"] == ids[best]), None)
                    if row is None:
                        return ids[best]
                    agree, _ = self.second_opinion(self.verify_question("merge", content, row["content"]))
                    if agree:
                        return ids[best]
                    self.log.info(f"Memory: saved {content!r} beside #{ids[best]} (close, but not the same)")
        return None

    def add_fact(self, content: str, category: str = "fact", importance: int = 3,
                 pinned: bool = False, source: str = "chat", evidence: str | None = None,
                 source_msg_id: int | None = None) -> tuple[int, bool]:
        """Stores a fact unless an equivalent one exists. Returns (fact_id, created). evidence and source_msg_id:
        where it came from (the owner's words and their message), shown in DeskAI's Memory screen."""
        content = redact_secrets(content or "").strip()
        if not content:
            raise ValueError("empty fact")
        category = category if category in CATEGORIES else "fact"
        importance = int(min(5, max(1, int(importance or 3))))
        vec = self._embed([content], wait=True)
        v0 = vec[0] if vec is not None else None
        now = get_iso_now()
        conn = self.db.get_connection()
        dup = self._find_duplicate(content, v0)
        if dup is not None:
            with conn:
                conn.execute(
                    "UPDATE mem_facts SET importance = MAX(importance, ?), pinned = MAX(pinned, ?), "
                    "status = 'active', updated_at = ?, valid_from = COALESCE(valid_from, created_at), "
                    "invalid_from = NULL WHERE id = ?",
                    (importance, int(pinned), now, dup)
                )
            self._mark_dirty()
            return dup, False
        with conn:
            cur = conn.execute(
                "INSERT INTO mem_facts (content, category, importance, pinned, source, created_at, updated_at, "
                "valid_from, invalid_from, embedding, evidence, source_msg_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (content, category, importance, int(pinned), source, now, now, now,
                 _vec_to_blob(v0) if v0 is not None else None, _evidence(evidence), source_msg_id)
            )
        self._mark_dirty()
        # lastrowid is int after a successful INSERT; -1 only if it were None.
        new_id = cur.lastrowid if cur.lastrowid is not None else -1
        return new_id, True

    def supersede_fact(self, fact_id: int, new_content: str, category: str | None = None,
                       importance: int | None = None, source: str = "chat", evidence: str | None = None,
                       source_msg_id: int | None = None) -> tuple[int, bool]:
        """
        Bi-temporally invalidates an existing fact and creates a new active fact with provenance linking.
        The old fact is marked with status='superseded' and invalid_from=now.
        The new fact is inserted with status='active' and valid_from=now.
        """
        new_content = redact_secrets(new_content or "").strip()
        if not new_content:
            raise ValueError("empty fact content")
        old_fact = self.get_fact(fact_id, follow_superseded=False)
        if not old_fact:
            return self.add_fact(new_content, category=category or "fact", importance=importance or 3,
                                 source=source, evidence=evidence, source_msg_id=source_msg_id)

        now = get_iso_now()
        cat = category if category in CATEGORIES else old_fact.get("category", "fact")
        imp = max(1, min(int(importance or old_fact.get("importance", 3)), 5))
        pinned = old_fact.get("pinned", 0)

        vec = self._embed([new_content], wait=True)
        v0 = vec[0] if vec is not None else None

        conn = self.db.get_connection()
        with conn:
            cur = conn.execute(
                "INSERT INTO mem_facts (content, category, importance, pinned, source, created_at, updated_at, "
                "valid_from, invalid_from, embedding, evidence, source_msg_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (new_content, cat, imp, pinned, source, now, now, now,
                 _vec_to_blob(v0) if v0 is not None else None, _evidence(evidence), source_msg_id)
            )
            # lastrowid is int after a successful INSERT; -1 only if it were None.
            new_id = cur.lastrowid if cur.lastrowid is not None else -1

            conn.execute(
                "UPDATE mem_facts SET status = 'superseded', invalid_from = ?, updated_at = ?, superseded_by = ? WHERE id = ?",
                (now, now, new_id, fact_id)
            )

        self._mark_dirty()
        return new_id, True

    def update_fact(self, fact_id: int, content: str, supersede: bool = True) -> bool:
        """
        Updates a fact. If supersede=True (default), preserves historical record via bi-temporal supersede.
        If supersede=False (e.g. HUD direct edit), updates row in place.
        """
        content = redact_secrets(content or "").strip()
        if not content or not self.get_fact(fact_id, follow_superseded=False):
            return False
        if supersede:
            new_id, _ = self.supersede_fact(fact_id, content)
            return bool(new_id)

        vec = self._embed([content], wait=True)
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE mem_facts SET content = ?, embedding = ?, status = 'active', updated_at = ? WHERE id = ?",
                (content, _vec_to_blob(vec[0]) if vec is not None else None, get_iso_now(), fact_id)
            )
        self._mark_dirty()
        return True

    def get_fact_history(self, fact_id: int) -> list[dict[str, Any]]:
        """
        Returns the provenance / revision chain for a fact, oldest first.
        """
        conn = self.db.get_connection()
        current = fact_id
        visited = {current}
        while True:
            prev = conn.execute("SELECT id FROM mem_facts WHERE superseded_by = ?", (current,)).fetchone()
            if prev and prev[0] not in visited:
                current = prev[0]
                visited.add(current)
            else:
                break
        root_id = current

        chain = []
        cur_id: int | None = root_id
        while cur_id is not None:
            row = conn.execute("SELECT * FROM mem_facts WHERE id = ?", (cur_id,)).fetchone()
            if not row:
                break
            chain.append(dict(row))
            cur_id = row["superseded_by"]
            if cur_id in [c["id"] for c in chain]:
                break
        return chain

    def forget_fact(self, fact_id: int, scrub: bool = False) -> bool:
        """
        Permanently deletes a fact and every revision in its supersede chain. scrub=True (the user
        asked to forget it) also erases that information from archived messages and summaries and
        rebuilds the profile, in the background.
        """
        chain = self.get_fact_history(fact_id)
        if not chain:
            fact = self.get_fact(fact_id, follow_superseded=False)
            chain = [fact] if fact else []
        if not chain:
            return False

        chain_ids = [f["id"] for f in chain]
        conn = self.db.get_connection()
        with conn:
            cur = conn.execute(f"DELETE FROM mem_facts WHERE id IN ({','.join('?' * len(chain_ids))})", chain_ids)
        if not cur.rowcount:
            return False
        self._mark_dirty()
        if scrub:
            seen_contents = set()
            for f in chain:
                content = (f.get("content") or "").strip()
                if content and content not in seen_contents:
                    seen_contents.add(content)
                    self._bg.submit(self._scrub_safe, content)
        return True

    # How memory names you: "Alex" once a profile fact says your name, "the user" before (used by the profile,
    # the extraction prompt, task briefings and the embedder's "I" -> you)
    OWNER_NAME_KEY = "owner_name"

    def owner_name(self) -> str:
        """The owner's first name, from the facts ('The user is Alex Morgan Rivera.'); '' until it's known."""
        name = self.db.get_state(self.OWNER_NAME_KEY, "")
        if not name:
            for r in self._fact_rows():
                m = re.search(r"^(?:the user|\w+)(?:'s (?:full |first )?name)? is ([A-Z][a-z]+)(?: [A-Z][a-z]+)+\.?$",
                              r["content"].strip(), re.IGNORECASE)
                if m and r["category"] == "profile":
                    name = m.group(1)
                    self.db.set_state(self.OWNER_NAME_KEY, name)
                    break
        # Memory search has to know that "I" in a question is this person (core/embedder.py)
        if name and hasattr(self.embedder, "owner") and self.embedder.owner != name:
            self.embedder.owner = name
        return name

    def subject(self) -> str:
        """How memories refer to the owner: by name once it's known (a personal assistant uses it)."""
        return self.owner_name() or "the user"
