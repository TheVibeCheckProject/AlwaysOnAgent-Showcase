"""Mail checks (core/watches.py §6)
================================
One plain search per mailbox (no AI); filters the search can't do are checked on the results.

Split out of core/watches.py on 2026-11-04; moved into core/watches/ on 2026-11-04.
core/watches/__init__.py re-exports the public names for backwards compatibility.
"""
import sys
from typing import Any


def _w():
    """core.watches, looked up at call time so a test patching a name on it takes effect."""
    return sys.modules["core.watches"]


class _MailMixin:
    # Interface this mixin requires from the host class (Watches):
    # connectors is set by Watches.__init__. Annotation-only declaration
    # so mypy sees it; no runtime value is set here, so nothing is shadowed.
    connectors: Any
    # §6  MAIL CHECKS
    #     One plain search per mailbox (no AI); filters the search can't do are checked on the results.

    def _matching(self, w: dict, keep_bulk: bool = False) -> list[tuple]:
        """[(seen key, email)] for every email that matches now (newest first), in each of the watch's mailboxes.
        An every-email watch skips bulk mail unless keep_bulk (when marking what's already there as seen)."""
        out = []
        everything = bool(w.get("everything"))
        if w["source"] in ("gmail", "both"):
            q = " ".join(p for p in (_w().GMAIL_INBOX if everything else "",
                                     f"from:({w['sender']})" if w["sender"] else "",
                                     f"subject:({w['subject']})" if w["subject"] else "",
                                     w["words"], "newer_than:2d") if p)
            for m in self.connectors.search(q, limit=20).get("emails", []):
                out.append((f"gmail:{m['id']}", {**m, "mailbox": "Gmail"}))
        if w["source"] in ("zoho", "both"):
            if everything:
                # The Inbox folder's newest, every filter checked here (Zoho's search can't limit to a folder)
                key, found = "", self.connectors.zoho_recent(20, folder=_w().ZOHO_INBOX)
            else:
                # Zoho's search takes one field; the others are checked here
                key = (f"sender:{w['sender']}" if w["sender"] else f"subject:{w['subject']}" if w["subject"]
                       else w["words"])
                found = self.connectors.zoho_search(key, limit=20)
            for m in found:
                if self._fits(w, m, skip=key):
                    out.append((f"zoho:{m['id']}", {**m, "mailbox": "Zoho"}))
        if everything and not keep_bulk:
            out = [(k, m) for k, m in out if not _w().looks_bulk(m)]
        return out

    @staticmethod
    def _fits(w: dict, m: dict, skip: str) -> bool:
        who = f"{m.get('from', '')} {m.get('from_name', '')}".lower()
        text = f"{m.get('subject', '')} {m.get('snippet', '')}".lower()
        if w["sender"] and w["sender"].lower() not in who:
            return False
        if w["subject"] and w["subject"].lower() not in (m.get("subject") or "").lower():
            return False
        return not (w["words"] and skip != w["words"] and not all(x in text for x in w["words"].lower().split()))
