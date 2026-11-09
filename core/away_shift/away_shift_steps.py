"""Split out of core/away_shift.py §9 on 2026-11-03; core/away_shift/__init__.py re-exports these names.
The steps: followed channels, interests, weekly patterns, curious questions.
"""
import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .away_shift_constants import MAX_INTEREST_SEARCHES
    from .away_shift_dates import (
        CURIOSITY_POOL_PROMPT,
        FINDING_MAX_AGE,
        INTEREST_EVAL_PROMPT,
        INTERESTS_PROMPT,
        NOT_AN_INTEREST,
        PATTERNS_PROMPT,
        STATE_SEEN_FINDINGS,
        parse_published,
    )
except ImportError:
    from core.away_shift.away_shift_constants import MAX_INTEREST_SEARCHES
    from core.away_shift.away_shift_dates import (
        CURIOSITY_POOL_PROMPT,
        FINDING_MAX_AGE,
        INTEREST_EVAL_PROMPT,
        INTERESTS_PROMPT,
        NOT_AN_INTEREST,
        PATTERNS_PROMPT,
        STATE_SEEN_FINDINGS,
        parse_published,
    )

logger = logging.getLogger("AlwaysOnAgent.AwayShift")


class _StepsMixin:
    # Host-class state set in AwayShift.__init__ (core/away_shift/__init__.py:172),
    # plus methods defined on earlier mixins in the MRO (Budget, Dedup, Session).
    # Declared under TYPE_CHECKING only: _StepsMixin is last in the MRO, so a
    # runtime stub would not shadow anything, but TYPE_CHECKING is always safe.
    # DatabaseManager is imported only under TYPE_CHECKING, so its annotation
    # must stay quoted or the import breaks at runtime.
    db: "DatabaseManager"
    memory: Any

    if TYPE_CHECKING:
        def search_budget_remaining(self, now: datetime.datetime | None = None) -> int: ...
        def capped_search(self, query: str, limit: int = 4, now: datetime.datetime | None = None,
                          recent: bool = False) -> list[dict[str, Any]]: ...
        def _published(self, result: dict[str, Any], cur: datetime.datetime) -> datetime.datetime | None: ...
        def _covered_subjects(self, cur: datetime.datetime, limit: int = 40) -> list[str]: ...
        @staticmethod
        def _link_key(url: str) -> str: ...
        def _seen_findings(self) -> list[str]: ...
        def _ask_json(self, system: str, user: str, max_tokens: int = 600) -> dict[str, Any] | None: ...

    def _step_creators(self, cur: datetime.datetime, pool: Any) -> int:
        """New uploads on followed YouTube channels, from their feeds (core/feeds.py): no search, no AI call."""
        try:
            from core import feeds
        except ImportError:
            import feeds  # type: ignore[no-redef]
        return feeds.to_pool(self.db, pool, cur)

    def _step_interests(self, cur: datetime.datetime, pool: Any) -> int:
        """Searches for updates on things Alex follows and adds fresh findings to the pool. A finding is new by its
        link, not its topic: until 2026-10-27 "repeat of 'Upcoming AI Hackathons'" threw away a new hackathon, while
        the same Oct 15 video, under the same topic, was searched and dropped 12 times (B3)."""
        facts, pro = self._interest_facts()
        if not facts:
            return 0
        queries = self._interest_queries(facts)
        if not isinstance(queries, list):
            return 0
        seen = self._seen_findings()
        added = 0
        for item in queries[:MAX_INTEREST_SEARCHES]:
            if self.search_budget_remaining(cur) <= 0:
                break
            added += self._process_interest_item(item, cur, pool, pro, seen)
        self.db.set_state(STATE_SEEN_FINDINGS, json.dumps(seen[-400:]))
        return added

    def _step_patterns(self, cur: datetime.datetime, pool: Any) -> int:
        """Reflects on weekly activity patterns to form curious, helpful questions."""
        since = (cur - datetime.timedelta(days=7)).isoformat()
        messages = [r["content"] for r in self.db.get_connection().execute(
            "SELECT content FROM mem_messages WHERE role = 'user' AND created_at >= ? ORDER BY id DESC LIMIT 40",
            (since,)).fetchall()]
        if len(messages) < 6:
            return 0
        ans = self._ask_json(PATTERNS_PROMPT, f"Alex's recent messages this week:\n{json.dumps(messages, ensure_ascii=False)}")
        q = (ans or {}).get("question")
        if q and str(q).strip():
            subject = str((ans or {}).get("subject") or "weekly pattern").strip()[:100]
            why = str((ans or {}).get("why") or "noticed a pattern across this week's work").strip()[:150]
            pool.add("pattern", "question", q.strip()[:400], topic=subject, why=why, urgency="whenever")
            return 1
        return 0

    def _step_curiosity(self, cur: datetime.datetime, pool: Any) -> int:
        """Gathers curious questions grounded in facts and messages to populate the pool."""
        rows = list(self.memory._fact_rows()) if self.memory else []
        # Half the most important facts, half the newest: the importance order alone put the same old facts first
        # every time (the bear, the sinkhole), and newer everyday ones rarely made the cut (2026-10-27, B2/B6)
        newest = sorted(rows, key=lambda r: r["updated_at"] or "", reverse=True)[:15]
        picked = rows[:15] + [r for r in newest if r not in rows[:15]]
        facts = [r["content"] for r in picked][:30]
        recent = [r["content"] for r in self.db.get_connection().execute(
            "SELECT content FROM mem_messages WHERE role = 'user' ORDER BY id DESC LIMIT 15").fetchall()]
        if not facts and not recent:
            return 0
        covered = self._covered_subjects(cur)
        openings = [" ".join(str(r[0]).split()[:4]) for r in self.db.get_connection().execute(
            "SELECT text FROM proactive_messages WHERE kind = 'question' ORDER BY id DESC LIMIT 5").fetchall()]
        ans = self._ask_json(
            CURIOSITY_POOL_PROMPT,
            f"Facts:\n{json.dumps(facts, ensure_ascii=False)}\n\nRecent messages:\n{json.dumps(recent, ensure_ascii=False)}"
            f"\n\nAlready asked or tried lately (don't suggest these subjects or anything close to them):\n"
            f"{json.dumps(covered, ensure_ascii=False)}"
            f"\n\nRecent questions began like this; start differently:\n{json.dumps(openings, ensure_ascii=False)}"
        )
        candidates = (ans or {}).get("candidates", [])
        if not isinstance(candidates, list):
            return 0
        added = 0
        known_text = " ".join(facts + recent).lower()
        for c in candidates[:5]:
            q = c.get("question")
            about = str(c.get("about") or "").strip()
            if not q or not about or about.lower() not in known_text:
                continue
            subject = str(c.get("subject") or "curious thought").strip()[:100]
            why = str(c.get("why") or f"you mentioned: “{about[:80]}”").strip()[:150]
            pool.add("curiosity", "question", q.strip()[:400], about=about, topic=subject, why=why, urgency="whenever")
            added += 1
        return added

    def _interest_facts(self):
        """Memory preferences/projects plus curated interests and Today recipe rules; returns (facts, pro)."""
        facts = [r["content"] for r in (self.memory._fact_rows() if self.memory else [])
                 if r["category"] in ("preference", "project") and not NOT_AN_INTEREST.search(r["content"] or "")]
        pro = None
        try:
            from core.proactive import Proactive
            pro = Proactive(self.db)
            curated = [f"Follows interest: {i}" for i in pro.interests()]
            # What Alex asked for more of in his Today recipe (core/today/): searched like a followed interest
            try:
                from core.today import recipe
            except ImportError:
                from today import recipe  # type: ignore[no-redef]
            curated += [f"Follows interest (Today recipe): {t}" for t in recipe(self.db)["rules"]["more"]]
            facts = curated + facts
        except Exception as e:
            logger.warning(f"away shift: followed-interest facts unavailable: {e}")
        return facts, pro

    def _interest_queries(self, facts):
        """Asks the model for interest queries; returns the query list (possibly not a list)."""
        try:
            from core import feeds
        except ImportError:
            import feeds  # type: ignore[no-redef]
        ans = self._ask_json(INTERESTS_PROMPT, f"Owner's preferences and projects:\n{json.dumps(facts[:25], ensure_ascii=False)}"
                                               f"\n\nYouTube channels followed by feed (skip): {', '.join(feeds.channels(self.db))}")
        return (ans or {}).get("queries", [])

    def _interest_when(self, results, dated, link, cur):
        """The publish date of the chosen link, from the results or a fresh parse."""
        at = next((i for i, r in enumerate(results)
                   if r.get("url") and self._link_key(r.get("url")) == self._link_key(link)), None)
        return (dated[at] if at is not None else None) or self._published(
            results[at] if at is not None else {"url": link}, cur)

    def _process_interest_item(self, item, cur: datetime.datetime, pool: Any, pro, seen) -> int:
        """Checks one interest query and adds a fresh finding to the pool; returns 1 if added, else 0."""
        query = item.get("query")
        if not query or len(query) < 4:
            return 0
        topic = str(item.get("topic") or "")
        if NOT_AN_INTEREST.search(query) or NOT_AN_INTEREST.search(topic):
            return 0
        if pro is not None and (pro.is_excluded(query) or pro.is_excluded(topic)):
            return 0
        results = self.capped_search(query, limit=3, now=cur, recent=True)
        if not results:
            return 0
        keys = [self._link_key(r.get("url") or "") for r in results]
        if all(k and k in seen for k in keys):
            return 0                                  # nothing it hasn't looked at already: no AI call
        dated = [parse_published(r.get("published") or "", cur) for r in results]
        eval_ans = self._ask_json(
            INTEREST_EVAL_PROMPT,
            f"Today: {cur:%Y-%m-%d}\nTopic: {item.get('topic')}\nQuery: {query}\nResults:\n"
            + json.dumps([{"title": r.get("title"), "snippet": r.get("snippet"), "url": r.get("url"),
                           "published": f"{d:%Y-%m-%d}" if d else "unknown"}
                          for r, d in zip(results, dated)], ensure_ascii=False)
        )
        if eval_ans is None:
            return 0          # no answer (rate limit, outage): not marked seen, so it's looked at again (audit 0.10)
        seen.extend(k for k in keys if k and k not in seen)
        news = (eval_ans or {}).get("news")
        if news and str(news).strip():
            subject = str((eval_ans or {}).get("subject") or item.get("topic") or "interest update").strip()[:100]
            if pro is not None and (pro.is_excluded(news) or pro.is_excluded(subject)):
                return 0
            # The link the AI names counts only if it's one of the results; otherwise the first result's. A
            # made-up or mistyped link became a Today card and was fetched (2026-10-28 audit 0.5)
            named = str((eval_ans or {}).get("url") or "")
            link = named if named and self._link_key(named) in keys else str(results[0].get("url") or "")
            # Checked in code, not left to the AI: an article older than FINDING_MAX_AGE is not news (Oct 29)
            when = self._interest_when(results, dated, link, cur)
            if when is not None and cur - when > FINDING_MAX_AGE:
                logger.info(f"AwayShift: finding dropped, published {when:%Y-%m-%d} ({(cur - when).days} days "
                            f"old): {link}")
                return 0
            why = str((eval_ans or {}).get("why") or f"checked {item.get('topic', 'interest')} while you were away").strip()[:150]
            pool.add("interest", "notice", news.strip()[:400], topic=subject, why=why, urgency="whenever",
                     ref=f"link:{self._link_key(link)}"[:200] if link else "", url=link)
            return 1
        return 0
