"""Bulk mail (core/watches.py §2)
===============================
looks_bulk(): a newsletter, promotion or automated notification rather than a person writing.

Split out of core/watches.py on 2026-11-04; moved into core/watches/ on 2026-11-04.
core/watches/__init__.py re-exports the public names for backwards compatibility.
"""
import sys


def _w():
    """core.watches, looked up at call time so a test patching a name on it takes effect."""
    return sys.modules["core.watches"]


def looks_bulk(m: dict) -> bool:
    """A newsletter, promotion or automated notification rather than a person writing (the sender's address, then
    the subject and preview)."""
    local, _, domain = str(m.get("from") or "").lower().partition("@")
    if _w().BULK_SENDER.search(local):
        return True
    labels = domain.split(".")
    if len(labels) >= 3 and labels[0] in _w().BULK_SUBDOMAIN:
        return True
    return bool(_w().BULK_TEXT.search(f"{m.get('subject') or ''} {m.get('snippet') or ''}"))
