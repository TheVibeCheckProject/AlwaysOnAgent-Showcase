"""Split out of core/away_shift.py §7 on 2026-11-03; core/away_shift/__init__.py re-exports these names.
A session: starting and stopping; run_session(): every step in order.
"""
import concurrent.futures
import datetime
import json
import logging
import re
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core.candidates import backfill_subjects
    from core.llm_usage import usage_purpose
except ImportError:
    from candidates import backfill_subjects  # type: ignore[no-redef]
    from llm_usage import usage_purpose  # type: ignore[no-redef]

try:
    from .away_shift_constants import (
        FULL_UPKEEP_EVERY,
        POOL_LOW_WATERMARK,
        STATE_LAST_FULL_UPKEEP,
        STATE_LAST_PATTERNS,
        STATE_LAST_RECORD,
        STATE_LAST_SESSION,
        STATE_LAST_SUMMARY,
    )
    from .away_shift_dates import INTEREST_EVERY, STATE_INTERESTS_AT, _parse_iso
except ImportError:
    from core.away_shift.away_shift_constants import (
        FULL_UPKEEP_EVERY,
        POOL_LOW_WATERMARK,
        STATE_LAST_FULL_UPKEEP,
        STATE_LAST_PATTERNS,
        STATE_LAST_RECORD,
        STATE_LAST_SESSION,
        STATE_LAST_SUMMARY,
    )
    from core.away_shift.away_shift_dates import INTEREST_EVERY, STATE_INTERESTS_AT, _parse_iso

logger = logging.getLogger("AlwaysOnAgent.AwayShift")


class _SessionMixin:
    # Host state set by AwayShift.__init__ (core/away_shift/__init__.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime. Types mirror AwayShift.__init__.
    db: "DatabaseManager"  # quoted: imported only under TYPE_CHECKING
    llm: Any
    memory: Any
    proactive: Any
    open_loops: Any
    clock: Callable[[], float]
    now_fn: Callable[[], datetime.datetime]
    executor: concurrent.futures.ThreadPoolExecutor
    _lock: threading.Lock
    _active_future: concurrent.futures.Future | None
    _session_start_time: float | None

    # Methods defined on EARLIER mixins in the MRO (Budget, Status): a runtime
    # stub here is safe — the real method is found first in the MRO.
    def _set_step(self, step: str, label: str) -> None:
        raise NotImplementedError

    def searches_today(self, now: datetime.datetime | None = None) -> int:
        raise NotImplementedError

    def search_budget_remaining(self, now: datetime.datetime | None = None) -> int:
        raise NotImplementedError

    def capped_search(self, query: str, limit: int = 4, now: datetime.datetime | None = None,
                      recent: bool = False) -> list[dict[str, Any]]:
        raise NotImplementedError

    if TYPE_CHECKING:
        # Methods defined on LATER mixins in the MRO (Dedup, Steps): declared
        # type-only — a runtime stub here would shadow the real method.
        def _generator_ready(self, step: str, source: str, cur: datetime.datetime) -> bool: ...
        def _mark_generated(self, step: str, source: str, cur: datetime.datetime) -> None: ...
        def _step_creators(self, cur: datetime.datetime, pool: Any) -> int: ...
        def _step_interests(self, cur: datetime.datetime, pool: Any) -> int: ...
        def _step_patterns(self, cur: datetime.datetime, pool: Any) -> int: ...
        def _step_curiosity(self, cur: datetime.datetime, pool: Any) -> int: ...

    def start(self, now: datetime.datetime | None = None) -> concurrent.futures.Future:
        """Starts an away session in its own executor thread."""
        with self._lock:
            if self._active_future is not None and not self._active_future.done():
                return self._active_future
            self._active_future = self.executor.submit(self.run_session, now)
            return self._active_future

    def shutdown(self, wait: bool = False) -> None:
        self.executor.shutdown(wait=wait)

    def _ask_json(self, system: str, user: str, max_tokens: int = 600) -> dict[str, Any] | None:
        if not self.llm:
            return None
        try:
            with usage_purpose("away_shift"):
                raw = self.llm.chat(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens=max_tokens, temperature=0.3, json_mode=True, timeout=50,
                    models=getattr(self.llm, "BACKGROUND_MODELS", None)
                )
            m = re.search(r"[\{\[].*[\}\]]", raw or "", re.DOTALL)
            return json.loads(m.group(0)) if m else None
        except Exception as e:
            logger.debug(f"away shift AI call failed: {e}")
            return None

    # ------------------------------------------------------------------------------------------- session logic

    def run_session(self, now: datetime.datetime | None = None) -> dict[str, Any]:
        """Runs one full away shift pass across memory, open loops, interests, patterns, and research."""
        cur = now or self.now_fn()
        with self._lock:
            self._session_start_time = self.clock()
        self.db.set_state(STATE_LAST_SESSION, cur.isoformat())
        try:
            from core.away_shift import publish_activity
        except ImportError:
            from away_shift import publish_activity  # type: ignore[no-redef]
        publish_activity("started", label="Researching in the background", step="starting")
        found_total = 0
        pool = getattr(self.proactive, "pool", None) if self.proactive else None
        steps_taken: list[dict[str, Any]] = []
        self._backfill_subjects(pool)
        found_total += self._run_memory_upkeep(cur, steps_taken)
        found_total += self._run_open_loops(cur, steps_taken)
        pending_count = len(pool.pending()) if pool else 0
        if pool:
            found = self._run_creators(cur, pool, steps_taken)
            found_total += found
            pending_count += found
        found = self._run_interests(cur, pool, pending_count, steps_taken)
        found_total += found
        pending_count += found
        found = self._run_patterns(cur, pool, pending_count, steps_taken)
        found_total += found
        pending_count += found
        found_total += self._run_curiosity(cur, pool, pending_count, steps_taken)
        step, found = self._run_research_worker(cur)
        if step is not None:
            found_total += found
            steps_taken.append(step)
        return self._finish_session(cur, now, found_total, steps_taken)

    def _backfill_subjects(self, pool) -> None:
        """Backfills candidate subjects when the pool and LLM are available."""
        if pool and self.llm:
            try:
                def bg_chat(msgs):
                    with usage_purpose("away_shift"):
                        return self.llm.chat(msgs, max_tokens=600, temperature=0.1, json_mode=True, timeout=40,
                                             models=getattr(self.llm, "BACKGROUND_MODELS", None))
                backfill_subjects(self.db, bg_chat)
            except Exception as e:
                logger.debug(f"subjects backfill: {e}")

    def _run_memory_upkeep(self, cur: datetime.datetime, steps_taken: list) -> int:
        """Consolidates long-term memory; returns findings added (always 0)."""
        self._set_step("memory_upkeep", "Consolidating long-term memory")
        mem_outcome = "skipped"
        if self.memory:
            try:
                # Once a week every fact is compared, not just the ones changed since the last pass: older duplicates
                # (the house project twice, a relative twice) were otherwise never compared again (B7.3). Merges still
                # need the second opinion's agreement.
                last_full = _parse_iso(self.db.get_state(STATE_LAST_FULL_UPKEEP, ""))
                full = last_full is None or cur - last_full >= FULL_UPKEEP_EVERY
                stats = self.memory.consolidate(force=full)
                if full:
                    self.db.set_state(STATE_LAST_FULL_UPKEEP, cur.isoformat())
                mem_outcome = f"consolidated ({stats})" if any(stats.values()) else "checked (clean)"
                if any(stats.values()):
                    logger.info(f"AwayShift memory upkeep: {stats}")
            except Exception as e:
                mem_outcome = f"failed: {e}"
                logger.warning(f"AwayShift memory upkeep failed: {e}")
        steps_taken.append({"step": "memory_upkeep", "label": "Consolidating long-term memory", "outcome": mem_outcome})
        return 0

    def _run_open_loops(self, cur: datetime.datetime, steps_taken: list) -> int:
        """Checks matters waiting on an outcome; returns findings settled or new."""
        self._set_step("open_loops", "Checking matters waiting on an outcome")
        loop_outcome = "skipped"
        found = 0
        if self.open_loops:
            try:
                # Wrap open_loops research function with daily cap
                orig_research = getattr(self.open_loops, "research", None)
                self.open_loops.research = lambda q: self.capped_search(q, limit=5, now=cur)
                try:
                    # The cycle's own budget and question rules (until 2026-10-27 the defaults, room=2 and may_ask=True,
                    # let it ask at 00:47 past the day's cap and an unanswered question). What it says goes through
                    # Proactive.deliver, which holds it until Alex is around.
                    room, may_ask = 2, True
                    if self.proactive is not None:
                        today = self.proactive._rows(cur.replace(hour=0, minute=0, second=0, microsecond=0))
                        room = max(0, int(self.proactive.settings()["daily_cap"]) - len(today))
                        may_ask = self.proactive._question_allowed(cur, today)
                    res = self.open_loops.run(cur, room=room, may_ask=may_ask, include_research=True)
                    loop_settled = len(res.get("settled", []))
                    loop_news = len(res.get("news", []))
                    found = loop_settled + loop_news
                    loop_outcome = f"settled: {loop_settled}, news: {loop_news}"
                finally:
                    if orig_research:
                        self.open_loops.research = orig_research
            except Exception as e:
                loop_outcome = f"failed: {e}"
                logger.warning(f"AwayShift open loops check failed: {e}")
        steps_taken.append({"step": "open_loops", "label": "Checking matters waiting on an outcome", "outcome": loop_outcome})
        return found

    def _run_creators(self, cur: datetime.datetime, pool: Any, steps_taken: list) -> int:
        """Checks followed channels for new uploads; returns videos found."""
        self._set_step("creators", "Checking channels you follow")
        try:
            found = self._step_creators(cur, pool)
            outcome = f"{found} new video(s)"
        except Exception as e:
            outcome = f"failed: {e}"
            logger.warning(f"AwayShift channel feeds failed: {e}")
            found = 0
        steps_taken.append({"step": "creators", "label": "Checking channels you follow", "outcome": outcome})
        return found

    def _run_interests(self, cur: datetime.datetime, pool: Any, pending_count: int, steps_taken: list) -> int:
        """Looks into followed interests when the pool has room; returns findings added."""
        last_interests = _parse_iso(self.db.get_state(STATE_INTERESTS_AT, ""))
        if not (pool and pending_count < POOL_LOW_WATERMARK and self.search_budget_remaining(cur) > 0
                and (last_interests is None or cur - last_interests >= INTEREST_EVERY)
                and self._generator_ready("interests", "interest", cur)):
            return 0
        self._mark_generated("interests", "interest", cur)      # judges the last batch first, then records this run
        self.db.set_state(STATE_INTERESTS_AT, cur.isoformat())
        self._set_step("interests", "Looking into interests")
        try:
            found = self._step_interests(cur, pool)
            outcome = f"{found} finding(s)"
        except Exception as e:
            outcome = f"failed: {e}"
            logger.warning(f"AwayShift interests check failed: {e}")
            found = 0
        steps_taken.append({"step": "interests", "label": "Looking into interests", "outcome": outcome})
        return found

    def _run_patterns(self, cur: datetime.datetime, pool: Any, pending_count: int, steps_taken: list) -> int:
        """Reflects on weekly patterns when due; returns questions added."""
        last_patterns = _parse_iso(self.db.get_state(STATE_LAST_PATTERNS, ""))
        if not (pool and pending_count < POOL_LOW_WATERMARK
                and (last_patterns is None or (cur - last_patterns).total_seconds() >= 86400)):
            return 0
        self._set_step("patterns", "Reflecting on weekly patterns")
        try:
            found = self._step_patterns(cur, pool)
            self.db.set_state(STATE_LAST_PATTERNS, cur.isoformat())
            outcome = f"{found} question(s)"
        except Exception as e:
            outcome = f"failed: {e}"
            logger.warning(f"AwayShift patterns check failed: {e}")
            found = 0
        steps_taken.append({"step": "patterns", "label": "Reflecting on weekly patterns", "outcome": outcome})
        return found

    def _run_curiosity(self, cur: datetime.datetime, pool: Any, pending_count: int, steps_taken: list) -> int:
        """Prepares curious questions when the pool is low; returns questions added."""
        if not (pool and pending_count < POOL_LOW_WATERMARK and self._generator_ready("curiosity", "curiosity", cur)):
            return 0
        self._mark_generated("curiosity", "curiosity", cur)
        self._set_step("curiosity_pool", "Preparing curious questions")
        try:
            found = self._step_curiosity(cur, pool)
            outcome = f"{found} question(s)"
        except Exception as e:
            outcome = f"failed: {e}"
            logger.warning(f"AwayShift curiosity pool failed: {e}")
            found = 0
        steps_taken.append({"step": "curiosity_pool", "label": "Preparing curious questions", "outcome": outcome})
        return found

    def _run_research_worker(self, cur: datetime.datetime):
        """Scans for free AI opportunities; returns (step dict or None, findings added)."""
        try:
            from core import research_worker
            if research_worker.research_due(self.db, now=cur):
                self._set_step("research_worker", "Scanning for free AI opportunities")

                def wrapped_search(q, limit=4):
                    results = self.capped_search(q, limit=limit, now=cur)
                    # convert structured results to text format expected by gather_opportunities
                    lines = [f"Search results for query: {q}"]
                    for i, r in enumerate(results, 1):
                        lines.append(f"{i}. {r.get('title', '')}\n   {r.get('url', '')}\n   {r.get('snippet', '')}")
                    return "\n".join(lines)

                name = self.memory.subject() if self.memory else "the owner"
                res = research_worker.run_research_cycle(
                    self.db, memory=self.memory, llm=self.llm, proactive=self.proactive,
                    log=logger, search_fn=wrapped_search, name=name
                )
                found = 1 if res and res.get("count", 0) > 0 else 0
                return ({"step": "research_worker", "label": "Scanning for free AI opportunities",
                         "outcome": f"{res.get('count', 0) if res else 0} opportunit(ies)"}, found)
        except Exception as e:
            logger.warning(f"AwayShift AI research worker step failed: {e}")
        return (None, 0)

    def _finish_session(self, cur: datetime.datetime, now: datetime.datetime | None,
                        found_total: int, steps_taken: list) -> dict[str, Any]:
        """Publishes the finish, records the session, and returns the session summary."""
        try:
            from core.away_shift import publish_activity
        except ImportError:
            from away_shift import publish_activity  # type: ignore[no-redef]
        publish_activity("finished", label="Background work finished", step="", found=found_total)
        with self._lock:
            self._current_step = ""
            self._step_label = ""
        summary_str = f"{found_total} item(s)" if found_total else "memory and pending matters checked"
        self.db.set_state(STATE_LAST_SUMMARY, summary_str)
        session_record = {
            "started_at": cur.isoformat(),
            "finished_at": (now or self.now_fn()).isoformat(),
            "found": found_total,
            "steps": steps_taken,
            "searches_today": self.searches_today(cur),
            "search_budget_remaining": self.search_budget_remaining(cur),
        }
        self.db.set_state(STATE_LAST_RECORD, json.dumps(session_record))
        logger.info(f"AwayShift session finished; {found_total} candidate(s)/finding(s) prepared")
        return {"found": found_total, "timestamp": cur.isoformat()}
