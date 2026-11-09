"""The check (core/watches.py §8)
================================
One pass over every active watch, the follow-up reminder at a deadline, and the messages sent.

Split out of core/watches.py on 2026-11-04; moved into core/watches/ on 2026-11-04.
core/watches/__init__.py re-exports the public names for backwards compatibility.
"""
import builtins
import json
import sys
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import datetime

    from core.database import DatabaseManager
    from core.proactive import Proactive


def _w():
    """core.watches, looked up at call time so a test patching a name on it takes effect."""
    return sys.modules["core.watches"]


class _RunMixin:
    # Interface this mixin requires from the host class (Watches) and its
    # sibling mixins: db and proactive are set by Watches.__init__; list and
    # describe live on _ManageMixin, _matching on _MailMixin, _due_now and
    # _run_other on _ChecksMixin (all earlier in the MRO than this mixin, so
    # these type-only declarations can never shadow a real method). Declared
    # here so mypy can see them.
    db: "DatabaseManager"
    proactive: Optional["Proactive"]

    if TYPE_CHECKING:
        def list(self, include_stopped: bool = False) -> list[dict]: ...

        @staticmethod
        def describe(w: dict) -> str: ...

        def _matching(self, w: dict, keep_bulk: bool = False) -> builtins.list[tuple]: ...
        def _due_now(self, w: dict, now: datetime.datetime) -> bool: ...
        def _run_other(self, w: dict) -> dict | None: ...
    # §8  THE CHECK
    #     One pass over every active watch, the follow-up reminder at a deadline, and the messages sent.

    def due(self) -> bool:
        import datetime
        last = self.db.get_state("watches_last_run", "")
        try:
            t = datetime.datetime.fromisoformat(last)
        except ValueError:
            return True
        t = t if t.tzinfo else t.astimezone()
        return datetime.datetime.now().astimezone() - t >= datetime.timedelta(minutes=_w().WATCH_EVERY_MINUTES)

    def run_check(self) -> builtins.list[dict]:
        """Checks every active watch; returns the messages sent."""
        self.db.set_state("watches_last_run", _w().get_iso_now())
        active = self.list()
        if not active:
            return []
        if self.proactive is not None:
            import datetime
            if self.proactive.quiet(datetime.datetime.now().astimezone()):
                return []                          # held: the next check after quiet hours finds them
        sent = []
        now = datetime.datetime.now().astimezone()
        for w in active:
            if (w.get("kind") or "mail") != "mail":
                # Pages, searches and the calendar: each at its own pace (_due_now)
                if self._due_now(w, now):
                    told = self._run_other(w)
                    if told is not None:
                        sent.append(told)
                continue
            try:
                found = self._matching(w)
                error = None
            except Exception as e:
                found, error = [], str(e)[:200]
                _w().logger.warning(f"watch #{w['id']} check failed: {error}")
            seen = json.loads(w["seen"] or "[]")
            new = [(k, m) for k, m in found if k not in seen]
            if new:
                msg = self._message(w, [m for _, m in new])
                if self.proactive is not None:
                    # urgent: the email he asked to hear about has come; it's never held for later
                    sent.append(self.proactive.deliver("notice", msg, f"watch:{w['id']}", urgent=True))
                seen = [k for k, _ in new] + seen
            active_now = 0 if (new and w["once"]) else w["active"]
            if not new and active_now and w.get("deadline") and self._past(w["deadline"]):
                # A follow-up whose deadline passed with nothing: remind the owner, and it's done
                if self.proactive is not None:
                    sent.append(self.proactive.deliver("notice", self._overdue(w), f"watch:{w['id']}", urgent=True))
                active_now = 0
            with self.db.get_connection() as conn:
                conn.execute("UPDATE watches SET seen = ?, last_check = ?, last_error = ?, matches = matches + ?, "
                             "last_match = COALESCE(?, last_match), active = ? WHERE id = ?",
                             (json.dumps(seen[:_w().SEEN_KEEP]), _w().get_iso_now(), error, len(new),
                              _w().get_iso_now() if new else None, active_now, w["id"]))
        return sent

    @staticmethod
    def _past(deadline: str) -> bool:
        import datetime
        try:
            d = datetime.datetime.fromisoformat(deadline)
        except ValueError:
            return False
        d = d if d.tzinfo else d.astimezone()
        return datetime.datetime.now().astimezone() >= d

    def _overdue(self, w: dict) -> str:
        who = w.get("sender") or w.get("subject") or w.get("words") or "anyone"
        about = f" ({w['note']})" if w.get("note") else ""
        return (f"⏰ Follow-up: nothing from {who} yet{about}. You asked me to check by "
                f"{w['deadline'][:16].replace('T', ' ')}. Want me to draft a nudge, or keep watching?")

    def _message(self, w: dict, emails: builtins.list[dict]) -> str:
        lines = [f"- {m['mailbox']}: {m.get('from_name') or m.get('from')} <{m.get('from')}>: "
                 f"\"{m.get('subject') or '(no subject)'}\" (id {m['id']})" for m in emails[:_w().MAX_PER_MESSAGE]]
        more = f"\n…and {len(emails) - _w().MAX_PER_MESSAGE} more" if len(emails) > _w().MAX_PER_MESSAGE else ""
        end = ("\nThat was your follow-up" + (f" ({w['note']})" if w.get("note") else "") + ": it came in before the "
               "deadline, so it's done." if w.get("deadline") else
               "\nThat was a one-time watch, so it's stopped now." if w["once"]
               else "\nSay \"read it\" to see one, or \"stop that watch\" to end it.")
        n = len(emails)
        return f"Your watch found {n} new email{'s' if n != 1 else ''} ({self.describe(w)}):\n" + "\n".join(lines) + more + end
