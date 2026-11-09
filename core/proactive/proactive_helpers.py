"""Small helpers for the proactive agent.

Split out of core/proactive.py §2 on 2026-11-03; core/proactive/__init__.py re-exports
these names, so `proactive.greeting(...)` and `proactive._parse_day(...)` still
work.
"""
import datetime


def greeting(now: datetime.datetime) -> str:
    """The brief's greeting from the clock. The model chose "Good night" at 2-3 am (a goodbye), before "still to come
    today" (2026-10-28, 11-02)."""
    h = now.hour
    return ("Good morning" if 5 <= h < 12 else "Good afternoon" if 12 <= h < 17 else "Good evening" if 17 <= h < 22
            else "Hi")


def _now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def _parse_iso(value: str) -> datetime.datetime | None:
    try:
        t = datetime.datetime.fromisoformat(str(value))
        return t if t.tzinfo else t.astimezone()
    except (TypeError, ValueError):
        return None


def _parse_day(text: str) -> datetime.date | None:
    """'October 12, 2026', 'Oct 12, 2026' or '10/12/2026' (renewal dates as emails state them)."""
    text = (text or "").replace(".", "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _hhmm(value: str, default: str) -> datetime.time:
    try:
        h, m = (value or default).split(":")
        return datetime.time(int(h), int(m))
    except (ValueError, AttributeError):
        h, m = default.split(":")
        return datetime.time(int(h), int(m))
