"""
Calling Connectors
==================
Connectors (CONNECTORS_DIR, a separate tool) holds the links to the owner's own accounts. It keeps its own
sign-ins (secrets/ in its folder) and the subscription database it fills from the mail. The agent runs it as its
own program and reads the JSON lines it prints; the last line is the result.

- Gmail: important mail, search, one email (with its links), unread counts. Sending, replying and drafts happen
  only from an approval card the owner tapped (interfaces/hud/companion.run_proposal, kind "email").
- Google Calendar (read; add an event from a card, kind "calendar_event"), Contacts and Drive (read).
- Zoho Mail (wecare@example-site.com): read-only.
- Sign-ins from the phone: google_auth_url / google_auth_finish, zoho_connect. The pasted address or Self Client
  details reach here from interfaces/hud/chat.py without ever going to a model (see GOOGLE_SIGNIN, ZOHO_CODE).
- The daemon's mail sync every MAIL_SYNC_MINUTES: new mail into the subscription database; a newly found paid
  subscription is sent to the owner. Mail watches (core/watches.py) use search and zoho_search.
Anything sensitive goes to the tool on stdin (run(stdin=...)), never on its command line, where other programs
and logs could see it. Email, calendar and file content is third-party text: it's shown to the chat model as
untrusted data, never as instructions. Plan and status: docs/CONNECTIONS_PLAN.md.
"""

import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from core.config import CONNECTORS_DIR, get_safe_subprocess_env
except ImportError:
    from config import CONNECTORS_DIR, get_safe_subprocess_env

logger = logging.getLogger("Connectors")
MAIL_SYNC_MINUTES = 30


class ConnectorError(Exception):
    """The tool reported an error (its message is written for the owner). needs_auth: sign in again."""

    def __init__(self, message: str, needs_auth: bool = False):
        super().__init__(message)
        self.needs_auth = needs_auth


def installed() -> bool:
    return (CONNECTORS_DIR / "connectors.py").is_file()


def run(args: List[str], timeout: float = 120, stdin: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Runs `connectors.py <args>` and returns its "done" event; raises ConnectorError on an "error" event."""
    script = CONNECTORS_DIR / "connectors.py"
    if not script.is_file():
        raise ConnectorError(f"Connectors isn't installed at {CONNECTORS_DIR}")
    # Its own secrets/ hold the Google sign-in; the agent's secrets are not passed on
    env = get_safe_subprocess_env()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        res = subprocess.run([sys.executable, str(script), *args], cwd=str(CONNECTORS_DIR), env=env,
                             input=json.dumps(stdin) if stdin is not None else None,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ConnectorError(f"Connectors took longer than {int(timeout)} s")
    final: Optional[Dict[str, Any]] = None
    for line in res.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") in ("done", "error"):
            final = event
    if final is None:
        raise ConnectorError(f"Connectors stopped without a result (exit code {res.returncode})")
    if final.pop("event") == "error":
        raise ConnectorError(final.get("message") or "Connectors failed", bool(final.get("needs_auth")))
    return final


def important(days: int = 7, limit: int = 20) -> Dict[str, Any]:
    return run(["important", "--days", str(days), "--limit", str(limit)])


def search(query: str, limit: int = 10) -> Dict[str, Any]:
    return run(["search", query, "--limit", str(limit)])


def counts() -> Dict[str, Any]:
    """Exact unread numbers from Gmail's counters: inbox, primary (+ primary_at_least), all_mail."""
    return run(["counts"])


# ---------------------------------------------------------------- Google beyond Gmail (docs/archive/CONNECTIONS_PLAN.md)
def google_auth_url() -> str:
    return run(["google-auth-url"])["url"]


def google_auth_finish(pasted: str) -> Dict[str, Any]:
    """The address the owner's phone landed on after approving (it carries a one-time code): on stdin, never in a
    command line or a log."""
    return run(["google-auth-finish"], stdin={"pasted": pasted})



def events(start: Optional[str] = None, days: int = 1, query: str = "") -> List[Dict[str, Any]]:
    args = ["events", "--days", str(days)] + (["--start", start] if start else []) + (["--query", query] if query else [])
    return run(args)["events"]


def add_event(event: Dict[str, Any]) -> Dict[str, Any]:
    return run(["add-event"], stdin=event)


# The owner's lists (Google Tasks). Adding and checking off happen right away (the owner's choice, 2026-10-11).
def task_lists() -> List[Dict[str, Any]]:
    return run(["lists"])["lists"]


def tasks(list_name: str = "", done: bool = False) -> Dict[str, Any]:
    return run(["tasks"] + (["--list", list_name] if list_name else []) + (["--done"] if done else []))


def add_task(title: str, list_name: str = "", due: Optional[str] = None) -> Dict[str, Any]:
    return run(["add-task"], stdin={"title": title, "list": list_name, **({"due": due} if due else {})})


def complete_task(title: str, list_name: str = "") -> Dict[str, Any]:
    return run(["complete-task"], stdin={"title": title, "list": list_name})


def contacts(query: str) -> List[Dict[str, Any]]:
    return run(["contacts", query])["contacts"]


def drive_search(query: str) -> List[Dict[str, Any]]:
    return run(["drive-search", query])["files"]


def drive_read(file_id: str) -> Dict[str, Any]:
    return run(["drive-read", file_id])


def send_email(email: Dict[str, Any], draft: bool = False) -> Dict[str, Any]:
    return run(["send-email"] + (["--draft"] if draft else []), stdin=email)


# ---------------------------------------------------------------- GitHub, read-only (DeskAI's repo browser)
def github_repos() -> List[Dict[str, Any]]:
    return run(["github-repos"], timeout=60)["repos"]


def github_tree(repo: str, path: str = "") -> Dict[str, Any]:
    return run(["github-tree", repo, "--path", path], timeout=60)


def github_file(repo: str, path: str) -> Dict[str, Any]:
    return run(["github-file", repo, path], timeout=60)


def github_download(repo: str, path: str, fmt: str, out: str) -> Dict[str, Any]:
    """A file or folder written to `out` for sharing: fmt "raw", "zip" or "text" (a file is always itself).
    Returns {"name", "files", "skipped", "bytes", "out"}."""
    return run(["github-download", repo, "--path", path, "--format", fmt, "--out", out], timeout=300)


# ---------------------------------------------------------------- Zoho Mail, read-only (CONNECTIONS_PLAN part 2)
def zoho_connect(pasted: str) -> Dict[str, Any]:
    """The Self Client's Client ID, Secret and one-time code the owner pasted: on stdin, never logged."""
    return run(["zoho-connect"], stdin={"pasted": pasted})


def zoho_status() -> Dict[str, Any]:
    return run(["zoho-status"])


def zoho_recent(limit: int = 10, unread: bool = False) -> List[Dict[str, Any]]:
    return run(["zoho-recent", "--limit", str(limit)] + (["--unread"] if unread else []))["emails"]


def zoho_search(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    return run(["zoho-search", query, "--limit", str(limit)])["emails"]


def zoho_read(message_id: str, max_chars: int = 2500) -> Dict[str, Any]:
    return run(["zoho-read", message_id, "--max-chars", str(max_chars)])


def read(message_id: str, max_chars: int = 2500) -> Dict[str, Any]:
    return run(["read", message_id, "--max-chars", str(max_chars)])


def subscriptions(kind: str = "both", limit: int = 40) -> Dict[str, Any]:
    return run(["subscriptions", "--kind", kind, "--limit", str(limit)])


def sync(timeout: float = 1800) -> Dict[str, Any]:
    return run(["sync"], timeout=timeout)


# ---------------------------------------------------------------------------------------------- the mail sync

def mail_sync_due(db) -> bool:
    if not installed():
        return False
    last = db.get_state("mail_sync_last", "")
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(minutes=MAIL_SYNC_MINUTES)
    except ValueError:
        return True


def run_mail_sync(db, notify=None) -> Optional[Dict[str, Any]]:
    """One sync. Newly found paid subscriptions go to the owner via notify(text); a lost sign-in is reported
    once (not every half hour) until a sync works again."""
    db.set_state("mail_sync_last", datetime.now(timezone.utc).isoformat())
    try:
        result = sync()
    except ConnectorError as e:
        logger.warning(f"Mail sync failed: {e}")
        if e.needs_auth and db.get_state("mail_sync_auth_warned", "") != "1":
            db.set_state("mail_sync_auth_warned", "1")
            if notify:
                notify("📧 Gmail isn't connected any more (Google refused the saved sign-in), so the subscription "
                       "tracking has stopped. Run `python connectors.py auth` in C:\\Projects\\Connectors to "
                       "connect it again.")
        return None
    db.set_state("mail_sync_auth_warned", "0")
    new_paid = result.get("new_paid") or []
    if new_paid and notify and not result.get("first_sync"):
        notify("📧 New subscription spotted in your email: " + ", ".join(new_paid) +
               ". Ask me \"what am I subscribed to?\" for the details.")
    return result
