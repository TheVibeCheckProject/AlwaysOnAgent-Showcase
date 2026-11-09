"""
Second opinion and forgetting everywhere for the AlwaysOnAgent assistant.

Split out of core/memory.py §9 on 2026-11-03; MemoryManager inherits
these methods via _SecondOpinionMixin, so they are still bound methods on the manager.
"""


import logging
import re
import sqlite3
import threading
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    import numpy as np

    from core.database import DatabaseManager
    from core.procedures import Procedures

try:
    from core.config import redact_secrets
    from core.llm_usage import usage_purpose

    from .memory_text import _parse_json_object, count_tokens
except ImportError:
    from config import redact_secrets  # type: ignore[no-redef]
    from llm_usage import usage_purpose  # type: ignore[no-redef]

    from .memory_text import _parse_json_object, count_tokens


class _SecondOpinionMixin:
    # Host state / methods supplied by MemoryManager.__init__ or other mixins
    # (see core/memory/manager.py). Annotation-only attributes declare the type
    # without creating a runtime value; methods live in TYPE_CHECKING so a stub
    # can never shadow a sibling mixin's real method at runtime (MRO rule).
    db: "DatabaseManager"
    llm: Any
    log: logging.Logger
    conv: str
    procedures: "Procedures"
    _cache_lock: threading.Lock
    _msg_cache: "dict[int, np.ndarray]"  # quoted: np is TYPE_CHECKING-only here
    VEC_MIN_SCRUB: float
    SCRUB_BATCH_CHARS: int

    if TYPE_CHECKING:
        def _embed(self, texts: list[str], query: bool = False, wait: bool = False) -> np.ndarray | None:
            raise NotImplementedError

        def _bg_chat(self, messages: list[dict[str, str]], purpose: str, cloud_only: bool = False,
                     **kw) -> str | None:
            raise NotImplementedError

        def search_facts(self, query: str, k: int = 8, touch: bool = False,
                         qvec: np.ndarray | None = None, min_keyword_sim: float | None = None,
                         at_timestamp: str | None = None) -> list[dict[str, Any]]:
            raise NotImplementedError

        def search_archive(self, query: str, k: int = 4, compressed_only: bool = True,
                           qvec: np.ndarray | None = None, min_sim: float | None = None,
                           min_keyword_sim: float | None = None) -> list[dict[str, Any]]:
            raise NotImplementedError

        def forget_fact(self, fact_id: int, scrub: bool = False) -> bool:
            raise NotImplementedError

        def rebuild_profile(self) -> bool:
            raise NotImplementedError

    VERIFY_MODELS = ("groq:openai/gpt-oss-120b", "cloudflare:@cf/openai/gpt-oss-120b", "mistral:ministral-14b-latest",
                     "groq:qwen/qwen3.8-27b")
    VERIFY_PROMPT = (
        "You double-check a personal assistant's long-term memory before it permanently deletes something. "
        "Another AI proposed the change below. Agree only if the text given clearly supports it; if in doubt, "
        "disagree: keeping a memory costs little, losing one can't be undone.\n"
        'Respond with JSON only: {"agree": true or false, "reason": "one short sentence"}'
    )

    def second_opinion(self, question: str) -> tuple[bool | None, str]:
        """(True/False, reason) from the checking AI; (None, "") when none answered or the answer was unreadable."""
        if not self.llm:
            return None, ""
        kw = {"models": self.VERIFY_MODELS} if getattr(self.llm, "BACKGROUND_MODELS", None) else {}
        try:
            with usage_purpose("verify"):
                out = self.llm.chat([{"role": "system", "content": self.VERIFY_PROMPT},
                                     {"role": "user", "content": question}],
                                    max_tokens=400, temperature=0.0, json_mode=True, timeout=30, **kw)
        except Exception as e:
            self.log.warning(f"Memory: the second opinion failed: {e}")
            return None, ""
        ans = _parse_json_object(out)
        if not isinstance(ans, dict) or not isinstance(ans.get("agree"), bool):
            return None, ""
        return ans["agree"], " ".join(str(ans.get("reason") or "").split())[:200]

    @staticmethod
    def verify_question(kind: str, fact: str, context: str) -> str:
        """kind: 'delete' (context: the owner's message), 'forget' (context: what the owner asked to forget) or
        'merge' (context: the fact that is kept)."""
        if kind == "delete":
            return (f"The user just said:\n\"{context[:1500]}\"\n\nSaved memory: \"{fact}\"\n\nProposed: delete this "
                    "memory, because the user's message shows it is outdated or no longer true. Does it show that?")
        if kind == "forget":
            return (f"The user asked their assistant to forget something. Their words:\n\"{context[:1500]}\"\n\n"
                    f"Saved memory: \"{fact}\"\n\nProposed: erase this memory everywhere, as part of what they asked "
                    "to forget. Is it part of what they asked to forget?")
        return (f"Saved memory A: \"{context}\"\nSaved memory B: \"{fact}\"\n\nProposed: delete B because it says the "
                "same thing as A, so nothing would be lost. Do they say the same thing?")

    def _confirmed(self, kind: str, fact: str, context: str) -> bool:
        """Whether a delete the memory proposed goes ahead. A held delete or forget is logged, and the owner is
        told when the checking AI disagreed, or couldn't check a forget they asked for."""
        agree, reason = self.second_opinion(self.verify_question(kind, fact, context))
        if agree:
            return True
        self.log.info(f"Memory: kept {fact!r} ({kind}): the second opinion "
                      + (f"disagreed: {reason}" if agree is False else "couldn't be had"))
        note = None
        if kind == "forget":
            note = (f"You asked me to forget something (\"{context[:100]}\"). "
                    + ("A second check wasn't sure this memory was part of it" if agree is False else
                       "I couldn't double-check that this memory was part of it")
                    + f", so I kept it: \"{fact}\". If it should go too, delete it in Memory.")
        elif kind == "delete" and agree is False:
            note = ("I thought this memory was out of date after what you said, but a second check disagreed"
                    + (f" ({reason.rstrip('.')})" if reason else "") + f", so I kept it: \"{fact}\". "
                    "Delete it in Memory if it's wrong.")
        # NOTIFY is defined in core/memory/manager.py (the live agent sets it on the core.memory package);
        # read it lazily at call time so a value set after import is seen, and so
        # this mixin never imports core.memory at module level (circular).
        try:
            from core.memory import NOTIFY
        except ImportError:
            from memory import NOTIFY  # type: ignore[no-redef]
        if note and NOTIFY is not None:
            try:
                NOTIFY(note)
            except Exception as e:
                self.log.warning(f"Couldn't tell the owner about a kept memory: {e}")
        return False

    SCRUB_PROMPT = (
        "The user asked their assistant to forget this, everywhere: \"{fact}\"\n\n"
        "Below are stored conversation messages (M<id>) and summaries (S<id>). For each one that "
        "contains that information, rewrite it with the information removed and everything else "
        "kept as it was. If nothing meaningful would remain, use an empty string. Leave out items "
        "that don't contain it. Saved facts (F<id>), if any, are deleted whole: list the ones that state "
        "that information under \"forget\". Things the assistant wrote from its memory (C<id> ideas, P<id> messages "
        "it sent, O<id> messages waiting, T<id> Today cards) are deleted whole too: list the ones that contain it "
        "under \"remove\".\n"
        'Respond with JSON only: {{"rewrite": [{{"ref": "M12", "content": str}}], "forget": ["F3"], '
        '"remove": ["P7"]}}'
    )
    SCRUB_MESSAGES = 60          # archived messages shown (those above VEC_MIN_SCRUB); 15 until audit 4.1
    # Rows written from memory, by label: (table, text column(s), how many of the newest are looked at)
    DERIVED: ClassVar[dict[str, tuple[str, str, int]]] = {"C": ("proactive_candidates", "text || ' ' || COALESCE(topic, '')", 400),
               "P": ("proactive_messages", "text", 300),
               "O": ("proactive_outbox", "text", 100),
               "T": ("today_items", "COALESCE(title, '') || ' ' || COALESCE(body, '')", 200)}

    def _derived_rows(self, forgotten: str) -> list[tuple[str, int, str]]:
        """(label, id, text) of rows written from memory that share a word with what's being forgotten: the proactive
        tables and Today's cards kept it after a forget (2026-10-28 audit 4.1). The scrub call decides."""
        words = {w for w in re.findall(r"[a-z0-9]+", forgotten.lower()) if len(w) > 3}
        if not words:
            return []
        out, conn = [], self.db.get_connection()
        for label, (table, text_sql, newest) in self.DERIVED.items():
            try:
                rows = conn.execute(f"SELECT id, {text_sql} AS t FROM {table} ORDER BY id DESC LIMIT ?",
                                    (newest,)).fetchall()
            except sqlite3.OperationalError:
                continue                            # that table isn't made yet
            out += [(label, r["id"], r["t"]) for r in rows if words & set(re.findall(r"[a-z0-9]+", (r["t"] or "").lower()))]
        return out

    def forget_everywhere(self, description: str) -> dict[str, int]:
        """The chat's forget_memory tool: erases a piece of information the user described, wherever it is
        stored. Unlike forget_fact it needs no fact id, so it reaches things that were only ever in
        summaries or messages (2026-10-04: "the DeskAI code lives in AI Studio" was in three summaries, no
        fact, and the assistant said it was forgotten when nothing had been)."""
        facts = self.search_facts(description, k=8)
        out = self.scrub(description, facts=facts)
        out["procedures"] = 0
        # Recipes close to it: the checking AI picks which are part of it (a "no" is normal here, so no message)
        for r in self.procedures.relevant(description, k=8, budget=10_000):
            agree, _ = self.second_opinion(self.verify_question("forget", self.procedures.text_of(r), description))
            if agree and self.procedures.delete(r["id"]):
                out["procedures"] += 1
        return out

    def _scrub_safe(self, forgotten: str) -> None:
        try:
            n = self.scrub(forgotten)
            self.log.info(f"Forgot a memory everywhere ({n} stored item(s) rewritten).")
        except Exception as e:
            self.log.warning(f"Scrubbing a forgotten memory failed: {e}")

    def _scrub_items(self, forgotten: str, facts: list[dict[str, Any]] | None):
        """Items the scrub call sees, plus the originals and the facts/derived ids it may act on."""
        conn = self.db.get_connection()
        # Every summary and the closest messages: the newest 40 summaries and the top 15 messages left older copies
        # behind while the tool said it was gone (audit 4.1)
        msgs = self.search_archive(forgotten, k=self.SCRUB_MESSAGES, compressed_only=False, min_sim=self.VEC_MIN_SCRUB)
        sums = conn.execute(
            "SELECT id, content FROM mem_summaries WHERE conv = ? ORDER BY id DESC", (self.conv,)
        ).fetchall()
        derived = self._derived_rows(forgotten)
        # Whole messages: a rewrite replaces the stored text, so a shortened copy would lose the rest of it
        items = ([f"F{f['id']}: {f['content']}" for f in facts or []]
                 + [f"M{m['id']}: {m['content']}" for m in msgs] + [f"S{s['id']}: {s['content']}" for s in sums]
                 + [f"{label}{rid}: {text}" for label, rid, text in derived])
        original = {("M", m["id"]): m["content"] for m in msgs}
        original.update({("S", s["id"]): s["content"] for s in sums})
        valid_f = {f["id"]: f["content"] for f in facts or []}
        derived_ids = {(label, rid) for label, rid, _ in derived}
        return items, original, valid_f, derived_ids

    def _scrub_batches(self, items: list[str]) -> list[list[str]]:
        # In batches: the answer repeats every rewritten item in full, and one call for everything ran into
        # its output limit, cut the JSON short and changed nothing (2026-10-04, 1,493 of 1,500 tokens)
        batches: list[list[str]] = []
        for item in items:
            if batches and sum(map(len, batches[-1])) + len(item) <= self.SCRUB_BATCH_CHARS:
                batches[-1].append(item)
            else:
                batches.append([item])
        return batches

    def _apply_scrub_rewrites(self, conn, ops: dict[str, Any], forgotten: str,
                              original: dict[tuple[str, int], str], removed: str) -> int:
        """Saves the model's rewrites; returns how many items were rewritten."""
        rewritten = 0
        for op in ops.get("rewrite") or []:
            try:
                ref = str(op.get("ref", ""))
                # The model sometimes repeats the item's label ("S7: ...") inside the rewrite
                new = re.sub(r"^[FMSCPOT]\d+:\s*", "", str(op.get("content") or "").strip()) or removed
                kind, rid = ref[:1].upper(), int(ref[1:])
            except (ValueError, AttributeError, TypeError):
                continue
            new = redact_secrets(new)
            old = original.get((kind, rid))
            if old is None:
                continue
            if new != removed and (reason := self._bad_rewrite(old, new, forgotten)):
                self.log.warning(f"Memory: kept {kind}{rid} as it was; the rewrite {reason}.")
                continue
            with conn:
                if kind == "M":
                    conn.execute("UPDATE mem_messages SET content = ?, tokens = ?, embedding = NULL, embedded_at = NULL "
                                 "WHERE id = ?", (new, count_tokens(new), rid))
                    with self._cache_lock:
                        self._msg_cache.pop(rid, None)
                    rewritten += 1
                elif kind == "S":
                    conn.execute("UPDATE mem_summaries SET content = ?, tokens = ?, embedding = NULL WHERE id = ?",
                                 (new, count_tokens(new), rid))
                    rewritten += 1
        return rewritten

    def _apply_scrub_forgets(self, ops: dict[str, Any], valid_f: dict[int, str], forgotten: str) -> int:
        """Deletes the facts the model listed; returns how many were forgotten."""
        facts_forgotten = 0
        for ref in ops.get("forget") or []:
            try:
                fid = int(str(ref).upper().lstrip("F"))
            except ValueError:
                continue
            if fid in valid_f and self._confirmed("forget", valid_f[fid], forgotten) \
                    and self.forget_fact(fid):              # no scrub: this call is the scrub
                facts_forgotten += 1
            valid_f.pop(fid, None)                          # listed again in a later batch: asked once
        return facts_forgotten

    def _apply_scrub_removes(self, conn, ops: dict[str, Any], derived_ids) -> int:
        """Deletes the derived rows the model listed; returns how many were removed."""
        removed_derived = 0
        for ref in ops.get("remove") or []:
            label, num = str(ref).strip().upper()[:1], str(ref).strip()[1:]
            if not num.isdigit() or (label, int(num)) not in derived_ids:
                continue
            with conn:
                conn.execute(f"DELETE FROM {self.DERIVED[label][0]} WHERE id = ?", (int(num),))
            derived_ids.discard((label, int(num)))
            removed_derived += 1
        return removed_derived

    def scrub(self, forgotten: str, facts: list[dict[str, Any]] | None = None) -> Any:
        """Erases a forgotten piece of information from the archive and summaries; returns items rewritten.
        Given candidate facts, it may delete those too, and returns {"rewritten": n, "facts": n}."""
        if not self.llm:
            return {"rewritten": 0, "facts": 0} if facts is not None else 0
        conn = self.db.get_connection()
        items, original, valid_f, derived_ids = self._scrub_items(forgotten, facts)
        batches = self._scrub_batches(items)
        removed = "[removed at the user's request]"
        rewritten = facts_forgotten = removed_derived = 0
        for batch in batches:
            text = "\n\n".join(batch)
            out = self._bg_chat([
                {"role": "system", "content": self.SCRUB_PROMPT.format(fact=forgotten)},
                {"role": "user", "content": text}
            ], "scrub", max_tokens=max(1500, len(text) // 2), temperature=0.0, json_mode=True, timeout=60)
            ops = _parse_json_object(out) or {}
            rewritten += self._apply_scrub_rewrites(conn, ops, forgotten, original, removed)
            facts_forgotten += self._apply_scrub_forgets(ops, valid_f, forgotten)
            removed_derived += self._apply_scrub_removes(conn, ops, derived_ids)
        self.rebuild_profile()      # the profile may quote it; don't wait for idle-time upkeep
        if facts is None:
            return rewritten
        return {"rewritten": rewritten, "facts": facts_forgotten, "derived": removed_derived}

    def _bad_rewrite(self, old: str, new: str, forgotten: str) -> str | None:
        """Why an erase rewrite mustn't be saved (None if it's fine). Erasing only removes, so a rewrite that is
        unchanged, longer, or no further in meaning from the forgotten thing is a model mistake, and the
        original is kept rather than damaged (docs/IDEAS.md #3)."""
        if new.strip() == old.strip():
            return "changed nothing"
        if len(new) > len(old):
            return "was longer than the original"
        vecs = self._embed([forgotten, old, new], wait=True)
        if vecs is not None and float(vecs[2] @ vecs[0]) >= float(vecs[1] @ vecs[0]):
            return "was no further from what was forgotten"
        return None
