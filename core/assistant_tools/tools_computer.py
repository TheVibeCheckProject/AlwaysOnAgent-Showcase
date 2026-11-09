"""
Computer and watch tools: a computer task on the Mini's screen, and tell-me-when
watches for mail, web pages, searches and the calendar.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tools_computer.py).

Split out of core/assistant_tools.py on 2026-11-03; AssistantTools inherits
these methods via _ComputerToolsMixin, so they are still bound methods on the tools object.
"""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core import computer_use, watches

    from .tool_constants import ASKS_TO_STOP
except ImportError:
    import computer_use  # type: ignore[no-redef]
    import watches  # type: ignore[no-redef]

    from core.assistant_tools.tool_constants import ASKS_TO_STOP


class _ComputerToolsMixin:
    if TYPE_CHECKING:
        # Host-class attribute, set by AssistantTools._init_state. Annotation-only:
        # no runtime value exists, so nothing here can shadow anything at runtime.
        db: DatabaseManager

    # Interface this mixin requires from the host class: _propose_post and _asked_to
    # are defined on AssistantTools itself, which comes before every mixin in its
    # MRO, so these stubs never shadow the real ones at runtime.
    def _propose_post(self, kind: str, instruction: str, add: dict[str, Any]) -> str | None:
        raise NotImplementedError

    def _asked_to(self, words: "re.Pattern") -> str | None:
        raise NotImplementedError

    def computer_task(self, goal: str) -> str:
        goal = " ".join((goal or "").split())
        if len(goal) < 8:
            return "Error: say what to do on the computer, in a sentence (e.g. the site and what to find)."
        if (err := self._propose_post("computer", f"Computer task: {goal[:200]}", {"goal": goal[:500]})):
            return err
        return ("The user sees a Computer task card; it runs on the Mini's screen when they tap Run, and stops before "
                "anything is submitted, sent or paid. Nothing has started yet: say so in a sentence.")

    def computer_screenshots(self, task_id: str = "", steps: str = "") -> str:
        """A computer run's step screenshots, as image lines the reply can show (computer_use.run_screenshots)."""
        tid = (task_id or "").strip().lower()
        if tid:
            if not tid.startswith("task-"):
                tid = f"task-{tid}"
            run_id = computer_use.run_for_task(self.db.get_task(tid))
            if not run_id:
                return f"Error: {tid} has no computer run with screenshots (not a computer task, or it didn't start)."
        else:
            runs = computer_use.latest_runs(1)
            if not runs:
                return "There are no computer runs with screenshots on the PC."
            run_id = runs[0]
        names = computer_use.run_screenshots(run_id)
        if not names:
            return f"Run {run_id} has no screenshots left (runs are kept 14 days)."
        if steps.strip().lower() == "all":
            pick = names[-6:] if len(names) > 6 else names
        elif steps.strip():
            wanted = {int(n) for n in re.findall(r"\d+", steps)}
            pick = [n for n in names if (m := re.search(r"\d+", n)) and int(m.group()) in wanted][:6]
        else:
            pick = names[-3:]                             # the end of a run is usually what matters
        lines = [f"![{tid or run_id} {n[:-4]}](/api/computer/runs/{run_id}/screenshots/{n})" for n in pick]
        return (f"Run {run_id}{f' ({tid})' if tid else ''}: {len(names)} screenshots ({names[0][:-4]} to "
                f"{names[-1][:-4]}). To show them, put these lines in your reply exactly as they are:\n" + "\n".join(lines))

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
    def _watches(self):
        return watches.Watches(self.db)

    def watch_mail(self, source: str = "both", sender: str = "", subject: str = "", words: str = "",
                   once: bool = False, deadline: str = "", note: str = "", every_email: bool = False) -> str:
        # 2026-11-03: "every email in my Zoho inbox" was saved as subject 'inbox' (never matched). A folder name in a
        # filter is that mistake: it means every email
        if (subject or words).strip().lower() in ("inbox", "in:inbox", "my inbox", "the inbox") and not sender:
            subject = words = ""
            every_email = True
        try:
            w = self._watches().add(source, sender, subject, words, bool(once), deadline=deadline, note=note,
                                    everything=bool(every_email))
        except ValueError as e:
            return f"Error: {e}."
        except Exception as e:
            return f"Error: couldn't search that mailbox to start the watch ({e}). Nothing was saved."
        return (f"Saved {watches.Watches.describe(w)}. Checked every 5 minutes; emails already there don't count. Tell the "
                "user exactly what it matches (and quiet hours hold the messages until morning).")

    def watch_for(self, kind: str = "", target: str = "", words: str = "", gone: str = "", ask: str = "",
                  every_minutes: int | None = None, lead_minutes: int | None = None, once: bool = False) -> str:
        """Watches for anything that isn't email (Alex, 2026-11-03: "whatever I ask for it to watch it should create
        a watch for it"): a web page, a web search, the calendar."""
        kind = (kind or "").strip().lower()
        try:
            if kind == "calendar":
                w = self._watches().add_calendar(words, lead_minutes, bool(once))
            else:
                w = self._watches().add_web(kind, target, words, gone, ask, every_minutes, bool(once))
        except ValueError as e:
            return f"Error: {e}. Nothing was saved."
        except Exception as e:
            what = "read the calendar" if kind == "calendar" else ("open that page" if kind == "page" else "search")
            return f"Error: couldn't {what} to start the watch ({e}). Nothing was saved."
        return (f"Saved {watches.Watches.describe(w)}. What's there now doesn't count, only what changes after. Tell "
                "the user exactly what it watches and how often (quiet hours hold the messages until morning).")

    def list_watches(self) -> str:
        ws = self._watches().list()
        if not ws:
            return "No active watches."
        return "\n".join(f"- {watches.Watches.describe(w)}; {w['matches']} found so far"
                         + (f"; last check failed: {w['last_error']}" if w.get("last_error") else "") for w in ws)

    def change_watch(self, id: int, sender: str | None = None, subject: str | None = None,
                     words: str | None = None, once: bool | None = None) -> str:
        try:
            w = self._watches().change(int(id), sender, subject, words, once)
        except ValueError as e:
            return f"Error: {e}. Nothing was changed."
        except Exception as e:
            return f"Error: couldn't check the mailbox for the new filter ({e}). Nothing was changed."
        return f"Changed: {watches.Watches.describe(w)}. Tell the user exactly what it matches now."

    def stop_watch(self, id: int) -> str:
        refused = self._asked_to(ASKS_TO_STOP)
        if refused:
            return refused
        return f"Stopped watch #{id}." if self._watches().stop(int(id)) else f"No active watch #{id}."

__all__ = ['_ComputerToolsMixin']
