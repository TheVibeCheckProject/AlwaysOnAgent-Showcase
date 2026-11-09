"""Checking a loop: gathering evidence, the AI's verdict, the quote check, the second opinion (core/open_loops.py §7).

Split out of core/open_loops.py on 2026-11-03; moved into core/open_loops/ on 2026-11-04. The methods are
unchanged apart from being dedented one level, and the names they use are imported from the sibling modules below.
"""

import datetime
import hashlib
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .open_loops_constants import SOURCE_NAMES, logger
from .open_loops_helpers import CHECK_PROMPT, VERIFY_SETTLED, _around, _json, _norm, _one_line, _parse_iso, _queries
from .open_loops_research import _excerpt

try:
    from core.database import get_iso_now
except ImportError:
    from database import get_iso_now  # type: ignore[no-redef]

class _CheckingMixin:
    # Host-class state set in OpenLoops.__init__ (core/open_loops/__init__.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime.
    memory: Any
    connectors: Any | None
    research: Callable[[str], list[dict[str, str]]]

    # _conn is defined on OpenLoops itself, which comes after every mixin in
    # the MRO, so it lives under TYPE_CHECKING only: a runtime stub would
    # shadow the real method.
    if TYPE_CHECKING:
        def _conn(self) -> Any: ...

    # research_due lives on _TimingMixin, which comes before this mixin in the
    # MRO, so this runtime stub never shadows the real method.
    def research_due(self, loop: dict[str, Any], fact: dict[str, Any],
                     now: datetime.datetime) -> bool:
        raise NotImplementedError

    def _evidence(self, fact: dict[str, Any], loop: dict[str, Any],
                  now: datetime.datetime | None = None,
                  include_research: bool = True) -> dict[str, dict[str, str]]:
        """{label: {"text", "url"?}} of everything that could show how it settled."""
        since = fact["created_at"]
        items: dict[str, dict[str, str]] = {}
        items.update(self._evidence_facts(fact, since))
        words = {w for w in _norm(f"{loop.get('waiting_on', '')} {fact['content']}").split() if len(w) > 3}
        items.update(self._evidence_messages(fact, loop, since, words))
        for n, m in enumerate(self._mail(loop, since), 1):
            items[f"E{n}"] = {"text": (f"[{m.get('date', '')}] from {m.get('from_name') or m.get('from', '')}: "
                                       f"{m.get('subject', '')}: {m.get('snippet', '')}")[:500]}
        cur_now = now or datetime.datetime.now().astimezone()
        if include_research and self.research_due(loop, fact, cur_now):
            self._evidence_research(fact, loop, cur_now, items)
        return items

    def _evidence_facts(self, fact: dict[str, Any], since: str) -> dict[str, dict[str, str]]:
        items: dict[str, dict[str, str]] = {}
        for r in self._conn().execute(
                "SELECT id, content, valid_from FROM mem_facts WHERE status = 'active' AND invalid_from IS NULL "
                "AND id != ? AND created_at > ? ORDER BY id DESC LIMIT 40", (fact["id"], since)).fetchall():
            items[f"F{r['id']}"] = {"text": f"[{str(r['valid_from'])[:10]}] {r['content']}"}
        return items

    def _evidence_messages(self, fact: dict[str, Any], loop: dict[str, Any], since: str,
                           words: set) -> dict[str, dict[str, str]]:
        items: dict[str, dict[str, str]] = {}
        asked = loop.get("asked_at")
        for r in self._conn().execute(
                "SELECT id, role, content, created_at FROM mem_messages WHERE role IN ('user', 'event', 'assistant') "
                "AND created_at > ? ORDER BY id DESC LIMIT 600", (since,)).fetchall():
            text = " ".join((r["content"] or "").split())
            after_question = bool(asked) and r["created_at"] >= asked
            # The assistant's own words count only as the question it asked; the owner's answer right after it is
            # kept even when it's just "yes, it came on the 5th"
            if r["role"] == "assistant" and not (after_question and "waiting on" in text.lower()):
                continue
            if not (after_question or words & set(_norm(text).split())):
                continue
            who = {"user": "owner", "event": "event", "assistant": "assistant asked"}[r["role"]]
            items[f"M{r['id']}"] = {"text": f"[{str(r['created_at'])[:10]} {who}] {text[:600]}"}
            if sum(k.startswith("M") for k in items) >= 30:
                break
        return items

    def _evidence_research(self, fact: dict[str, Any], loop: dict[str, Any],
                           cur_now: datetime.datetime, items: dict[str, dict[str, str]]) -> None:
        queries = _queries(loop.get("research"))
        terms = re.findall(r"[A-Za-z0-9][\w.:-]+", " ".join(queries) + " " + str(loop.get("waiting_on") or ""))
        seen, n = set(), 0
        for q in queries:
            try:
                results = self.research(q)
            except Exception as e:                  # offline, rate-limited: the rest still counts
                logger.info(f"open loops: research skipped ({type(e).__name__}: {e})")
                results = []
            for r in results[:5]:
                url = str(r.get("url") or "")
                if url in seen:
                    continue
                seen.add(url)
                n += 1
                items[f"W{n}"] = {"text": f"[search result] {r.get('title', '')}: {r.get('snippet', '')} ({url})"[:600],
                                  "url": url}
                if r.get("page"):
                    items[f"R{n}"] = {"text": f"[page {url}] " + _excerpt(r["page"], terms), "url": url}
        stamp = cur_now.isoformat()
        with self._conn() as conn:
            conn.execute("UPDATE mem_open_loops SET researched_at = ? WHERE fact_id = ?",
                         (stamp, fact["id"]))
        loop["researched_at"] = stamp

    def _mail(self, loop: dict[str, Any], since: str) -> list[dict[str, Any]]:
        if not (loop.get("mail") and self.connectors is not None):
            return []
        day = (_parse_iso(since) or datetime.datetime.now().astimezone()).strftime("%Y/%m/%d")
        try:
            return (self.connectors.search(f"{loop['mail']} after:{day}", limit=6) or {}).get("emails", [])[:6]
        except Exception as e:                  # no Gmail connected, quota, network: chat and facts still count
            logger.info(f"open loops: mail search skipped ({type(e).__name__}: {e})")
            return []

    def check(self, fact: dict[str, Any], loop: dict[str, Any], answered: bool = False,
              include_research: bool = True, now: datetime.datetime | None = None) -> dict[str, Any] | None:
        """{"kind": "settled", ...} when the evidence settles the loop (and the change is made), {"kind": "news",
        ...} when it shows something new happened without the outcome, else None."""
        cur_now = now or datetime.datetime.now().astimezone()
        self.check_failed = False               # True when no AI verdict could be had (read by _run)
        evidence = self._evidence(fact, loop, now=cur_now, include_research=include_research)
        key = hashlib.sha1("\n".join(f"{k}:{v['text']}" for k, v in sorted(evidence.items())).encode()).hexdigest()
        with self._conn() as conn:
            conn.execute("UPDATE mem_open_loops SET checked_at = ? WHERE fact_id = ?", (cur_now.isoformat(), fact["id"]))
        if not evidence or (key == loop.get("evidence_key") and not answered):
            return None                         # nothing new since the last look: no AI call
        ans = self._check_verdict(fact, loop, evidence)
        if ans is None:
            return None
        with self._conn() as conn:
            conn.execute("UPDATE mem_open_loops SET evidence_key = ? WHERE fact_id = ?", (key, fact["id"]))
        kind = ans.get("result")
        if kind not in ("settled", "news"):
            return None
        ref = str(ans.get("ref") or "").strip().upper()
        quote = _norm(str(ans.get("quote") or ""))
        content = _one_line(ans.get("content"), 400)
        if ref not in evidence or len(quote) < 6 or quote not in _norm(evidence[ref]["text"]):
            logger.info(f"open loops: #{fact['id']}: an answer whose quote isn't in {ref or 'the evidence'} was dropped")
            return None
        return self._check_outcome(fact, loop, ans, evidence, ref, quote, content, kind)

    def _check_verdict(self, fact: dict[str, Any], loop: dict[str, Any],
                       evidence: dict[str, dict[str, str]]) -> dict[str, Any] | None:
        out = self.memory._bg_chat(
            [{"role": "system", "content": CHECK_PROMPT},
             {"role": "user", "content": f"Saved memory (since {str(fact['created_at'])[:10]}): {fact['content']}\n"
                                         f"Waiting on: {loop.get('waiting_on') or ''}\n\nEvidence:\n"
                                         + "\n".join(f"{k}: {v['text']}" for k, v in evidence.items())}],
            "open_loops", max_tokens=500, temperature=0.0, json_mode=True, timeout=60)
        ans = _json(out)
        if not isinstance(ans, dict):
            # No verdict (every model failed or answered nonsense): the evidence key isn't saved, so the next run asks
            # again. Until 2026-10-31 the key was saved before this call, and the same evidence was then skipped for good
            self.check_failed = True
            logger.info(f"open loops: #{fact['id']}: no readable answer; checked again next run")
            return None
        return ans

    def _check_outcome(self, fact: dict[str, Any], loop: dict[str, Any], ans: dict[str, Any],
                       evidence: dict[str, dict[str, str]], ref: str, quote: str, content: str,
                       kind: str) -> dict[str, Any] | None:
        item = evidence[ref]
        where = SOURCE_NAMES[ref[0]]
        found = {"fact_id": fact["id"], "old": fact["content"], "where": where, "url": item.get("url") or "",
                 "waiting_on": loop.get("waiting_on") or fact["content"], "quote": _one_line(ans.get("quote"), 400)}
        if kind == "news":
            if len(content) < 10 or _norm(item["text"]) == _norm(loop.get("news") or ""):
                return None                     # told already
            logger.info(f"open loops: #{fact['id']}: news from {ref}")
            return {**found, "kind": "news", "content": content, "item": item["text"]}
        if ref.startswith("F"):
            content = item["text"].split("] ", 1)[-1]           # that memory's own words replace it
        elif len(content) < 10:
            return None
        # The whole item, not just the quote: shown only "approved on October 5", the checker said a newer memory's
        # permit number and address were "details not supported by the evidence" (dry run on the real memory)
        agree, reason = self.memory.second_opinion(VERIFY_SETTLED.format(
            fact=fact["content"], where=where, quote=_around(item["text"], found["quote"]), content=content))
        if not agree:
            logger.info(f"open loops: #{fact['id']} kept: the second opinion "
                        + (f"disagreed: {reason}" if agree is False else "couldn't be had"))
            if agree is None:
                # No second opinion (models down) isn't a "no": look at this evidence again next run
                self.check_failed = True
                with self._conn() as conn:
                    conn.execute("UPDATE mem_open_loops SET evidence_key = NULL WHERE fact_id = ?", (fact["id"],))
            return None
        new_id = self._check_settle(fact, ref, content, found)
        with self._conn() as conn:
            conn.execute("UPDATE mem_open_loops SET status = 'closed', closed_by = ?, note = ? WHERE fact_id = ?",
                         (new_id, f"settled by {where}", fact["id"]))
        logger.info(f"open loops: #{fact['id']} settled by {ref}; now #{new_id}")
        return {**found, "kind": "settled", "content": content, "replaced_by": new_id}

    def _check_settle(self, fact: dict[str, Any], ref: str, content: str,
                      found: dict[str, Any]) -> int:
        if ref.startswith("F"):
            # A newer memory already says it (the permit: "approved on Oct 5" saved beside five "waiting" ones)
            new_id = int(ref[1:])
            with self._conn() as conn:
                conn.execute("UPDATE mem_facts SET status = 'superseded', invalid_from = ?, updated_at = ?, "
                             "superseded_by = ? WHERE id = ? AND status = 'active'",
                             (get_iso_now(), get_iso_now(), new_id, fact["id"]))
            self.memory._mark_dirty()
        else:
            new_id, _ = self.memory.supersede_fact(
                fact["id"], content, source="loop",
                evidence=f"{found['quote'][:250]}" + (f" ({found['url']})" if found["url"] else ""),
                source_msg_id=int(ref[1:]) if ref.startswith("M") else None)
        return new_id
