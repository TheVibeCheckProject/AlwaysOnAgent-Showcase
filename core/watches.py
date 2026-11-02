"""
Watches: "let me know whenever Zoho gets a Pinterest email"
==========================================================
A watch is a saved mail search (Gmail, Zoho, or both) matched on sender, subject and/or words. Every
WATCH_EVERY_MINUTES the daemon runs each active watch as a plain search (no AI, no quota) and sends the owner
a message with a push for each email it hasn't told them about yet. "once" watches stop after their first match.
A follow-up is a once watch with a deadline ("if the landlord hasn't replied by Friday, remind me"): a match before
the deadline is told as usual and ends it; at the deadline with no match, the owner gets a reminder and it ends.

- When a watch is made, the emails that already match are marked seen: it tells about new mail only.
- Quiet hours (the proactive setting) hold the messages; the search window (2 days) catches them up after.
- Email text is other people's words: a match message shows only the sender, subject and the email's id.
Design: docs/WATCHES.md.
"""

import json
import logging
from typing import Dict, List, Optional

try:
    from core.database import get_iso_now
except ImportError:
    from database import get_iso_now

logger = logging.getLogger("Watches")

WATCH_EVERY_MINUTES = 5
SOURCES = ("gmail", "zoho", "both")
SEEN_KEEP = 300                 # the newest seen ids kept per watch (the search window is 2 days)
MAX_PER_MESSAGE = 5
DEADLINE_HOUR = 17             # a deadline given as a date alone means 5 pm that day


class Watches:
    def __init__(self, db, connectors_mod=None, proactive=None):
        self.db, self.proactive = db, proactive
        if connectors_mod is None:
            from core import connectors as connectors_mod
        self.connectors = connectors_mod
        with db.get_connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, sender TEXT, subject TEXT, words TEXT,
                once INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, seen TEXT NOT NULL DEFAULT '[]',
                matches INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_check TEXT, last_match TEXT,
                last_error TEXT)""")
            cols = {r[1] for r in conn.execute("PRAGMA table_info(watches)")}
            if "deadline" not in cols:            # follow-ups (2026-10-11)
                conn.execute("ALTER TABLE watches ADD COLUMN deadline TEXT")
                conn.execute("ALTER TABLE watches ADD COLUMN note TEXT")

    # ------------------------------------------------------------------------------------------- the store
    def add(self, source: str, sender: str = "", subject: str = "", words: str = "", once: bool = False,
            deadline: str = "", note: str = "") -> Dict:
        """Saves a watch and marks what already matches as seen. With a deadline it's a follow-up (once, and a
        reminder if nothing came by then; note: what the owner is waiting on). Raises ValueError (bad input) or the
        connector's error (not connected), so the chat can say why."""
        due_at = self.parse_deadline(deadline) if (deadline or "").strip() else None
        if due_at is not None:
            once = True
        source = (source or "both").strip().lower()
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        sender, subject, words = ((v or "").strip()[:80] for v in (sender, subject, words))
        if not (sender or subject or words):
            raise ValueError("a watch needs a sender, a subject or words to match")
        w = {"source": source, "sender": sender, "subject": subject, "words": words, "once": int(bool(once))}
        seen = [key for key, _ in self._matching(w)]              # raises if the mailbox isn't connected
        with self.db.get_connection() as conn:
            cur = conn.execute("INSERT INTO watches (source, sender, subject, words, once, seen, created_at, last_check, "
                               "deadline, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (source, sender, subject, words, w["once"], json.dumps(seen[:SEEN_KEEP]),
                                get_iso_now(), get_iso_now(), due_at.isoformat() if due_at else None,
                                (note or "").strip()[:200] or None))
        return self.get(cur.lastrowid)

    def get(self, watch_id: int) -> Optional[Dict]:
        r = self.db.get_connection().execute("SELECT * FROM watches WHERE id = ?", (int(watch_id),)).fetchone()
        return dict(r) if r else None

    def list(self, include_stopped: bool = False) -> List[Dict]:
        where = "" if include_stopped else "WHERE active = 1"
        return [dict(r) for r in self.db.get_connection().execute(f"SELECT * FROM watches {where} ORDER BY id")]

    def change(self, watch_id: int, sender: Optional[str] = None, subject: Optional[str] = None,
               words: Optional[str] = None, once: Optional[bool] = None) -> Dict:
        """Narrows or changes an active watch in place (2026-10-10: the owner asked twice to keep only Pinterest's
        upgrade email; with no way to change a watch the chat said it had, and nothing changed). Emails that match
        the new filter now are marked seen. Raises ValueError for an unknown watch or an empty filter."""
        w = self.get(watch_id)
        if not w or not w["active"]:
            raise ValueError(f"no active watch #{watch_id}")
        for key, value in (("sender", sender), ("subject", subject), ("words", words)):
            if value is not None:
                w[key] = value.strip()[:80]
        if once is not None:
            w["once"] = int(bool(once))
        if not (w["sender"] or w["subject"] or w["words"]):
            raise ValueError("a watch needs a sender, a subject or words to match")
        seen = [key for key, _ in self._matching(w)] + json.loads(w["seen"] or "[]")
        with self.db.get_connection() as conn:
            conn.execute("UPDATE watches SET sender = ?, subject = ?, words = ?, once = ?, seen = ? WHERE id = ?",
                         (w["sender"], w["subject"], w["words"], w["once"],
                          json.dumps(list(dict.fromkeys(seen))[:SEEN_KEEP]), int(watch_id)))
        return self.get(watch_id)

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
            d = d.replace(hour=DEADLINE_HOUR, minute=0)
        d = d if d.tzinfo else d.astimezone()
        if d <= datetime.datetime.now().astimezone():
            raise ValueError("that deadline has already passed")
        return d

    @staticmethod
    def describe(w: Dict) -> str:
        parts = [f"sender contains '{w['sender']}'" if w.get("sender") else "",
                 f"subject contains '{w['subject']}'" if w.get("subject") else "",
                 f"mentions '{w['words']}'" if w.get("words") else ""]
        where = {"gmail": "Gmail", "zoho": "Zoho Mail", "both": "Gmail and Zoho Mail"}[w["source"]]
        due = f", follow-up until {w['deadline'][:16].replace('T', ' ')}" if w.get("deadline") else ""
        return (f"watch #{w['id']}: {where}, {' and '.join(p for p in parts if p)}, "
                f"{'once' if w['once'] else 'every time'}{due}") if w.get("id") else ""

    # ------------------------------------------------------------------------------------------- searching
    def _matching(self, w: Dict) -> List[tuple]:
        """[(seen key, email)] for every email that matches now (newest first), in each of the watch's mailboxes."""
        out = []
        if w["source"] in ("gmail", "both"):
            q = " ".join(p for p in (f"from:({w['sender']})" if w["sender"] else "",
                                     f"subject:({w['subject']})" if w["subject"] else "",
                                     w["words"], "newer_than:2d") if p)
            for m in self.connectors.search(q, limit=20).get("emails", []):
                out.append((f"gmail:{m['id']}", {**m, "mailbox": "Gmail"}))
        if w["source"] in ("zoho", "both"):
            # Zoho's search takes one field; the others are checked here
            key = (f"sender:{w['sender']}" if w["sender"] else f"subject:{w['subject']}" if w["subject"]
                   else w["words"])
            for m in self.connectors.zoho_search(key, limit=20):
                if self._fits(w, m, skip=key):
                    out.append((f"zoho:{m['id']}", {**m, "mailbox": "Zoho"}))
        return out

    @staticmethod
    def _fits(w: Dict, m: Dict, skip: str) -> bool:
        who = f"{m.get('from', '')} {m.get('from_name', '')}".lower()
        text = f"{m.get('subject', '')} {m.get('snippet', '')}".lower()
        if w["sender"] and w["sender"].lower() not in who:
            return False
        if w["subject"] and w["subject"].lower() not in (m.get("subject") or "").lower():
            return False
        if w["words"] and skip != w["words"] and not all(x in text for x in w["words"].lower().split()):
            return False
        return True

    # ------------------------------------------------------------------------------------------- the check
    def due(self) -> bool:
        import datetime
        last = self.db.get_state("watches_last_run", "")
        try:
            t = datetime.datetime.fromisoformat(last)
        except ValueError:
            return True
        t = t if t.tzinfo else t.astimezone()
        return datetime.datetime.now().astimezone() - t >= datetime.timedelta(minutes=WATCH_EVERY_MINUTES)

    def run_check(self) -> List[Dict]:
        """Checks every active watch; returns the messages sent."""
        self.db.set_state("watches_last_run", get_iso_now())
        active = self.list()
        if not active:
            return []
        if self.proactive is not None:
            import datetime
            if self.proactive.quiet(datetime.datetime.now().astimezone()):
                return []                          # held: the next check after quiet hours finds them
        sent = []
        for w in active:
            try:
                found = self._matching(w)
                error = None
            except Exception as e:
                found, error = [], str(e)[:200]
                logger.warning(f"watch #{w['id']} check failed: {error}")
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
                             (json.dumps(seen[:SEEN_KEEP]), get_iso_now(), error, len(new),
                              get_iso_now() if new else None, active_now, w["id"]))
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

    def _overdue(self, w: Dict) -> str:
        who = w.get("sender") or w.get("subject") or w.get("words")
        about = f" ({w['note']})" if w.get("note") else ""
        return (f"⏰ Follow-up: nothing from {who} yet{about}. You asked me to check by "
                f"{w['deadline'][:16].replace('T', ' ')}. Want me to draft a nudge, or keep watching?")

    def _message(self, w: Dict, emails: List[Dict]) -> str:
        lines = [f"- {m['mailbox']}: {m.get('from_name') or m.get('from')} <{m.get('from')}>: "
                 f"\"{m.get('subject') or '(no subject)'}\" (id {m['id']})" for m in emails[:MAX_PER_MESSAGE]]
        more = f"\n…and {len(emails) - MAX_PER_MESSAGE} more" if len(emails) > MAX_PER_MESSAGE else ""
        end = ("\nThat was your follow-up" + (f" ({w['note']})" if w.get("note") else "") + ": it came in before the "
               "deadline, so it's done." if w.get("deadline") else
               "\nThat was a one-time watch, so it's stopped now." if w["once"]
               else "\nSay \"read it\" to see one, or \"stop that watch\" to end it.")
        n = len(emails)
        return f"Your watch found {n} new email{'s' if n != 1 else ''} ({self.describe(w)}):\n" + "\n".join(lines) + more + end
