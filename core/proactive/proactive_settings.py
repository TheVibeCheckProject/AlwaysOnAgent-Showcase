"""§3  SETTINGS AND YOUR SWITCHES — split out of core/proactive.py on 2026-11-03.

One mixin per section: every method here stays a bound method of Proactive
(the class inherits this mixin), so all existing callers work unchanged.
"""
import datetime
import json
import re
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .proactive_constants import DEFAULTS, PROACTIVE_EVERY_MINUTES, STATE_EXCLUSIONS, STATE_INTERESTS, SWITCH_KEYS
    from .proactive_helpers import _hhmm, _parse_iso
except ImportError:
    from core.proactive.proactive_constants import (
        DEFAULTS,
        PROACTIVE_EVERY_MINUTES,
        STATE_EXCLUSIONS,
        STATE_INTERESTS,
        SWITCH_KEYS,
    )
    from core.proactive.proactive_helpers import _hhmm, _parse_iso


class _SettingsMixin:
    # Interface this mixin requires from the host class (Proactive):
    # db / now are set by Proactive.__init__. Declared here so mypy
    # can see them.
    db: "DatabaseManager"
    now: Callable[[], datetime.datetime]

    # ----------------------------------------------------------------------------------- settings & curate
    def settings(self) -> dict[str, str]:
        return {k: self.db.get_state(f"proactive_{k}", v) for k, v in DEFAULTS.items()}

    def interests(self) -> list[str]:
        raw = self.db.get_state(STATE_INTERESTS, "[]")
        try:
            data = json.loads(raw or "[]")
            return [str(x).strip() for x in data if str(x).strip()] if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    def exclusions(self) -> list[str]:
        raw = self.db.get_state(STATE_EXCLUSIONS, "[]")
        try:
            data = json.loads(raw or "[]")
            return [str(x).strip() for x in data if str(x).strip()] if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    def add_interest(self, topic: str) -> list[str]:
        topic = str(topic or "").strip()
        if not topic:
            return self.interests()
        current = self.interests()
        if not any(t.lower() == topic.lower() for t in current):
            current.append(topic)
            self.db.set_state(STATE_INTERESTS, json.dumps(current))
        return current

    def remove_interest(self, topic: str) -> list[str]:
        topic = str(topic or "").strip().lower()
        current = [t for t in self.interests() if t.lower() != topic]
        self.db.set_state(STATE_INTERESTS, json.dumps(current))
        return current

    def add_exclusion(self, topic: str) -> list[str]:
        topic = str(topic or "").strip()
        if not topic:
            return self.exclusions()
        current = self.exclusions()
        if not any(t.lower() == topic.lower() for t in current):
            current.append(topic)
            self.db.set_state(STATE_EXCLUSIONS, json.dumps(current))
        return current

    def remove_exclusion(self, topic: str) -> list[str]:
        topic = str(topic or "").strip().lower()
        current = [t for t in self.exclusions() if t.lower() != topic]
        self.db.set_state(STATE_EXCLUSIONS, json.dumps(current))
        return current

    def is_excluded(self, text: str) -> bool:
        if not text:
            return False
        text = str(text)
        never: list[str] = []
        try:
            try:
                from core.today import recipe
            except ImportError:
                from today import recipe  # type: ignore[no-redef]
            never = recipe(self.db)["rules"]["never"]          # what his Today recipe says never to show
        except (AttributeError, KeyError, TypeError, sqlite3.Error):
            pass
        for ex in self.exclusions() + never:
            ex = (ex or "").strip()
            if not ex:
                continue
            try:
                if re.search(rf"\b{re.escape(ex)}\b", text, re.IGNORECASE):
                    return True
            except (TypeError, re.error):
                if ex.lower() in text.lower():
                    return True
        return False

    def switch(self, name: str) -> bool:
        name = str(name).strip().lower()
        val = self.settings().get(name, "1")
        return str(val).lower() in ("1", "true", "yes", "on")

    def switch_for_source(self, source: str | None) -> bool:
        if not source:
            return True
        s = str(source).lower()
        if s in ("open_loop", "open_loops"):
            return self.switch("open_loops")
        if s == "curiosity":
            return self.switch("curiosity")
        if s == "research":
            return self.switch("research")
        if s in ("pattern", "patterns"):
            return self.switch("patterns")
        return True

    def change(self, **fields) -> dict[str, str]:
        """Validates and stores settings; raises ValueError with the reason."""
        for k, v in fields.items():
            if v is None:
                continue
            if k == "add_interest":
                self.add_interest(str(v))
                continue
            elif k == "remove_interest":
                self.remove_interest(str(v))
                continue
            elif k == "add_exclusion":
                self.add_exclusion(str(v))
                continue
            elif k == "remove_exclusion":
                self.remove_exclusion(str(v))
                continue
            v = str(v).strip()
            if k in ("enabled", *SWITCH_KEYS):
                v = "1" if v.lower() in ("1", "true", "on", "yes") else "0"
            elif k == "brief_time":
                if v.lower() == "auto":
                    v = "auto"
                elif not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", v):
                    raise ValueError("brief_time must be HH:MM (24-hour), e.g. 07:30, or 'auto' (when you're around)")
            elif k == "quiet_hours":
                if v.lower() in ("off", "none", ""):
                    v = "off"
                elif not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d-([01]?\d|2[0-3]):[0-5]\d", v):
                    raise ValueError("quiet_hours must be HH:MM-HH:MM, e.g. 22:00-08:00, or 'off'")
            elif k in ("daily_cap", "questions_per_day"):
                if not v.isdigit() or not 0 <= int(v) <= (20 if k == "daily_cap" else 10):
                    raise ValueError(f"{k} must be a whole number (daily_cap 0-20, questions_per_day 0-10)")
            elif k == "brief_to":
                v = v.lower()
                if v not in ("today", "chat"):
                    raise ValueError("brief_to is 'today' (the Today screen's header) or 'chat' (a message)")
            elif k != "location":
                raise ValueError(f"unknown setting '{k}'")
            self.db.set_state(f"proactive_{k}", v)
        return self.settings()

    def quiet(self, t: datetime.datetime) -> bool:
        if self.settings()["quiet_hours"].strip().lower() in ("off", "none", ""):
            return False
        start_s, _, end_s = self.settings()["quiet_hours"].partition("-")
        start, end, now = _hhmm(start_s, "22:00"), _hhmm(end_s, "08:00"), t.time()
        return (start <= now or now < end) if start > end else (start <= now < end)

    def due(self) -> bool:
        last = _parse_iso(self.db.get_state("proactive_last_run", ""))
        return last is None or self.now() - last >= datetime.timedelta(minutes=PROACTIVE_EVERY_MINUTES)

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
