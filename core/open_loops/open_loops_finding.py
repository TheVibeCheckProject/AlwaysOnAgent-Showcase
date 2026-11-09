"""Finding loops: tagging facts that wait on something, closing ones settled elsewhere (core/open_loops.py §5).

Split out of core/open_loops.py on 2026-11-03; moved into core/open_loops/ on 2026-11-04. The methods are
unchanged apart from being dedented one level, and the names they use are imported from the sibling modules below.
"""

import datetime
import json
from typing import TYPE_CHECKING, Any

from .open_loops_constants import DEFAULT_WAIT_DAYS, LOOP_CATEGORIES, TAG_BATCH, logger
from .open_loops_helpers import TAG_PROMPT, _json, _one_line, _parse_day, _parse_iso, _queries

try:
    from core.database import get_iso_now
except ImportError:
    from database import get_iso_now  # type: ignore[no-redef]

class _FindingMixin:
    # Host-class state set in OpenLoops.__init__ (core/open_loops/__init__.py).
    # Annotation-only declaration: no runtime value exists, so nothing here can
    # shadow anything at runtime.
    memory: Any

    # _conn is defined on OpenLoops itself, which comes after every mixin in
    # the MRO, so it lives under TYPE_CHECKING only: a runtime stub would
    # shadow the real method.
    if TYPE_CHECKING:
        def _conn(self) -> Any: ...

    def tag_new(self, now: datetime.datetime) -> int:
        """Looks at active facts not yet looked at; returns how many open loops it found."""
        rows = self._conn().execute(
            "SELECT id, content, category, created_at FROM mem_facts WHERE status = 'active' AND invalid_from IS NULL "
            f"AND category IN ({','.join('?' * len(LOOP_CATEGORIES))}) "
            "AND id NOT IN (SELECT fact_id FROM mem_open_loops) ORDER BY id LIMIT ?",
            (*LOOP_CATEGORIES, TAG_BATCH)).fetchall()
        if not rows:
            return 0
        ids = {r["id"]: r for r in rows}
        listing = [{"id": r["id"], "saved": str(r["created_at"])[:10], "memory": r["content"]} for r in rows]
        # The rest, for names and numbers: "waiting for his permit" doesn't say which one; another memory does
        context = [r["content"] for r in self.memory._fact_rows() if r["id"] not in ids][:60]
        out = self.memory._bg_chat(
            [{"role": "system", "content": TAG_PROMPT},
             {"role": "user", "content": f"Today is {now.date().isoformat()}.\n\n"
                                         f"Memories to mark:\n{json.dumps(listing, ensure_ascii=False)}\n\n"
                                         f"Other memories (context only):\n{json.dumps(context, ensure_ascii=False)}"}],
            "open_loops", max_tokens=1500, temperature=0.1, json_mode=True, timeout=60)
        ans = _json(out)
        if not isinstance(ans, dict) or not isinstance(ans.get("open"), list):
            logger.warning("open loops: tagging got no readable answer; tried again next run")
            return 0
        found = {}
        for item in ans["open"]:
            try:
                fid = int(item.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            if fid not in ids:
                continue
            saved = (_parse_iso(ids[fid]["created_at"]) or now).date()
            day = _parse_day(item.get("check_after")) or saved + datetime.timedelta(days=DEFAULT_WAIT_DAYS)
            found[fid] = (_one_line(item.get("waiting_on"), 160) or ids[fid]["content"][:160], day.isoformat(),
                          _one_line(item.get("mail"), 60),
                          json.dumps(_queries(item.get("research"))) if _queries(item.get("research")) else None)
        stamp = get_iso_now()
        with self._conn() as conn:
            for fid in ids:
                if fid in found:
                    conn.execute("INSERT OR IGNORE INTO mem_open_loops (fact_id, status, waiting_on, check_after, mail, "
                                 "research, created_at) VALUES (?, 'open', ?, ?, ?, ?, ?)", (fid, *found[fid], stamp))
                else:
                    conn.execute("INSERT OR IGNORE INTO mem_open_loops (fact_id, status, created_at) "
                                 "VALUES (?, 'none', ?)", (fid, stamp))
        if found:
            logger.info(f"open loops: tagged {sorted(found)}")
        return len(found)

    # ------------------------------------------------------------------------------------------- 2. closed elsewhere
    def close_settled_elsewhere(self) -> int:
        """Loops whose fact is no longer active (replaced in chat, retired in the weekly check, deleted)."""
        rows = self._conn().execute(
            "SELECT l.fact_id, f.superseded_by, f.status FROM mem_open_loops l LEFT JOIN mem_facts f ON f.id = l.fact_id "
            "WHERE l.status = 'open' AND (f.id IS NULL OR f.status != 'active' OR f.invalid_from IS NOT NULL)").fetchall()
        with self._conn() as conn:
            for r in rows:
                conn.execute("UPDATE mem_open_loops SET status = 'closed', closed_by = ?, note = ? WHERE fact_id = ?",
                             (r["superseded_by"], "settled elsewhere" if r["status"] else "fact deleted", r["fact_id"]))
        return len(rows)
