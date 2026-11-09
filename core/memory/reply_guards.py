"""
Plain-code reply guards for chat responses
==========================================
Checks every reply after the model writes it and before it's saved or shown:
  - unbacked_claim_note   it says it changed, posted or applied something, and no tool did: an honest note
  - keep_promise          it promises to look later ("one moment"): the background helper is really started
  - ensure_media_shown    a file the tools found is really shown, and made-up media links are fixed
  - tools_ran_note        every model failed after tools ran: says what was done instead of nothing
  - trim_tail_question    a closing question right after another one is dropped
"""
import re
import urllib.parse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

# A reply that says it changed something the user set up, when no tool that could have done it ran (2026-10-10:
# "I've muted those completely" and "I'll ignore the general recommendations" about a mail watch, twice, with no
# watch tool called; the watch kept alerting). The reply gets an honest note instead of being trusted.
CLAIMS = (
    (re.compile(r"\b(muted|unmuted|stopped|paused|changed|updated|narrowed|adjusted|filtered|turned off|set up|"
                r"won'?t (ping|alert|notify|tell) you|will only (ping|alert|notify|tell) you|i'?ll ignore|ignoring)\b"
                r"[^.!?\n]{0,90}\b(watch|e-?mails?|alerts?|notifications?|pings?|digests?|recommendations)\b|"
                r"\b(watch|alerts?)\b[^.!?\n]{0,60}\b(muted|stopped|changed|updated|narrowed)\b", re.IGNORECASE),
     {"watch_mail", "change_watch", "stop_watch", "proactive_settings", "create_schedule", "change_schedule"},
     ("I didn't actually change any watch or alert just now: nothing was saved. Say what to watch for (or "
     "\"stop the watch\") and I'll do it.")),
    # 2026-10-11: "Here is the card to text Sam" when the text tool had failed: no card existed
    (re.compile(r"\b(here('?s| is) (the|a|your) (card|text|draft)|tap (below|the card|it)\b|card (below|above)|"
                r"i'?ve (made|put|prepared|added) (a|the) card)", re.IGNORECASE),
     # offer_go_live and the task starters show a card too (until 2026-10-13 they were missing here, so the
     # apply card for task-189 came with "There's no card this time" under it, twice)
     {"propose_text", "propose_call", "propose_email", "propose_event", "propose_task", "propose_project",
      "post_video_to_tiktok", "post_pins", "computer_task", "offer_go_live", "start_project_job", "render_video"},
     "There's no card this time: nothing was proposed. Ask again and I'll make one."),
)

# A reply that says an update or change went live (2026-10-13: "Yes, it worked! Task-189 is fully applied and
# merged into live" when nothing had been applied: only the owner's tap on its card applies one). Checked
# against the go-live record (core/self_update), not against which tools ran: no tool applies anything.
LIVE_CLAIM = re.compile(
    r"\b(task-\d+|update|changes?|branch|code|worker|feature)\b[^.!?\n]{0,80}\b(applied|merged|deployed|"
    r"(is|are|now|went|gone) live|in live|into live)\b|"
    r"\b(applied|merged|deployed)\b[^.!?\n]{0,60}\b(task-\d+|update|changes?|branch|into live|to live)\b", re.IGNORECASE)
LIVE_NOTE = ("That isn't right: nothing has been applied. An update goes live only when you tap ⚡ Apply update "
             "(or 🚀 Push live) on its card, and a notification tells you how it went.")
TASK_ID = re.compile(r"\btask-(\d+)\b", re.IGNORECASE)

# A reply that claims background research happened (Step 1.11). Checked against the away shift session
# record and candidate pool, or backed by a successful background_status tool call.
BACKGROUND_CLAIM = re.compile(
    r"\b(in the background|while you were away|during (the|my) away shift|my background (work|research)|"
    r"background session)\b[^.!?\n]{0,80}\b(researched|looked into|investigated|searched for|checked on|found)\b|"
    r"\b(researched|looked into|investigated|searched for|checked on|found)\b[^.!?\n]{0,60}\b(in the background|while you were away|during (the|my) away shift)\b",
    re.IGNORECASE)
BACKGROUND_NOTE = ("I didn't actually run background research on that: no away shift session recorded it. "
                   "I can research it now if you'd like.")

# A reply that promises to do something after it ends (2026-10-26: "I'll pull those files for you right now. One
# moment!", "Let me fix that right now ... Hang tight", then nothing ran: a reply ends the turn). Kept by starting
# the background helper (core/follow_up.py) unless the reply already started something.
# "Hang/hold tight" counts only as "wait while I do it": the agent also says it for "you wait until you're ready"
# and "I'll hold tight on that" (2026-10-27/31), which started a helper on nothing and sent an empty notice.
PROMISE = re.compile(
    r"\b(one moment|bear with me|stand by|give me (a|one) (sec|second|moment|minute))\b|"
    r"\b(hang|hold) tight\b(?=[,.!—-]?\s*(a |one |just a )?(sec|second|moment|minute|while i|as i|i'?m (checking|"
    r"looking|pulling|grabbing|finding)))|"
    r"\b(let me|i'?ll|i will|i'?m going to)\s+(quickly\s+)?(check|look|find|pull|dig|search|fix|grab|track|locate|"
    r"fetch|investigate)\b[^.!?\n]{0,80}\b(right now|now|real quick|and (get|report) back|and let you know)\b|"
    r"\blet'?s (do a (quick )?)?(search|look|check|dig)\b[^.!?\n]{0,80}\b(in the background|right now|now)\b", re.IGNORECASE)
PROMISE_TAIL = 220          # characters at the end of a reply where a broken promise is looked for
# Tools that keep a promise themselves: a watch or schedule really does "check and let you know" later
PROMISE_BACKED_BY = {"look_into", "propose_task", "computer_task", "start_project_job", "render_video",
                     "research_ai_opportunities", "watch_mail", "change_watch", "create_schedule", "change_schedule",
                     "proactive_settings", "propose_text", "propose_call", "propose_email", "propose_event",
                     "propose_project", "post_video_to_tiktok", "post_pins", "offer_go_live"}
PROMISE_STARTED_NOTE = "I've started looking into it in the background and will message you here with what I find."
PROMISE_FAILED_NOTE = "Nothing is actually running on that yet: ask me to look into it and I'll do it in the background."


# A file the tools made ready to show (mini_files show, computer_screenshots): the app draws it from this line
MEDIA_LINE = re.compile(r"^!?\[[^\]\n]*\]\(/api/(files/m|computer/runs)/[^)\s]+\)$", re.MULTILINE)
# What a model wrote instead of the line (2026-10-27: "show C:\Projects\TiktokVideos\videos\vibe_check_….mp4")
SHOW_COMMAND = re.compile(r"^\s*`?show\s+[A-Za-z]:\\[^\n]*$", re.MULTILINE | re.IGNORECASE)


MEDIA_AS_IMAGE = re.compile(r"!\[([^\]\n]*)\]\((\S+?\.(mp4|mov|mkv|webm|avi|m4v|mp3|wav|m4a|ogg|flac|aac|pdf))\)", re.IGNORECASE)
MEDIA_ICON = {"mp3": "🔊", "wav": "🔊", "m4a": "🔊", "ogg": "🔊", "flac": "🔊", "aac": "🔊", "pdf": "📄"}
# A media card whose address the server doesn't serve: a model wrote one from memory without calling the tool
# (2026-10-27 18:06: "[▶ x.mp4](/videos/x.mp4)", a card that could never load)
MEDIA_MADE_UP = re.compile(r"\[[^\]\n]*\]\((?!/api/|https?://)([^)\s]*?([^/\\)\s]+\.(mp4|mov|mkv|webm|avi|m4v|mp3|wav|"
                           r"m4a|ogg|flac|aac|pdf)))\)", re.IGNORECASE)


MEDIA_LOOKUPS = 2          # made-up media links looked up on the Mini per reply (each is a disk walk of up to 6 s)


def _real_media_line(m: "re.Match", budget: list[int] | None = None) -> str:
    """The line that really shows the file a made-up link names (found on the Mini by its name), else its name. Only
    MEDIA_LOOKUPS per reply (budget), and only when exactly one file has that name: each look-up walked the disk for up
    to 6 s, and a common name ("output.mp4") showed whichever file came first (2026-10-28 audit 0.9)."""
    name = urllib.parse.unquote(m.group(2))
    if budget is not None:
        if budget[0] <= 0:
            return f"{name} (not looked up)"
        budget[0] -= 1
    try:
        from core import mini_files
    except ImportError:
        import mini_files  # type: ignore[no-redef]
    try:
        same = [h for h in mini_files.find(name, limit=5) if Path(h["path"]).name.lower() == name.lower()]
        if len(same) == 1:
            return mini_files.show_line(same[0]["path"])
        if len(same) > 1:
            return f"{name} (several files on the PC have that name: ask me to show one by its folder)"
    except (OSError, KeyError, ValueError):
        pass
    return f"{name} (I couldn't find that file on the PC)"


def ensure_media_shown(reply: str, trace: Any, limit: int = 6) -> str:
    """The reply with every media line the tools produced, appended when the model left it out. Showing a file must
    not depend on a model copying a line exactly: on 2026-10-27 a fallback model wrote "show C:\\…\\x.mp4" instead,
    and the video never appeared. And a video, audio file or PDF written as a picture ("![x.mp4](/videos/x.mp4)", the
    same evening: a blank card, "unable to load image stream") becomes the card that plays it."""
    reply = MEDIA_AS_IMAGE.sub(lambda m: f"[{MEDIA_ICON.get(m.group(3).lower(), '▶')} {m.group(1) or m.group(2)}]"
                                         f"({m.group(2)})", reply or "")
    budget = [MEDIA_LOOKUPS]
    reply = MEDIA_MADE_UP.sub(lambda m: _real_media_line(m, budget), reply)
    lines: list[str] = []
    for t in trace or []:
        if isinstance(t, dict) and t.get("name") in ("mini_files", "computer_screenshots"):
            lines += [m.group(0) for m in MEDIA_LINE.finditer(str(t.get("result") or ""))]
    missing = [ln for ln in dict.fromkeys(lines) if ln not in (reply or "")][:limit]
    if not missing:
        return reply
    text = SHOW_COMMAND.sub("", reply or "").rstrip()
    return (text + "\n\n" if text else "") + "\n".join(missing)


def tools_ran_note(done: list[dict[str, Any]], limit: int = 6) -> str:
    """The reply when every model failed after tools had acted: what was done, from the tools' own results."""
    lines = []
    for t in done[:limit]:
        first = next((ln.strip() for ln in str(t.get("result") or "").splitlines() if ln.strip()
                      and not ln.strip().startswith("[")), "")       # skip "[Web content: ...]"-style labels
        lines.append(f"- {t.get('name')}: {first[:160]}" if first else f"- {t.get('name')}")
    return ("The AI services stopped answering before I could finish my reply. What I did this turn:\n"
            + "\n".join(lines) + "\nAsk me again in a few minutes for the rest.")


def keep_promise(reply: str, trace: Any, start: Callable[[str], str] | None) -> str:
    """The reply, and when it promises later work that nothing started, the background helper started on it
    (start: question -> look_into's result) with a note saying so. Only a promise in the reply's closing words counts
    (PROMISE_TAIL): one mid-reply is usually about something already done or being described, and the first live
    hours started the helper on replies like that (2026-10-27)."""
    if not PROMISE.search((reply or "")[-PROMISE_TAIL:]):
        return reply
    called = {t.get("name") for t in (trace or [])
              if isinstance(t, dict) and not str(t.get("result") or "").lstrip().startswith("Error")}
    if called & PROMISE_BACKED_BY:
        return reply
    started = start(reply) if start is not None else "Error: no helper"
    note = PROMISE_FAILED_NOTE if str(started).startswith("Error") else PROMISE_STARTED_NOTE
    return reply.rstrip() + "\n\n(" + note + ")"


# The question(s) a reply closes with (each a sentence ending in "?"), and what a reply needs left without them
TAIL_QUESTION = re.compile(r"(?:[ \t]*[^.!?\n]*\?[\"')*_\s]*)+$")
TAIL_KEEP_MIN = 40


def trim_tail_question(reply: str, previous_reply: str, asked_card: bool = False) -> str:
    """The reply without its closing question when the previous reply already ended with one (2026-11-03: nearly
    every reply in a week ended with a question, and Alex kept answering "I don't know, I'm just starting it").
    A question card (ask_user), a reply that is only a question, and one whose rest is too short stay as they are."""
    if asked_card or not (previous_reply or "").rstrip().endswith("?"):
        return reply
    text = (reply or "").rstrip()
    m = TAIL_QUESTION.search(text)
    if not m or not text.endswith("?"):
        return reply
    rest = text[:m.start()].rstrip()
    return rest if len(rest) >= TAIL_KEEP_MIN else reply


def _any_applied(task_ids: Sequence[str]) -> bool:
    """True if one of these jobs has really gone live (or is going live now)."""
    try:
        from core import self_update
    except ImportError:
        import self_update  # type: ignore[no-redef]
    for tid in task_ids:
        state = (self_update.status(tid) or {}).get("status")
        if state and state not in ("failed", "rolled_back", "rollback_failed"):
            return True
    return False


def unbacked_claim_note(reply: str, trace: Any, task_exists: Callable[[str], bool] | None = None,
                        has_background_record: Callable[[str], bool] | None = None) -> str:
    """The reply, with a short correction when it claims a change that no tool made, names a task that doesn't
    exist (task_exists: looks a task id up), says an update went live when none did, or claims background
    research that no session recorded."""
    # Only calls that worked back a claim: a tool that returned an error changed nothing and made no card
    called = {t.get("name") for t in (trace or [])
              if isinstance(t, dict) and not str(t.get("result") or "").lstrip().startswith("Error")}
    notes = []
    for pattern, tools, note in CLAIMS:
        if pattern.search(reply or "") and not (called & tools):
            notes.append(note)
            break
    ids = sorted({f"task-{n}" for n in TASK_ID.findall(reply or "")})
    # 2026-10-13: "I'm kicking off a dedicated background task (task-190)": there was no task-190
    if task_exists is not None:
        made_up = [t for t in ids if not task_exists(t)]
        if made_up:
            notes.append(f"There's no {', '.join(made_up)}: no such task exists, so nothing was started.")
    if LIVE_CLAIM.search(reply or "") and not _any_applied(ids):
        notes.append(LIVE_NOTE)
    if BACKGROUND_CLAIM.search(reply or ""):
        backed = False
        if "background_status" in called:
            for t in (trace or []):
                if isinstance(t, dict) and t.get("name") == "background_status":
                    res_str = str(t.get("result") or "")
                    if res_str and "No background" not in res_str and not res_str.startswith("Error"):
                        backed = True
                        break
        if not backed and has_background_record is not None and has_background_record(reply):
            backed = True
        if not backed:
            notes.append(BACKGROUND_NOTE)
    return reply.rstrip() + "".join("\n\n(" + n + ")" for n in notes) if notes else reply

