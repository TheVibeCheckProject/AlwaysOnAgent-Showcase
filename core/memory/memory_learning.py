"""
Learning for the AlwaysOnAgent assistant.

Split out of core/memory.py §14 on 2026-11-03; MemoryManager inherits
these methods via _LearningMixin, so they are still bound methods on the manager.
"""


import datetime
import logging
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.procedures import Procedures

try:
    from .memory_text import _blob_to_vec, _is_date_statement, _parse_json_object
except ImportError:
    from .memory_text import _blob_to_vec, _is_date_statement, _parse_json_object


class _LearningMixin:
    # --- Host state / constants supplied by MemoryManager and earlier mixins ---
    # (declared here so mypy sees them; the real values live on _SetupMixin /
    # MemoryManager.__init__ and on _FactsMixin/_SearchMixin/_SecondOpinionMixin,
    # all earlier in the MRO, so the runtime stubs below never shadow anything.)
    db: "DatabaseManager"
    conv: str
    log: logging.Logger
    llm: Any
    procedures: "Procedures"
    EXTRACT_ALL_FACTS_UPTO: int
    UPDATE_MIN_SIM: float

    def _fact_rows(self, where: str = "status = 'active' AND invalid_from IS NULL",
                  params: Sequence[Any] = ()) -> list[Any]:
        raise NotImplementedError

    def _fact_matrix(self) -> tuple[list[int], np.ndarray | None]:
        raise NotImplementedError

    def search_facts(self, query: str, k: int = 8, touch: bool = False,
                     qvec: np.ndarray | None = None,
                     min_keyword_sim: float | None = None,
                     at_timestamp: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _bg_chat(self, messages: list[dict[str, str]], purpose: str,
                 cloud_only: bool = False, **kw: Any) -> str | None:
        raise NotImplementedError

    def _confirmed(self, kind: str, fact: str, context: str) -> bool:
        raise NotImplementedError

    def forget_fact(self, fact_id: int, scrub: bool = False) -> bool:
        raise NotImplementedError

    def add_fact(self, content: str, category: str = "fact", importance: int = 3,
                 pinned: bool = False, source: str = "chat", evidence: str | None = None,
                 source_msg_id: int | None = None) -> tuple[int, bool]:
        raise NotImplementedError

    def supersede_fact(self, fact_id: int, new_content: str, category: str | None = None,
                       importance: int | None = None, source: str = "chat",
                       evidence: str | None = None,
                       source_msg_id: int | None = None) -> tuple[int, bool]:
        raise NotImplementedError

    def subject(self) -> str:
        raise NotImplementedError

    if TYPE_CHECKING:
        # _embed lives on _EmbeddingsMixin, LATER in the MRO: a runtime stub here
        # would shadow the real method, so this declaration is typing-only.
        def _embed(self, texts: list[str], query: bool = False,
                   wait: bool = False) -> np.ndarray | None:
            ...

    EXTRACT_PROMPT = (
        "You maintain the long-term memory of a personal assistant. Read the latest exchange and decide "
        "what is worth remembering about the USER for weeks or months: who they are, preferences, "
        "projects, goals, plans and deadlines, decisions, people in their life, and standing "
        "instructions for the assistant.\n\n"
        "Only record what the USER actually stated or explicitly agreed to. Ideas, options, suggestions "
        "or guesses from the ASSISTANT are never memories unless the user clearly adopted them in this "
        "message (e.g. 'yes, let's do option C'). Do not infer personality traits, motives or "
        "preferences the user didn't express. Ignore small talk, questions, one-off requests, and "
        "temporary states ('currently looking into X', 'about to test Y'). Never record the current "
        "date, day or time itself: the assistant always knows it. Leave out counts, sizes and version numbers "
        "that keep changing ('a 574-test suite', 'build 84'): keep the lasting part ('runs the full test suite "
        "before every push'). Write each memory with the person's name, never 'the user'.\n"
        "Requests for the assistant to do something are not memories, even when phrased as 'I want…': "
        "scheduling ('post two TikTok videos a day', 'remind me at 9'), making or posting videos, looking "
        "something up, starting a task or project job. The assistant carries those out and its schedules "
        "and task list keep them; a copy in memory would only go stale. Save something from such a "
        "message only if it also says something lasting about the person themselves.\n\n"
        "Rules:\n"
        "- Every entry needs \"evidence\": the user's own words from this message, copied exactly (a "
        "short phrase is enough). Entries whose evidence isn't in the user's message are discarded.\n"
        "- When the exchange starts with the assistant's message the user was answering (often a question), use it "
        "only to know what a short answer refers to ('yes', 'last weekend', 'Springfield'): what the assistant asked "
        "or assumed is never a memory by itself, and the evidence is still the user's own words.\n"
        "- One fact per memory: a single standalone sentence about {subject}, by name (e.g. '{subject} "
        "prefers videos posted at 9am.'). Resolve relative dates to absolute ones (today's date is given below).\n"
        "- If new information changes or refines an existing memory, UPDATE that memory (by id) "
        "instead of adding a new one.\n"
        "- If the user asks you to forget something or not to store it, put its id in FORGET "
        "(it will be erased everywhere, including conversation history).\n"
        "- If a memory is simply outdated or now false, put its id in DELETE.\n"
        "- If the user explicitly asks you to remember something, add it with importance 5.\n"
        "- Plans stay plans ('{subject} plans to…') until the user says they happened; never record a planned or "
        "proposed action as done. Date events by when they happen, not when they were mentioned.\n"
        "- Say whose word it is: what someone else said or believes is theirs ('{subject}'s dad says…'), and "
        "{subject}'s own reading of events is recorded as theirs ('{subject} believes…'), not as established fact.\n"
        "- A correction can make several listed memories wrong: update or delete every one it affects.\n"
        "- One bad experience is not a standing rule unless the user makes it one.\n"
        "- Never store passwords, codes, card, account or ID numbers; at most where something is kept.\n"
        "- importance: 5 = core identity or explicit instruction, 4 = strong preference or active "
        "project, 3 = useful detail, 2 = minor, 1 = trivia.\n"
        "- categories: profile, preference, project, plan, person, instruction, fact.\n"
        "- Most exchanges contain nothing new: then return empty lists.\n"
        "- PROCEDURE, not a memory: when the user tells the assistant HOW to do a kind of job from now on (a "
        "standing rule for that job, steps to follow, something to always or never do while doing it, e.g. "
        "'when you write the TikTok caption, keep it under ten words', 'always check the forecast for the "
        "cabin, not home, when I mention fishing'), put it in PROCEDURE with: area (one or two words naming "
        "the job, e.g. 'tiktok videos', 'website', 'weather', 'email'), when (the situation), do (what to do, "
        "in the user's sense), evidence. A one-off request ('make a video now') is neither a memory nor a "
        "procedure.\n\n"
        "The message gives today's date, the existing memories (with ids) and the exchange.\n"
        "Respond with JSON only: "
        '{{"add": [{{"content": str, "category": str, "importance": int, "evidence": str}}], '
        '"update": [{{"id": int, "content": str, "evidence": str}}], '
        '"delete": [{{"id": int, "evidence": str}}], "forget": [{{"id": int, "evidence": str}}], '
        '"procedure": [{{"area": str, "when": str, "do": str, "evidence": str}}]}}'
    )

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))

    def _grounded(self, evidence: Any, user_text: str) -> bool:
        """True if the quoted evidence really appears in what the user said (ignoring case/punctuation)."""
        ev = self._norm(str(evidence or ""))
        return len(ev) >= 3 and ev in self._norm(user_text)

    def _extraction_candidates(self, text: str) -> list[dict[str, Any]]:
        """Existing facts the extractor may update or delete. Generous on purpose: a fact it isn't
        shown can't be updated or forgotten ("forget my dog" must reach "golden retriever named Pepper")."""
        active = self._fact_rows()
        if len(active) <= self.EXTRACT_ALL_FACTS_UPTO:
            return [dict(r) for r in active]
        chosen: dict[int, dict[str, Any] | None] = {f["id"]: f for f in self.search_facts(text, k=12)}
        q = self._embed([text], wait=False)
        ids, mat = self._fact_matrix()
        if q is not None and mat is not None:
            for i in np.argsort(-(mat @ q[0]))[:20]:
                chosen.setdefault(ids[i], None)
        missing = [i for i, f in chosen.items() if f is None]
        if missing:
            for r in self.db.get_connection().execute(
                    f"SELECT * FROM mem_facts WHERE id IN ({','.join('?' * len(missing))})", missing).fetchall():
                chosen[r["id"]] = dict(r)
        return [f for f in chosen.values() if f]

    ANSWER_WINDOW = datetime.timedelta(hours=12)    # an assistant message older than this isn't what a reply answers

    def _message_answered(self, msg_id: int | None) -> str:
        """The assistant's message just before the user's (a question it asked, often a proactive one), if recent:
        the extractor saw only "yeah, last weekend" or "Springfield" and had to guess what it answered (2026-10-28 audit
        7.2). Background events in between don't count. '' when there's none."""
        if not msg_id:
            return ""
        row = self.db.get_connection().execute(
            "SELECT role, content, created_at FROM mem_messages WHERE conv = ? AND id < ? AND role != 'event' "
            "ORDER BY id DESC LIMIT 1", (self.conv, int(msg_id))).fetchone()
        if not row or row["role"] != "assistant":
            return ""
        try:
            at = datetime.datetime.fromisoformat(row["created_at"])
            at = at if at.tzinfo else at.astimezone()
        except (TypeError, ValueError):
            return ""
        if datetime.datetime.now().astimezone() - at > self.ANSWER_WINDOW:
            return ""
        return " ".join((row["content"] or "").split())

    def _same_subject(self, fact_id: int, new_content: str) -> bool | None:
        """Whether new_content is about the same thing as fact #fact_id (None if it can't be judged)."""
        row = self.db.get_connection().execute("SELECT embedding FROM mem_facts WHERE id = ?", (fact_id,)).fetchone()
        new = self._embed([new_content], wait=True)
        if not row or row["embedding"] is None or new is None:
            return None
        return float(_blob_to_vec(row["embedding"]) @ new[0]) >= self.UPDATE_MIN_SIM

    # Messages that can't hold anything to remember: each cost a full extraction call (105 a week, many like
    # these; docs/IDEAS.md #5). Kept short on purpose: "yes" or "Springfield" may answer the assistant's question.
    NOTHING_TO_LEARN: ClassVar[set[str]] = {"thanks", "thank you", "thank you so much", "thanks so much", "ty", "ok", "okay", "k", "cool",
                        "nice", "great", "awesome", "lol", "haha", "of course", "got it", "sounds good", "hey", "hi",
                        "hello", "good morning", "good night", "np", "no problem", "you're welcome", "perfect"}

    @classmethod
    def _nothing_to_learn(cls, user_text: str) -> bool:
        words = re.findall(r"[a-z']+", (user_text or "").lower())
        return not words or " ".join(words) in cls.NOTHING_TO_LEARN

    def _grounded_op(self, op: Any, user_text: str) -> bool:
        """True if the op's quoted evidence really appears in what the user said."""
        return isinstance(op, dict) and self._grounded(op.get("evidence"), user_text)

    def _stale_date(self, op: Any) -> bool:
        """True if the op only states the current date/time (never stored)."""
        if _is_date_statement(str(op.get("content", ""))):
            self.log.info(f"Memory: ignored a statement of the current date/time: {op.get('content')!r}")
            return True
        return False

    def _run_extraction(self, messages: list[dict[str, str]]) -> dict[str, Any] | None:
        """One extraction call (redone in the cloud if the local model proposed a delete)."""
        out = self._bg_chat(messages, "extract", max_tokens=700, temperature=0.1, json_mode=True, timeout=40)
        ops = _parse_json_object(out)
        if isinstance(ops, dict) and (ops.get("delete") or ops.get("forget")) \
                and str(getattr(self, "_last_bg_route", "") or "").startswith("ollama:"):
            # Deletes are the cloud's call (IDEAS #10): redo this exchange there and apply only its answer
            self.log.info("Memory: the local model proposed a delete; asking the cloud instead.")
            ops = _parse_json_object(self._bg_chat(messages, "extract", cloud_only=True, max_tokens=700,
                                                   temperature=0.1, json_mode=True, timeout=40))
        return ops if isinstance(ops, dict) else None

    def _apply_forget_delete(self, ops: dict[str, Any], allowed, contents: dict[int, str],
                             user_text: str, stats: dict[str, int]) -> int:
        """Applies the forget/delete ops; returns how many were discarded as ungrounded."""
        rejected = 0
        for key, scrub in (("forget", True), ("delete", False)):
            for d in ops.get(key) or []:
                try:
                    fid = int(d["id"])
                    if not self._grounded_op(d, user_text):
                        rejected += 1
                        continue
                    if fid in allowed and self._confirmed(key, contents[fid], user_text) \
                            and self.forget_fact(fid, scrub=scrub):
                        stats["forgotten" if scrub else "deleted"] += 1
                except (TypeError, ValueError, KeyError):
                    continue
        return rejected

    def _apply_updates(self, ops: dict[str, Any], allowed, user_text: str, msg_id: int | None,
                       stats: dict[str, int]) -> int:
        """Applies the update ops; returns how many were discarded as ungrounded."""
        rejected = 0
        for u in ops.get("update") or []:
            try:
                if not self._grounded_op(u, user_text):
                    rejected += 1
                    continue
                if int(u.get("id")) not in allowed or self._stale_date(u):
                    continue
                fact_id, content = int(u["id"]), str(u.get("content", ""))
                if self._same_subject(fact_id, content) is False:
                    self.log.info(f"Memory: an update to fact #{fact_id} was about something else; saved it as a new fact.")
                    _, created = self.add_fact(content, "fact", 3, source="chat", evidence=u.get("evidence"),
                                               source_msg_id=msg_id)
                    stats["added"] += int(created)
                elif self.supersede_fact(fact_id, content, evidence=u.get("evidence"), source_msg_id=msg_id):
                    stats["updated"] += 1
            except (TypeError, ValueError, AttributeError):
                continue
        return rejected

    def _apply_adds(self, ops: dict[str, Any], user_text: str, msg_id: int | None,
                    stats: dict[str, int]) -> int:
        """Applies the add ops; returns how many were discarded as ungrounded."""
        rejected = 0
        for a in ops.get("add") or []:
            try:
                if not self._grounded_op(a, user_text):
                    rejected += 1
                    continue
                if self._stale_date(a):
                    continue
                _, created = self.add_fact(str(a.get("content", "")), str(a.get("category", "fact")),
                                           int(a.get("importance", 3)), source="chat",
                                           evidence=a.get("evidence"), source_msg_id=msg_id)
                stats["added"] += int(created)
            except (TypeError, ValueError, AttributeError):
                continue
        return rejected

    def _apply_procedures(self, ops: dict[str, Any], user_text: str, msg_id: int | None) -> int:
        """Applies the procedure ops; returns how many were discarded as ungrounded."""
        rejected = 0
        for p in ops.get("procedure") or []:
            try:
                if not self._grounded_op(p, user_text):
                    rejected += 1
                    continue
                # The owner's own how-to: no second opinion (their words are the evidence), never auto-retired
                self.procedures.add(str(p.get("area") or "general"), str(p.get("when") or ""), str(p.get("do") or ""),
                                    "owner", source_ref=msg_id, evidence=str(p.get("evidence") or ""))
            except (TypeError, ValueError, AttributeError):
                continue
        return rejected

    def learn_from_exchange(self, user_text: str, reply: str, msg_id: int | None = None) -> dict[str, int]:
        """One LLM call: extract new facts and update/delete/forget existing ones touched by this exchange."""
        stats = {"added": 0, "updated": 0, "deleted": 0, "forgotten": 0}
        if not self.llm or self._nothing_to_learn(user_text):
            return stats
        asked = self._message_answered(msg_id)
        related = self._extraction_candidates(user_text + "\n" + reply[:500] + ("\n" + asked[:300] if asked else ""))
        existing = "\n".join(f"[{f['id']}] {f['content']}" for f in related) or "(none)"
        # Instructions never change (cacheable); everything that does goes in the user message
        before = f"ASSISTANT, the message the user was answering: {asked[:800]}\n\n" if asked else ""
        messages = [
            {"role": "system", "content": self.EXTRACT_PROMPT.format(subject=self.subject())},
            {"role": "user", "content": f"Today is {datetime.date.today().isoformat()}.\n\nExisting memories:\n{existing}\n\n"
                                        f"Latest exchange:\n{before}USER: {user_text[:3000]}\n\nASSISTANT: {reply[:1500]}"}
        ]
        ops = self._run_extraction(messages)
        if ops is None:
            return stats
        allowed = {f["id"] for f in related}
        contents = {f["id"]: f["content"] for f in related}
        rejected = self._apply_forget_delete(ops, allowed, contents, user_text, stats)
        rejected += self._apply_updates(ops, allowed, user_text, msg_id, stats)
        rejected += self._apply_adds(ops, user_text, msg_id, stats)
        rejected += self._apply_procedures(ops, user_text, msg_id)
        if rejected:
            self.log.info(f"Memory: discarded {rejected} proposed change(s) not backed by the user's own words.")
        if any(stats.values()):
            self.log.info(f"Memory updated from conversation: {stats}")
        return stats
