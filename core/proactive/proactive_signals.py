"""§8  SIGNALS AND CONTEXT — split out of core/proactive.py on 2026-11-03.

One mixin per section: every method here stays a bound method of Proactive
(the class inherits this mixin), so all existing callers work unchanged.
"""
import datetime
import json
import re
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.llm import CloudLLM
    from core.memory import MemoryManager

try:
    from core.llm_usage import usage_purpose

    from .proactive_constants import DATED, logger
    from .proactive_helpers import _parse_day, _parse_iso
except ImportError:
    from llm_usage import usage_purpose  # type: ignore[no-redef]

    from core.proactive.proactive_constants import DATED, logger
    from core.proactive.proactive_helpers import _parse_day, _parse_iso


class _SignalsMixin:
    # Interface this mixin requires from the host class (Proactive):
    # db / memory / llm / connectors are set by Proactive.__init__.
    # Declared here so mypy can see them.
    db: "DatabaseManager"
    connectors: Any
    memory: "MemoryManager"
    llm: "CloudLLM"

    def _signals(self, now: datetime.datetime) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        # Not _schedule_signals any more (2026-10-27): a reminder pushes by itself when it's due, and the "coming up"
        # heads-up was made again every cycle until then (the same guide-reminder notice at 09:16 and 09:46 on 10-26).
        # Today's reminders are in the brief's "still to come".
        for fn in (self._email_signals, self._renewal_signals):
            try:
                out += fn(now)
            except Exception as e:
                logger.debug(f"signal {fn.__name__} failed: {e}")
        return out

    def _email_signals(self, now) -> list[dict[str, str]]:
        if not self.connectors or not self.connectors.installed():
            return []
        seen = set(json.loads(self.db.get_state("proactive_emails_seen", "[]") or "[]"))
        fresh = []
        for e in self.connectors.important(days=1, limit=8).get("emails", []):
            if e.get("score", 0) >= 5 and e.get("id") not in seen:
                fresh.append(e)
                seen.add(e["id"])
        self.db.set_state("proactive_emails_seen", json.dumps(list(seen)[-300:]))
        return [{"type": "email", "detail": f"From {e.get('from_name') or e.get('from')}: \"{e.get('subject')}\" "
                                            f"({(e.get('snippet') or '')[:140]})"} for e in fresh]

    def _renewal_signals(self, now) -> list[dict[str, str]]:
        if not self.connectors or not self.connectors.installed():
            return []
        flagged = set(json.loads(self.db.get_state("proactive_renewals_flagged", "[]") or "[]"))
        out = []
        for p in self.connectors.subscriptions(kind="paid").get("paid", []):
            day = _parse_day(p.get("renews") or "")
            key = f"{p.get('service')}|{p.get('renews')}"
            if day and 0 <= (day - now.date()).days <= 3 and key not in flagged:
                out.append({"type": "renewal", "detail": f"{p.get('service')} renews on {p.get('renews')}"
                                                         + (f" ({p['amount']})" if p.get("amount") else "")})
                flagged.add(key)
        self.db.set_state("proactive_renewals_flagged", json.dumps(sorted(flagged)[-100:]))
        return out

    # ------------------------------------------------------------------------------------------- context
    def _profile(self) -> str:
        try:
            return (self.memory.get_profile() if self.memory else "")[:2500]
        except (AttributeError, sqlite3.Error):
            return ""

    def _facts(self, dated_only: bool = False, limit: int = 40) -> list[str]:
        try:
            rows = self.memory._fact_rows() if self.memory else []
        except (AttributeError, sqlite3.Error):
            return []
        facts = [r["content"] for r in rows]
        return [f for f in facts if DATED.search(f)][:limit] if dated_only else facts[:limit]

    def _recent_owner_lines(self, now, hours: int = 48, limit: int = 15) -> list[str]:
        since = now - datetime.timedelta(hours=hours)
        try:
            rows = self.db.get_connection().execute(
                "SELECT content, created_at FROM mem_messages WHERE role = 'user' ORDER BY id DESC LIMIT 60").fetchall()
        except (AttributeError, sqlite3.Error):
            return []
        out = [f"[{r['created_at'][:16]}] {r['content'][:200]}" for r in rows
               if (_parse_iso(r["created_at"]) or since) >= since]
        return list(reversed(out[:limit]))

    def _owner_replied_since(self, when: str) -> bool:
        t = _parse_iso(when)
        try:
            rows = self.db.get_connection().execute(
                "SELECT created_at FROM mem_messages WHERE role = 'user' ORDER BY id DESC LIMIT 5").fetchall()
        except (AttributeError, sqlite3.Error):
            return False
        return any((_parse_iso(r["created_at"]) or t) > t for r in rows) if t else True

    def _ask_json(self, system: str, user: str, max_tokens: int = 500) -> dict[str, Any] | None:
        try:
            with usage_purpose("proactive"):
                # Background work: lite first, so the Flash models' 20 free requests a day stay for the owner's chat
                raw = self.llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                    max_tokens=max_tokens, temperature=0.5, json_mode=True, timeout=40,
                                    models=getattr(self.llm, "BACKGROUND_MODELS", None))
        except Exception as e:
            logger.warning(f"proactive AI call failed: {e}")
            return None
        try:
            m = re.search(r"\{.*\}", raw or "", re.DOTALL)
            return json.loads(m.group(0)) if m else None
        except ValueError:
            return None

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
