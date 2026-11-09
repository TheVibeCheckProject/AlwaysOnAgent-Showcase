"""Adding and managing watches (core/watches.py §5)
=================================================
Making a watch of each kind (saved only if it works now, and what's already there doesn't
count), changing or stopping one, follow-up deadlines, and the plain-English description.

Split out of core/watches.py on 2026-11-04; moved into core/watches/ on 2026-11-04.
core/watches/__init__.py re-exports the public names for backwards compatibility.
"""
import json
import re
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager


def _w():
    """core.watches, looked up at call time so a test patching a name on it takes effect."""
    return sys.modules["core.watches"]


class _ManageMixin:
    # Interface this mixin requires from the host class (Watches) and its
    # sibling mixins: db, connectors and search are set by Watches.__init__;
    # _matching lives on _MailMixin and _page_state on _ChecksMixin (both
    # later in the MRO than this mixin, so these are type-only declarations
    # — a runtime stub here would shadow the real method). Declared here so
    # mypy can see them.
    db: "DatabaseManager"
    connectors: Any
    search: Callable[[str], list[dict[str, str]]]

    if TYPE_CHECKING:
        def _matching(self, w: dict, keep_bulk: bool = False) -> list[tuple]: ...
        def _page_state(self, url: str, words: str, gone: str) -> dict[str, Any]: ...
    # §5  ADDING AND MANAGING
    #     Making a watch of each kind, changing or stopping one, follow-up deadlines, and the
    #     plain-English description.

    def add(self, source: str, sender: str = "", subject: str = "", words: str = "", once: bool = False,
            deadline: str = "", note: str = "", everything: bool = False) -> dict:
        """Saves a watch and marks what already matches as seen. With a deadline it's a follow-up (once, and a
        reminder if nothing came by then; note: what the owner is waiting on). everything: every new inbox email
        (bulk mail skipped), narrowed by sender/subject/words if given. Raises ValueError (bad input) or the
        connector's error (not connected), so the chat can say why."""
        due_at = self.parse_deadline(deadline) if (deadline or "").strip() else None
        if due_at is not None:
            once = True
        source = (source or "both").strip().lower()
        if source not in _w().SOURCES:
            raise ValueError(f"source must be one of {', '.join(_w().SOURCES)}")
        sender, subject, words = ((v or "").strip()[:80] for v in (sender, subject, words))
        if not (sender or subject or words or everything):
            raise ValueError("a watch needs a sender, a subject or words to match (or every email)")
        w = {"source": source, "sender": sender, "subject": subject, "words": words, "once": int(bool(once)),
             "everything": int(bool(everything))}
        seen = [key for key, _ in self._matching(w, keep_bulk=True)]   # raises if the mailbox isn't connected
        with self.db.get_connection() as conn:
            cur = conn.execute("INSERT INTO watches (source, sender, subject, words, once, seen, created_at, last_check, "
                               "deadline, note, everything) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (source, sender, subject, words, w["once"], json.dumps(seen[:_w().SEEN_KEEP]),
                                _w().get_iso_now(), _w().get_iso_now(), due_at.isoformat() if due_at else None,
                                (note or "").strip()[:200] or None, w["everything"]))
        assert cur.lastrowid is not None  # just inserted above
        row = self.get(cur.lastrowid)
        assert row is not None  # just inserted above
        return row

    def add_web(self, kind: str, target: str, words: str = "", gone: str = "", ask: str = "",
                every_minutes: int | None = None, once: bool = False) -> dict:
        """A page or search watch. Looks once now (so a page that can't be read, or a search that fails, isn't
        saved) and remembers what's there: only what changes after counts. Raises ValueError or the fetch's error."""
        kind = (kind or "").strip().lower()
        if kind not in ("page", "search"):
            raise ValueError("a web watch is kind 'page' (a web address) or 'search' (a web search)")
        target = (target or "").strip()[:500]
        words, gone, ask = ((v or "").strip()[:120] for v in (words, gone, ask))
        if not target:
            raise ValueError("a page watch needs its web address" if kind == "page" else "a search watch needs what to search")
        if kind == "page":
            if not re.match(r"^https?://", target, re.IGNORECASE):
                target = "https://" + target
            memo, seen = json.dumps(self._page_state(target, words, gone)), "[]"
        else:
            memo, seen = None, json.dumps([r["url"] for r in self.search(target)][:_w().SEEN_KEEP])
        with self.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO watches (source, kind, target, words, gone, ask, every_minutes, once, seen, memo, "
                "created_at, last_check) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, kind, target, words, gone, ask or None, self._every(kind, every_minutes), int(bool(once)), seen,
                 memo, _w().get_iso_now(), _w().get_iso_now()))
        assert cur.lastrowid is not None  # just inserted above
        row = self.get(cur.lastrowid)
        assert row is not None  # just inserted above
        return row

    def add_calendar(self, words: str = "", lead_minutes: int | None = None, once: bool = False) -> dict:
        """Told lead_minutes before each event (matching words, if given). Reads the calendar once now, so a calendar
        that isn't connected says so instead of saving a watch that never works."""
        words = (words or "").strip()[:120]
        lead = max(1, min(int(lead_minutes or _w().DEFAULT_LEAD), 24 * 60))
        self.connectors.events(None, 1, words)
        with self.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO watches (source, kind, words, lead_minutes, once, seen, created_at, last_check) "
                "VALUES ('calendar', 'calendar', ?, ?, ?, '[]', ?, ?)",
                (words, lead, int(bool(once)), _w().get_iso_now(), _w().get_iso_now()))
        assert cur.lastrowid is not None  # just inserted above
        row = self.get(cur.lastrowid)
        assert row is not None  # just inserted above
        return row

    # ------------------------------------------------------------------------------------------- the store

    @staticmethod
    def _every(kind: str, every_minutes: int | None) -> int | None:
        if not every_minutes:
            return None
        return max(_w().MIN_EVERY[kind], int(every_minutes))

    def get(self, watch_id: int) -> dict | None:
        r = self.db.get_connection().execute("SELECT * FROM watches WHERE id = ?", (int(watch_id),)).fetchone()
        return dict(r) if r else None

    def list(self, include_stopped: bool = False) -> list[dict]:
        where = "" if include_stopped else "WHERE active = 1"
        return [dict(r) for r in self.db.get_connection().execute(f"SELECT * FROM watches {where} ORDER BY id")]

    def change(self, watch_id: int, sender: str | None = None, subject: str | None = None,
               words: str | None = None, once: bool | None = None) -> dict:
        """Narrows or changes an active watch in place (2026-10-10: the owner asked twice to keep only Pinterest's
        upgrade email; with no way to change a watch the chat said it had, and nothing changed). Emails that match
        the new filter now are marked seen. Raises ValueError for an unknown watch or an empty filter."""
        w = self.get(watch_id)
        if not w or not w["active"]:
            raise ValueError(f"no active watch #{watch_id}")
        if (w.get("kind") or "mail") != "mail":
            # Page, search and calendar watches change their words and once; a page looks again so the new words
            # count from now (sender and subject are mail's)
            if words is not None:
                w["words"] = words.strip()[:120]
            if once is not None:
                w["once"] = int(bool(once))
            memo = (json.dumps(self._page_state(w["target"], w["words"] or "", w.get("gone") or ""))
                    if w["kind"] == "page" else w.get("memo"))
            with self.db.get_connection() as conn:
                conn.execute("UPDATE watches SET words = ?, once = ?, memo = ? WHERE id = ?",
                             (w["words"], w["once"], memo, int(watch_id)))
            row = self.get(watch_id)
            assert row is not None  # just updated above; it was active
            return row
        for key, value in (("sender", sender), ("subject", subject), ("words", words)):
            if value is not None:
                w[key] = value.strip()[:80]
        if once is not None:
            w["once"] = int(bool(once))
        if not (w["sender"] or w["subject"] or w["words"] or w.get("everything")):
            raise ValueError("a watch needs a sender, a subject or words to match")
        seen = [key for key, _ in self._matching(w, keep_bulk=True)] + json.loads(w["seen"] or "[]")
        with self.db.get_connection() as conn:
            conn.execute("UPDATE watches SET sender = ?, subject = ?, words = ?, once = ?, seen = ? WHERE id = ?",
                         (w["sender"], w["subject"], w["words"], w["once"],
                          json.dumps(list(dict.fromkeys(seen))[:_w().SEEN_KEEP]), int(watch_id)))
        row = self.get(watch_id)
        assert row is not None  # just updated above; it was active
        return row

    def stop(self, watch_id: int) -> bool:
        with self.db.get_connection() as conn:
            return bool(conn.execute("UPDATE watches SET active = 0 WHERE id = ? AND active = 1",
                                     (int(watch_id),)).rowcount)

    @staticmethod
    def parse_deadline(text: str):
        """A deadline as a local datetime: an ISO date (5 pm that day) or date-time. Raises ValueError for anything
        else, or a deadline already past."""
        import datetime
        t = (text or "").strip()
        try:
            d = datetime.datetime.fromisoformat(t)
        except ValueError:
            raise ValueError("the deadline must be a date (YYYY-MM-DD) or a date and time (YYYY-MM-DDTHH:MM)")
        if len(t) <= 10:
            d = d.replace(hour=_w().DEADLINE_HOUR, minute=0)
        d = d if d.tzinfo else d.astimezone()
        if d <= datetime.datetime.now().astimezone():
            raise ValueError("that deadline has already passed")
        return d

    @staticmethod
    def describe(w: dict) -> str:
        kind = w.get("kind") or "mail"
        if kind != "mail":
            if not w.get("id"):
                return ""
            every = int(w.get("every_minutes") or _w().EVERY[kind])
            pace = f"checked every {every // 60} h" if every % 60 == 0 and every >= 60 else f"checked every {every} min"
            how = "once" if w.get("once") else "every time"
            if kind == "page":
                when_parts = [f"when \"{w['words']}\" appears" if w.get("words") else "",
                              f"when \"{w['gone']}\" disappears" if w.get("gone") else ""]
                when = " or ".join(x for x in when_parts if x) or ("when it changes" if w.get("ask") else
                                                                  "when its text changes")
                ai = f", if the change is: {w['ask']} (an AI check)" if w.get("ask") else ""
                return f"watch #{w['id']}: the web page {w['target']}, {when}{ai}, {how}, {pace}"
            if kind == "search":
                return f"watch #{w['id']}: new web results for \"{w['target']}\" (past week), {how}, {pace}"
            what = f"events matching \"{w['words']}\"" if w.get("words") else "every event"
            return (f"watch #{w['id']}: Google Calendar, {int(w.get('lead_minutes') or _w().DEFAULT_LEAD)} min before "
                    f"{what}, {how}")
        parts = ["every new inbox email (newsletters, promotions and automated notifications skipped)"
                 if w.get("everything") else "",
                 f"sender contains '{w['sender']}'" if w.get("sender") else "",
                 f"subject contains '{w['subject']}'" if w.get("subject") else "",
                 f"mentions '{w['words']}'" if w.get("words") else ""]
        where = {"gmail": "Gmail", "zoho": "Zoho Mail", "both": "Gmail and Zoho Mail"}[w["source"]]
        due = f", follow-up until {w['deadline'][:16].replace('T', ' ')}" if w.get("deadline") else ""
        return (f"watch #{w['id']}: {where}, {' and '.join(p for p in parts if p)}, "
                f"{'once' if w['once'] else 'every time'}{due}") if w.get("id") else ""
