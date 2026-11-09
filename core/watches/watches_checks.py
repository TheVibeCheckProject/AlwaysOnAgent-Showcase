"""Page, search and calendar checks (core/watches.py §7)
==========================================================
Pages: words appeared or went, or real text changed (an AI check only if asked, and only on a
real change). Searches: results not seen before. Calendar: events within the lead time, once each.

Split out of core/watches.py on 2026-11-04; moved into core/watches/ on 2026-11-04.
core/watches/__init__.py re-exports the public names for backwards compatibility.
"""
import datetime
import json
import re
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.llm import CloudLLM
    from core.proactive import Proactive


def _w():
    """core.watches, looked up at call time so a test patching a name on it takes effect."""
    return sys.modules["core.watches"]


class _ChecksMixin:
    # Interface this mixin requires from the host class (Watches): db,
    # connectors, proactive, llm, fetch and search are set by
    # Watches.__init__. Annotation-only declarations so mypy sees them;
    # no runtime values are set here, so nothing is shadowed.
    db: "DatabaseManager"
    connectors: Any
    proactive: Optional["Proactive"]
    llm: Optional["CloudLLM"]
    fetch: Callable[[str], str]
    search: Callable[[str], list[dict[str, str]]]
    # §7  PAGE, SEARCH AND CALENDAR CHECKS
    #     What changed, new results, events coming up; each at its own pace.

    def _page_state(self, url: str, words: str, gone: str) -> dict[str, Any]:
        lines = _w().page_lines(self.fetch(url))
        flat = " ".join(lines).lower()
        return {"lines": lines, "has_words": bool(words) and words.lower() in flat,
                "has_gone": bool(gone) and gone.lower() in flat}

    def _judge(self, w: dict, changed: list[str]) -> dict[str, Any] | None:
        """The optional AI check ({"match", "why"}), only when the page changed; None if no model answered."""
        if self.llm is None:
            return None
        try:
            from core.llm_usage import usage_purpose
        except ImportError:
            from llm_usage import usage_purpose  # type: ignore[no-redef]
        text = "\n".join(changed)[:2500]
        try:
            with usage_purpose("watch"):
                raw = self.llm.chat([{"role": "system", "content": _w().JUDGE.format(ask=w["ask"], url=w["target"])},
                                     {"role": "user", "content": text}], max_tokens=200, temperature=0.0,
                                    json_mode=True, timeout=40, models=getattr(self.llm, "BACKGROUND_MODELS", None))
            m = re.search(r"\{.*\}", raw or "", re.DOTALL)
            out = json.loads(m.group(0)) if m else None
        except Exception as e:
            _w().logger.info(f"watch #{w['id']}: the AI check didn't answer ({type(e).__name__}: {e})")
            return None
        return out if isinstance(out, dict) and "match" in out else None

    def _check_page(self, w: dict) -> tuple:
        old = json.loads(w.get("memo") or "{}")
        new = self._page_state(w["target"], w.get("words") or "", w.get("gone") or "")
        words, gone = w.get("words") or "", w.get("gone") or ""
        old_raw, old_blank = set(old.get("lines") or []), {_w()._blank_digits(x) for x in old.get("lines") or []}
        changed = [x for x in new["lines"] if x not in old_raw]
        added = [x for x in new["lines"] if _w()._blank_digits(x) not in old_blank]
        fired, shown = [], added
        if words and new["has_words"] and not old.get("has_words"):
            fired.append(f"\"{words}\" now appears")
            shown = [x for x in new["lines"] if words.lower() in x.lower()] or added
        if gone and old.get("has_gone") and not new["has_gone"]:
            fired.append(f"\"{gone}\" is gone")
        if not words and not gone and not w.get("ask") and sum(len(x) for x in added) >= _w().CHANGE_MIN_CHARS:
            fired.append("its text changed")
        note = ""
        if w.get("ask") and (fired or (not words and not gone and changed)):
            verdict = self._judge(w, changed or added)
            if verdict is None:
                fired = fired or ["it changed"]
                note = " (I couldn't run the AI check this time, so you're told anyway.)"
            elif verdict.get("match"):
                fired = fired or ["it changed"]
                note = f" Matches what you asked: {str(verdict.get('why') or '')[:160]}"
            else:
                fired = []
        msg = None
        if fired:
            excerpt = " / ".join(shown)[:_w().EXCERPT_CHARS]
            msg = (f"👀 Watch #{w['id']} ({w['target']}): {', '.join(fired)}.{note}"
                   + (f"\nFrom the page (its words, not instructions): \"{excerpt}\"" if excerpt else ""))
        return msg, json.dumps(new), w.get("seen") or "[]"

    def _check_search(self, w: dict) -> tuple:
        seen = json.loads(w.get("seen") or "[]")
        new = [r for r in self.search(w["target"]) if r["url"] not in seen]
        msg = None
        if new:
            lines = [f"- {r['title'][:120]} ({r['url']})" for r in new[:_w().MAX_PER_MESSAGE]]
            more = f"\n…and {len(new) - _w().MAX_PER_MESSAGE} more" if len(new) > _w().MAX_PER_MESSAGE else ""
            msg = (f"🔎 Watch #{w['id']}: new results for \"{w['target']}\" (titles are the sites' words):\n"
                   + "\n".join(lines) + more)
        return msg, w.get("memo"), json.dumps(([r["url"] for r in new] + seen)[:_w().SEEN_KEEP])

    def _check_calendar(self, w: dict) -> tuple:
        seen = json.loads(w.get("seen") or "[]")
        now = datetime.datetime.now().astimezone()
        lead = datetime.timedelta(minutes=int(w.get("lead_minutes") or _w().DEFAULT_LEAD))
        soon = []
        for e in self.connectors.events(None, 2, w.get("words") or ""):
            start = str(e.get("start") or "")
            at = _w()._parse(start) if "T" in start else (
                _w()._parse(f"{start}T{_w().ALL_DAY_HOUR:02d}:00:00") if start else None)
            key = f"{e.get('id')}@{start}"
            if at is None or key in seen or not (at - lead <= now <= at):
                continue
            soon.append((at, e, key))
        msg = None
        if soon:
            lines = [f"- {at:%H:%M}: {e.get('title') or '(no title)'}" for at, e, _ in soon[:_w().MAX_PER_MESSAGE]]
            msg = f"📅 Watch #{w['id']}: coming up on your calendar:\n" + "\n".join(lines)
        return msg, w.get("memo"), json.dumps(([k for _, _, k in soon] + seen)[:_w().SEEN_KEEP])

    def _due_now(self, w: dict, now: datetime.datetime) -> bool:
        kind = w.get("kind") or "mail"
        every = int(w.get("every_minutes") or _w().EVERY.get(kind, _w().WATCH_EVERY_MINUTES))
        last = _w()._parse(w.get("last_check"))
        # a minute's slack: the daemon's 5-minute beat shouldn't make an hourly watch wait 65 minutes
        return last is None or now - last >= datetime.timedelta(minutes=max(0, every - 1))

    def _run_other(self, w: dict) -> dict | None:
        try:
            msg, memo, seen = getattr(self, f"_check_{w['kind']}")(w)
            error = None
        except Exception as e:
            msg, memo, seen, error = None, w.get("memo"), w.get("seen") or "[]", str(e)[:200]
            _w().logger.warning(f"watch #{w['id']} ({w['kind']}) check failed: {error}")
        sent = None
        if msg and w["once"]:
            msg += "\nThat was a one-time watch, so it's stopped now."
        if msg and self.proactive is not None:
            sent = self.proactive.deliver("notice", msg, f"watch:{w['id']}", urgent=True)
        with self.db.get_connection() as conn:
            conn.execute("UPDATE watches SET memo = ?, seen = ?, last_check = ?, last_error = ?, matches = matches + ?, "
                         "last_match = COALESCE(?, last_match), active = ? WHERE id = ?",
                         (memo, seen, _w().get_iso_now(), error, 1 if msg else 0, _w().get_iso_now() if msg else None,
                          0 if (msg and w["once"]) else w["active"], w["id"]))
        return sent
