"""Split out of core/away_shift.py §5 on 2026-11-03; core/away_shift/__init__.py re-exports these names.
The search budget: searches today, the cap, a capped search, a result's real date.
"""
import datetime
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .away_shift_constants import DAILY_SEARCH_CAP, STATE_SEARCHES_PREFIX
    from .away_shift_dates import parse_published
except ImportError:
    from core.away_shift.away_shift_constants import DAILY_SEARCH_CAP, STATE_SEARCHES_PREFIX
    from core.away_shift.away_shift_dates import parse_published

logger = logging.getLogger("AlwaysOnAgent.AwayShift")


class _BudgetMixin:
    # Host state set by AwayShift.__init__ (core/away_shift/__init__.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime. Types mirror AwayShift.__init__.
    db: "DatabaseManager"  # quoted: imported only under TYPE_CHECKING
    now_fn: Callable[[], datetime.datetime]
    search_fn: Callable | None
    page_date_fn: Callable[[str], str] | None

    def searches_today(self, now: datetime.datetime | None = None) -> int:
        cur = now or self.now_fn()
        key = f"{STATE_SEARCHES_PREFIX}{cur.strftime('%Y-%m-%d')}"
        try:
            return int(self.db.get_state(key, "0") or 0)
        except (ValueError, TypeError):
            return 0

    def record_searches(self, count: int = 1, now: datetime.datetime | None = None) -> int:
        cur = now or self.now_fn()
        key = f"{STATE_SEARCHES_PREFIX}{cur.strftime('%Y-%m-%d')}"
        new_val = self.searches_today(cur) + count
        self.db.set_state(key, str(new_val))
        return new_val

    def search_budget_remaining(self, now: datetime.datetime | None = None) -> int:
        return max(0, DAILY_SEARCH_CAP - self.searches_today(now))

    def capped_search(self, query: str, limit: int = 4, now: datetime.datetime | None = None,
                      recent: bool = False) -> list[dict[str, Any]]:
        """Web search wrapped with the daily budget cap. recent: the past week only (see web_tools.search_results)."""
        cur = now or self.now_fn()
        if self.search_budget_remaining(cur) <= 0:
            logger.info(f"AwayShift: daily search cap reached ({DAILY_SEARCH_CAP}/day), skipping search: {query!r}")
            return []
        self.record_searches(1, cur)
        if self.search_fn is not None:
            return self.search_fn(query, limit)
        from core.web_tools import search_results
        return search_results(query, limit=limit, recent=recent)[2]

    def _published(self, result: dict[str, Any], cur: datetime.datetime) -> datetime.datetime | None:
        """When a search result was published: the date its provider gave, else what its page says, else None."""
        when = parse_published(result.get("published") or "", cur)
        if when is None and result.get("url"):
            try:
                if self.page_date_fn is not None:
                    raw = self.page_date_fn(result["url"])
                else:
                    from core.web_tools import page_published
                    raw = page_published(result["url"])
                when = parse_published(raw, cur)
            except Exception as e:
                logger.info(f"AwayShift: no publication date for {result.get('url')} ({type(e).__name__}: {e})")
        return when

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
