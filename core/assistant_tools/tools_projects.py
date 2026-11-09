"""
Project tools: AgentWork jobs, adding a repo, the go-live card, reading its own code.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tools_projects.py).

Split out of core/assistant_tools.py on 2026-11-03; AssistantTools inherits
these methods via _ProjectsToolsMixin, so they are still bound methods on the tools object.
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

try:
    from core import actions, self_update

    from .tool_constants import BLOCKED_DIRS, BLOCKED_NAMES, TEXT_EXTENSIONS
except ImportError:
    import actions  # type: ignore[no-redef]
    import self_update  # type: ignore[no-redef]

    from core.assistant_tools.tool_constants import BLOCKED_DIRS, BLOCKED_NAMES, TEXT_EXTENSIONS


class _ProjectsToolsMixin:
    # Interface this mixin requires from the host class (AssistantTools) and
    # from _RoutingMixin, which comes before this mixin in AssistantTools'
    # MRO, so these runtime stubs never shadow the real definitions. The
    # plain annotations create no runtime value.
    db: Any
    memory: Any
    root: Path
    source: str
    started: list[str]
    question: dict[str, Any] | None
    proposals: list[dict[str, Any]]

    def _can_start(self) -> str | None:
        raise NotImplementedError

    def agentwork_projects(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def start_project_job(self, instruction: str, project: str) -> str:
        if (err := self._can_start()):
            return err
        instruction = (instruction or "").strip()
        if len(instruction) < 8:
            return "Error: describe the change in a sentence."
        project = (project or "").strip().lower()
        known = [p["name"] for p in self.agentwork_projects()]
        if project not in known:
            return f"Error: there is no AgentWork project called '{project}'. Known projects: {', '.join(known) or 'none'}."
        task_id = actions.queue_instruction(self.db, instruction[:2000], project, source=self.source)
        self.started.append(task_id)
        try:
            self.memory.record_event(f"From chat, the user started {task_id} on {project}: {instruction[:300]}")
        except (sqlite3.Error, AttributeError):
            pass  # recording the event is best-effort: the job already started
        return (f"Started {task_id}: AgentWork is working on '{project}' in its own branch. Nothing goes live "
                "without the user's review; the live card shows when it's done.")

    def propose_project(self, name: str, repo: str, description: str) -> str:
        name, repo, description = (name or "").strip().lower(), (repo or "").strip(), " ".join((description or "").split())
        if self.question:
            return "Error: you asked the user a question in this reply; wait for their answer before proposing."
        if len(self.proposals) >= 2:
            return "Error: at most two proposals per reply."
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", name):
            return "Error: the name must be lowercase letters, digits, - or _ (e.g. 'recipe-app')."
        if not re.fullmatch(r"https://[\w.-]+/[\w.-]+/[\w.-]+?(\.git)?/?", repo):
            return "Error: the repo must be an https git link, e.g. https://github.com/owner/repo.git. Ask the user for it."
        if len(description) < 5:
            return "Error: add a one-line description of what the repo is."
        if name in [p["name"] for p in self.agentwork_projects()]:
            return f"Error: '{name}' is already an AgentWork project; propose a task on it instead."
        self.proposals.append({"kind": "add_project", "instruction": f"Add the project '{name}' ({repo}): {description}",
                               "reason": "", "project": None,
                               "add": {"name": name, "repo": repo, "description": description[:300]}})
        return ("Proposed. The user sees an Add project button; nothing is added unless they tap it, and AgentWork "
                "then checks the repo can be reached. In your reply, say so in a sentence.")

    def offer_go_live(self, task_id: str) -> str:
        """Shows the job's card with its go-live chip; nothing goes live until the user taps it."""
        task = self.db.get_task(str(task_id).strip())
        try:
            self_update.check(task)
        except self_update.UpdateRefused as e:
            return f"Error: can't offer it: {e}."
        project = (task.get("metadata") or {}).get("project")
        self.card_task = task["id"]
        chip = "🚀 Push live" if project == "website" else "⚡ Apply update"
        what = ("it's built and tested first, then pushed to the live site" if project == "website" else
                "it's tested first, you restart, and it rolls back by itself if anything's wrong")
        return (f"The card for {task['id']} is shown with a {chip} chip. Tell the user to tap it when they're ready: "
                f"{what}, and a notification brings the outcome. Nothing has gone live yet.")

    def _safe_path(self, rel: str) -> Path:
        p = (self.root / (rel or ".")).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError("outside the project folder")
        for part in p.relative_to(self.root).parts:
            if part in BLOCKED_DIRS or BLOCKED_NAMES.search(part):
                raise PermissionError(f"'{rel}' is not readable (secrets, data or binaries)")
        return p

    def list_project_files(self, path: str = ".") -> str:
        p = self._safe_path(path)
        if not p.is_dir():
            return f"'{path}' is not a folder."
        entries = []
        for c in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
            if c.name in BLOCKED_DIRS or BLOCKED_NAMES.search(c.name) or c.name.startswith("."):
                continue
            entries.append(f"{c.name}/" if c.is_dir() else f"{c.name} ({c.stat().st_size:,} bytes)")
        return f"{p.relative_to(self.root).as_posix() or '.'}:\n" + "\n".join(entries[:200])

    def read_project_file(self, path: str, start_line: int = 1, max_lines: int = 150, search: str | None = None) -> str:
        p = self._safe_path(path)
        if not p.is_file():
            return f"'{path}' is not a file."
        if p.suffix.lower() not in TEXT_EXTENSIONS:
            return f"'{path}' is not a text file I can read."
        text = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if search:
            hits = [f"{i}: {l[:300]}" for i, l in enumerate(text, 1) if search.lower() in l.lower()]
            return f"{path}: {len(hits)} line(s) contain '{search}'\n" + "\n".join(hits[:80]) if hits else f"'{search}' not found in {path}."
        start = max(1, int(start_line or 1))
        n = max(1, min(int(max_lines or 150), 300))
        chunk = text[start - 1:start - 1 + n]
        body = "\n".join(f"{start + i}: {l}" for i, l in enumerate(chunk))
        more = f"\n…({len(text) - (start - 1 + len(chunk))} more lines)" if start - 1 + len(chunk) < len(text) else ""
        return f"{path} (lines {start}-{start + len(chunk) - 1} of {len(text)}):\n{body}{more}"

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['_ProjectsToolsMixin']
