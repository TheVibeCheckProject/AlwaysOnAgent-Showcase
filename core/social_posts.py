"""
Calling SocialPostEngine
========================
SocialPostEngine (SOCIALPOSTENGINE_DIR, a separate tool) posts Example Site's pins to Pinterest
through the official API. It holds its own sign-in, the pins and the record of what's gone out, and checks a
board before posting so nothing goes up twice. The agent runs it as its own program and reads the JSON lines it
prints: progress events go to the task card, and the last line is the result.
"""

import datetime
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from core.config import SOCIALPOSTENGINE_DIR, get_safe_subprocess_env
except ImportError:
    from config import SOCIALPOSTENGINE_DIR, get_safe_subprocess_env

logger = logging.getLogger("SocialPostEngine")
MAX_PINS_PER_RUN = 5              # a steady trickle; Pinterest favours regular posting over bursts


class SocialPostError(Exception):
    """The tool reported an error (its message is written for the owner)."""


def run(args: List[str], on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_start: Optional[Callable[[int], None]] = None, timeout: float = 600) -> Dict[str, Any]:
    """
    Runs `socialpostengine.py <args>`, passing progress events to on_progress and the process id to on_start.
    Returns the final "done" event; raises SocialPostError with the tool's own message on an "error" event,
    or when it ends without a result.
    """
    script = SOCIALPOSTENGINE_DIR / "socialpostengine.py"
    if not script.is_file():
        raise SocialPostError(f"SocialPostEngine isn't installed at {SOCIALPOSTENGINE_DIR}")
    # Its own .env and secrets/ hold the Pinterest app and sign-in; the agent's secrets are not passed on
    env = get_safe_subprocess_env()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, str(script), *args], cwd=str(SOCIALPOSTENGINE_DIR), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if on_start:
        on_start(proc.pid)
    final: Optional[Dict[str, Any]] = None
    try:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.pop("event", "")
            if kind == "progress":
                if on_progress:
                    try:
                        on_progress(event)
                    except Exception as e:           # progress is cosmetic
                        logger.debug(f"progress callback failed: {e}")
            elif kind in ("done", "error"):
                final = {"event": kind, **event}
        proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
    if final is None:
        raise SocialPostError(f"SocialPostEngine stopped without a result (exit code {proc.returncode})")
    if final["event"] == "error":
        raise SocialPostError(final.get("message") or "SocialPostEngine failed")
    final.pop("event")
    return final


def post_pins(count: int = 1, **callbacks) -> Dict[str, Any]:
    """Posts the next `count` pins (1 to MAX_PINS_PER_RUN) to their boards."""
    count = max(1, min(int(count or 1), MAX_PINS_PER_RUN))
    return run(["post", "--count", str(count)], **callbacks)


def status() -> Dict[str, Any]:
    return run(["status"], timeout=120)


# ------------------------------------------------------------------ the pin maker (SocialPostEngine pinmaker.py)
# New pins from the website's own messages, drafts first: the owner approves from a numbered preview sheet in the
# chat (2026-10-10, the owner: "a sophisticated pin maker ... shows the website link well"). When the queue runs
# low, the agent makes a batch by itself once a day and sends the preview (TOPUP_*).
TOPUP_BELOW = 8                   # fewer pins than this waiting: make a batch of drafts
TOPUP_BATCH = 8
TOPUP_HOURS = (10, 20)            # when a top-up may run (its message shouldn't arrive at night)
STATE_TOPUP_DAY = "pins_topup_day"


def _publish_preview(result: Dict[str, Any], images_dir: Optional[Path]) -> Dict[str, Any]:
    """Copies the tool's preview sheet into the agent's images folder (served at /images/, shown in the chat)."""
    src = Path(result.get("preview") or "")
    if images_dir is not None and src.is_file():
        images_dir.mkdir(parents=True, exist_ok=True)
        name = f"pin-drafts-{datetime.datetime.now():%Y%m%d-%H%M%S}.jpg"
        shutil.copyfile(src, images_dir / name)
        result["preview_file"] = name
    return result


def make_pins(count: int = TOPUP_BATCH, images_dir: Optional[Path] = None) -> Dict[str, Any]:
    n = max(1, min(int(count or TOPUP_BATCH), 16))
    return _publish_preview(run(["make", "--count", str(n)], timeout=900), images_dir)


def pin_drafts(images_dir: Optional[Path] = None) -> Dict[str, Any]:
    return _publish_preview(run(["drafts"], timeout=300), images_dir)


def approve_pins(which: str) -> Dict[str, Any]:
    return run(["approve", "--pins", which], timeout=120)


def reject_pins(which: str) -> Dict[str, Any]:
    return run(["reject", "--pins", which], timeout=120)


def waiting_drafts() -> int:
    """How many pin drafts wait for the owner, read from the tool's drafts file (no program run: it's for every
    chat reply's context)."""
    try:
        data = json.loads((SOCIALPOSTENGINE_DIR / "data" / "pin_drafts" / "drafts.json").read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else 0
    except (OSError, ValueError):
        return 0


def chat_note() -> str:
    """For the chat's context while drafts wait (2026-10-10: the owner's "approve all" got a Notepad task card,
    because the drafts message wasn't in the conversation and the recent tasks were Notepad ones)."""
    n = waiting_drafts()
    if not n:
        return ""
    return (f"- Pinterest pin drafts waiting for the owner's approval: {n} (numbered 1-{n} in the preview). A message "
            f"like 'approve all', 'approve 1, 3' or 'reject 2' is about these: use approve_pins / reject_pins.")


def drafts_message(result: Dict[str, Any], why: str = "") -> str:
    """The drafts for the owner: the preview sheet and a numbered list, and how to answer."""
    drafts = result.get("drafts") or []
    if not drafts:
        return "No pin drafts are waiting."
    lines = [f"🧷 {why + ' ' if why else ''}{len(drafts)} pin draft{'s' if len(drafts) != 1 else ''} to look at:"]
    if result.get("preview_file"):
        lines.append(f"\n![Pin drafts](/images/{result['preview_file']})\n")
    lines += [f"{d['n']}. {d['title']} ({d['board']})" for d in drafts]
    lines.append("\nSay \"approve all\", \"approve 1, 3\" or \"reject 2\". Nothing posts until you approve.")
    return "\n".join(lines)


def topup_due(db, now: Optional[datetime.datetime] = None) -> bool:
    now = now or datetime.datetime.now().astimezone()
    return TOPUP_HOURS[0] <= now.hour < TOPUP_HOURS[1] and db.get_state(STATE_TOPUP_DAY, "") != now.date().isoformat()


def run_topup(db, proactive, log=logger, images_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Once a day: when fewer than TOPUP_BELOW pins wait and no drafts do, makes a batch and sends the preview."""
    db.set_state(STATE_TOPUP_DAY, datetime.datetime.now().astimezone().date().isoformat())
    try:
        left = int(status().get("pins_left") or 0)
        if left >= TOPUP_BELOW or (run(["drafts"], timeout=300).get("drafts") or []):
            return None
        result = make_pins(TOPUP_BATCH, images_dir)
    except SocialPostError as e:
        log.warning(f"Pin top-up failed: {e}")
        return None
    if result.get("made") and proactive is not None:
        proactive.deliver("suggestion", drafts_message(result, why=f"Only {left} pins are left to post, so I made"),
                          "pin-drafts")
    log.info(f"Pin top-up: {left} pins left, made {result.get('made', 0)} drafts")
    return result


def summary(result: Dict[str, Any]) -> str:
    """One line for the task result and the owner's notification."""
    posted, skipped = result.get("posted") or [], result.get("skipped") or []
    parts = []
    if posted:
        names = ", ".join(f"{p.get('title', p.get('id'))} ({p.get('board')})" for p in posted)
        parts.append(f"Posted {len(posted)} pin{'s' if len(posted) != 1 else ''}"
                     f"{' to the sandbox (practice, only you see it)' if result.get('env') == 'sandbox' else ''}: {names}")
    if skipped:
        parts.append(f"skipped {len(skipped)} already on the board")
    if not posted and not skipped:
        parts.append(result.get("message") or "Nothing to post")
    left = result.get("left")
    return "; ".join(parts) + (f". {left} pins left." if left is not None else ".")
