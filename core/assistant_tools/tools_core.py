"""
Core tools: memory, the question and task cards, the agent's own state.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tools_core.py).

Split out of core/assistant_tools.py on 2026-11-03; AssistantTools inherits
these methods via _CoreToolsMixin, so they are still bound methods on the tools object.
"""

import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core import follow_up, self_update
    from core.housekeeping import LOG_NOISE
    from core.observation_sanitizer import compress_log_lines, format_task_details, format_task_row
    from core.processes import kill_process_tree

    from .tool_constants import ASK_USER_STATE, ASKS_TO_FORGET, ASKS_TO_STOP, WANTS_ADVICE
except ImportError:
    import follow_up  # type: ignore[no-redef]
    import self_update  # type: ignore[no-redef]
    from housekeeping import LOG_NOISE  # type: ignore[no-redef]
    from observation_sanitizer import compress_log_lines, format_task_details, format_task_row  # type: ignore[no-redef]
    from processes import kill_process_tree  # type: ignore[no-redef]

    from core.assistant_tools.tool_constants import ASK_USER_STATE, ASKS_TO_FORGET, ASKS_TO_STOP, WANTS_ADVICE

logger = logging.getLogger(__name__)


class _CoreToolsMixin:
    # Host state set by AssistantTools._init_state (core/assistant_tools/__init__.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime. Types mirror _init_state, except proposals:
    # the host declares List[Dict[str, str]] but propose_task appends a dict whose
    # "project" value is Optional[str], so this mixin declares the looser true type.
    db: "DatabaseManager"  # quoted: imported only under TYPE_CHECKING
    memory: Any
    root: Path
    agentwork: Any | None
    user_text: str
    question: dict[str, Any] | None
    proposals: list[dict[str, Any]]
    follow_ups: list[str]

    # _asked_to lives on AssistantTools itself and agentwork_projects on
    # _RoutingMixin, which comes before this mixin in AssistantTools' MRO, so
    # these stubs never shadow the real methods at runtime.
    def _asked_to(self, words: "re.Pattern") -> str | None:
        raise NotImplementedError

    def agentwork_projects(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def search_memory(self, query: str = "", date_from: str | None = None, date_to: str | None = None) -> str:
        if self.memory is None:
            return "Memory is not available."
        query = (query or "").strip()
        if date_from or date_to:
            try:
                p = self.memory.recall_period(date_from or date_to, date_to, query=query)
            except ValueError:
                return "Error: dates must look like 2026-09-23."
            out = [f"Period {p['start']} to {p['end']}: {p['messages_in_period']} messages and events."]
            if p.get("facts"):
                out += ["Facts during this period:"] + [
                    f"- #{f['id']} {f['content']}" + (" (superseded)" if f.get("status") == "superseded" else "")
                    for f in p["facts"]
                ]
            out += ["Summaries:"] + [f"- ({s['start_at'][:10]} to {s['end_at'][:10]}) {s['content']}"
                                     for s in p["summaries"]] if p["summaries"] else ["Summaries: none cover it."]
            out += [("Most relevant messages:" if query else "Messages (a sample across the period):")]
            out += [self._message_line(m) for m in p["messages"]] or ["- none"]
            return "\n".join(out)
        if not query:
            return "Error: give a query, or dates to recall a period."
        facts = self.memory.search_facts(query, k=8)
        msgs = self.memory.search_archive(query, k=6, compressed_only=False)
        out = ["Facts:"] + [f"- #{f['id']} ({f['updated_at'][:10]}) {f['content']}" for f in facts] if facts else ["Facts: none match."]
        out += ["Conversation and events:"] + [self._message_line(m) for m in msgs] if msgs else ["Conversation: nothing matches."]
        return "\n".join(out)

    @staticmethod
    def _message_line(m: dict[str, Any]) -> str:
        who = {"user": "User", "assistant": "Assistant", "event": "Event"}.get(m["role"], m["role"])
        return f"- [{m['created_at'][:16]}] {who}: {m['content'][:400]}"

    def forget_memory(self, what: str) -> str:
        if self.memory is None:
            return "Memory is not available."
        refused = self._asked_to(ASKS_TO_FORGET)
        if refused:
            return refused
        what = " ".join((what or "").split())
        if len(what) < 5:
            return "Error: say what to forget in a plain sentence."
        try:
            n = self.memory.forget_everywhere(what)
        except Exception as e:
            return f"Error: forgetting failed ({e}). Nothing may have been removed; tell the user."
        if not n["rewritten"] and not n["facts"] and not n.get("procedures") and not n.get("derived"):
            return ("Nothing stored matched it, so nothing was removed. Tell the user that, and that the Memory "
                    "panel in DeskAI shows everything saved.")
        # Honest about what's left: the nightly backups still hold it until they roll over (2026-10-28 audit 4.1)
        return (f"Removed: {n['facts']} saved fact(s) deleted, {n['rewritten']} past message(s)/summaries rewritten "
                f"without it, {n.get('procedures', 0)} learned recipe(s) and {n.get('derived', 0)} note(s) or card(s) "
                "made from it deleted. Tell the user it's gone from memory; the nightly backups (kept 14 days) "
                "still hold it until they roll over.")

    def _memory_review(self):
        """The weekly memory check (core/memory_review.py), for answering it; None without memory."""
        if self.memory is None or getattr(self, "db", None) is None:
            return None
        try:
            from core.memory_review import MemoryReview
        except ImportError:
            from memory_review import MemoryReview  # type: ignore[no-redef]
        return MemoryReview(self.db, self.memory, llm=None)

    def memory_check(self, keep: Any = None) -> str:
        review = self._memory_review()
        if review is None:
            return "Memory is not available."
        if isinstance(keep, (int, str)):
            keep = [keep]
        try:
            numbers = [int(n) for n in (keep or [])]
            r = review.answer(numbers)
        except (TypeError, ValueError) as e:
            return f"Error: {e}"
        return (f"Done: {len(r['saved'])} new fact(s) saved, {len(r['retired'])} retired, {r['skipped']} skipped. "
                "Tell the user in one short sentence.")

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
    def ask_user(self, question: str, options: Any = None, allow_other: bool = True) -> str:
        question = " ".join((question or "").split())
        if len(question) < 5:
            return "Error: the question is empty or too short."
        # 2026-11-01/02: "how would you structure it?" got a card back, then "I was asking you what you would choose"
        # got the same card again; "I don't know the best way" got another. The prompt's "never for a question the
        # user asked you" wasn't enough.
        if WANTS_ADVICE.search(self.user_text or ""):
            return ("Error: not asked. The user wants YOUR recommendation (or said they don't know), so don't hand the "
                    "choice back. Answer: pick one option, say why in a sentence or two, and state any assumption.")
        if self._asked_last_turn():
            return ("Error: not asked. Your previous reply was already a question card; don't ask again. Answer now "
                    "with your best recommendation, stating any assumption you're making.")
        if self.question:
            return "Error: ask one question per reply."
        if self.proposals:
            return "Error: you already proposed a task in this reply; ask before proposing, not after."
        if isinstance(options, str):
            options = [options]
        cleaned: list[str] = []
        for o in options or []:
            o = " ".join(str(o).split())[:60]
            if o and o.lower() not in (c.lower() for c in cleaned):
                cleaned.append(o)
        if not 2 <= len(cleaned) <= 4:
            return "Error: give 2 to 4 distinct options."
        self.question = {"text": question[:300], "options": cleaned, "allow_other": bool(allow_other)}
        try:
            self.db.set_state(ASK_USER_STATE, str(self._last_message_id()))
        except (sqlite3.Error, AttributeError):
            pass  # state save is best-effort: the question still goes out
        return ("Asked. The user sees your reply with a button for each option. In your reply, write the question "
                "itself in one sentence (the options appear as buttons, so don't list them), and nothing else: "
                "don't answer it yourself or propose a task yet. Their answer comes as their next message.")

    def _asked_last_turn(self) -> bool:
        """The previous reply was itself an ask_user card: the only assistant message since the card was asked (its
        own reply, saved after the tools ran) is the latest one."""
        at = self.db.get_state(ASK_USER_STATE, "") if hasattr(self.db, "get_state") else ""
        if not str(at).isdigit():
            return False
        try:
            since = self.db.get_connection().execute(
                "SELECT COUNT(*) FROM mem_messages WHERE role = 'assistant' AND id > ?", (int(at),)).fetchone()[0]
        except sqlite3.Error:
            return False
        return since == 1

    def _last_message_id(self) -> int:
        try:
            return int(self.db.get_connection().execute("SELECT COALESCE(MAX(id), 0) FROM mem_messages").fetchone()[0])
        except sqlite3.Error:
            return 0

    def propose_task(self, instruction: str, reason: str = "", project: str | None = None) -> str:
        instruction = (instruction or "").strip()
        if len(instruction) < 8:
            return "Error: the task instruction is too short to act on."
        if self.question:
            return "Error: you asked the user a question in this reply; wait for their answer before proposing."
        if len(self.proposals) >= 2:
            return "Error: at most two task proposals per reply."
        project = (project or "").strip().lower() or None
        known = [p["name"] for p in self.agentwork_projects()]
        if project and project not in known:
            return f"Error: there is no AgentWork project called '{project}'. Known projects: {', '.join(known) or 'none'}."
        self.proposals.append({"instruction": instruction[:2000], "reason": (reason or "").strip()[:300],
                               "project": project})
        return ("Proposed. The user now sees this task with a Run button; it will not run unless they tap it. "
                "In your reply, say in a sentence what it would do.")

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
    def get_system_status(self) -> str:
        s = self.db.get_stats()
        running = self.db.list_tasks(limit=1, phase="in_progress")
        blockers = self.db.get_active_blockers()[:5]
        out = {
            "daemon_status": s["daemon_status"], "overall_phase": s["overall_phase"],
            "last_heartbeat": s["last_heartbeat"], "total_heartbeats": s["total_heartbeats"],
            "default_engine": s["default_engine"],
            "tasks": {k: s[k] for k in ("total_tasks", "tasks_backlog", "tasks_in_progress", "tasks_completed", "tasks_failed")},
            "task_running_now": ({"id": running[0]["id"], "title": running[0]["title"], "started_at": running[0].get("started_at")}
                                 if running else None),
            "active_blockers": [{"task_id": b.get("task_id"), "message": b.get("message"), "at": b.get("created_at")} for b in blockers],
        }
        if self.memory is not None:
            out["memory"] = {**self.memory.stats(), "stored_in": "data/state.db (tables mem_messages, mem_summaries, mem_facts)"}
        return json.dumps(out, indent=1, default=str)

    def background_status(self) -> str:
        """What the away shift background worker is doing, its steps and what was found."""
        try:
            from core import away_shift
            shift = away_shift.get_current_away_shift()
            # The running shift only if it's this database's (the latest one made is kept module-wide: in the tests
            # another file's shift answered here, depending on the order they ran in)
            same_db = shift is not None and Path(getattr(shift.db, "db_path", "")).resolve() == \
                Path(getattr(self.db, "db_path", "")).resolve()
            if shift is not None and same_db:
                return shift.details_report()
            return away_shift.get_details_report(self.db)
        except Exception as e:
            return f"Background status unavailable: {e}"

    def look_into(self, question: str) -> str:
        """Starts the background helper (core/follow_up.py); its answer comes later as a chat message."""
        result = follow_up.start(question, self.db, self.memory, getattr(self.memory, "llm", None), self.root,
                                 agentwork=self.agentwork)
        if not result.startswith("Error"):
            self.follow_ups.append(question)
        return result

    def list_tasks(self, phase: str | None = None, limit: int = 10) -> str:
        tasks = self.db.list_tasks(limit=max(1, min(int(limit or 10), 30)), phase=phase or None)
        if not tasks:
            return "No tasks" + (f" in phase '{phase}'." if phase else ".")
        return "\n".join(format_task_row(t) for t in tasks)

    def get_task(self, task_id: str) -> str:
        t = self.db.get_task(str(task_id).strip())
        if not t:
            return f"No task with id '{task_id}'."
        update = self_update.status(t["id"])
        return format_task_details(t, update)

    def cancel_task(self, task_id: str) -> str:
        refused = self._asked_to(ASKS_TO_STOP)
        if refused:
            return refused
        task = self.db.get_task((task_id or "").strip())
        if not task:
            return f"Error: no task called '{task_id}'."
        if task.get("phase") == "in_progress" and task.get("worker_pid"):
            kill_process_tree(task["worker_pid"])
        if not self.db.cancel_task(task["id"], "Cancelled from chat"):
            return f"{task['id']} had already finished ({task.get('phase')}); nothing to cancel."
        self.db.end_busy_status()
        try:
            from core import computer_use
            computer_use.cancel_for_task(task["id"], reason="Cancelled from chat")
        except Exception as e:
            logger.warning(f"computer_use cancel failed for {task['id']}: {e}")
        return f"Cancelled {task['id']} ({task.get('title')})."

    def read_logs(self, lines: int = 40, errors_only: bool = False, contains: str | None = None) -> str:
        lines = max(1, min(int(lines or 40), 150))
        path = self.root / "logs" / "worker.log"
        if not path.exists():
            return "logs/worker.log does not exist yet."
        with open(path, "rb") as f:                    # the log is large: read only its tail
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - 1_500_000)
            f.seek(start)
            tail = f.read().decode("utf-8", errors="replace").splitlines()
            if start:
                tail = tail[1:]                         # a mid-file seek lands mid-line
        needle = (contains or "").lower()
        picked = [l for l in tail if not any(n in l for n in LOG_NOISE)
                  and (not errors_only or "[WARNING]" in l or "[ERROR]" in l or "Traceback" in l)
                  and (not needle or needle in l.lower())]
        if not picked:
            return "No matching log lines in the recent log."
        compressed = compress_log_lines(picked[-lines:], max_len=300)
        return "\n".join(compressed)

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['_CoreToolsMixin']
