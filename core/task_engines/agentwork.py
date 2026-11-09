"""AgentWorkEngine: hand a project job to AgentWork
===============================================
Split out of core/task_engines.py §6 on 2026-11-04; moved into core/task_engines/
on 2026-11-04, re-exported by the package for backwards compatibility.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from core.config import get_safe_subprocess_env, resolve_agy_binary
except ImportError:
    from config import get_safe_subprocess_env, resolve_agy_binary  # type: ignore[no-redef]
try:
    from .streaming import run_streaming
except ImportError:
    from core.task_engines.streaming import run_streaming  # type: ignore[no-redef]

class AgentWorkEngine:
    """
    Hands a job to AgentWork, a separate tool in its own folder (AGENT_WORK_DIR, C:/Projects/AgentWork).
    AgentWork keeps the projects (repos and their rules), makes a fresh copy of the repo on its own
    branch, runs the coding agent there and reports what changed. This class only calls it:
        agentwork.py list                          the projects, as JSON
        agentwork.py add <name> --repo --description   registers a repo (AgentWork checks it first)
        agentwork.py run <project> <job id>        one job (the instruction on stdin)
    """

    def __init__(self, home: Path, db: Any | None = None, logger: logging.Logger | None = None):
        self.home = Path(home)
        self.db = db
        self.logger = logger or logging.getLogger("AgentWorkEngine")

    @property
    def script(self) -> Path:
        return self.home / "agentwork.py"

    def _env(self) -> dict[str, str]:
        extra = {"PYTHONIOENCODING": "utf-8"}
        agy = resolve_agy_binary()
        if Path(agy).is_file():
            extra["AGY_PATH"] = agy
        return get_safe_subprocess_env(extra)

    def list_projects(self) -> list[dict[str, Any]]:
        """AgentWork's projects ([{name, description, repo, ...}]); [] if the tool is missing or fails."""
        if not self.script.is_file():
            self.logger.warning(f"[agentwork] Not found: {self.script}")
            return []
        try:
            result = subprocess.run([sys.executable, str(self.script), "list"], cwd=str(self.home), env=self._env(),
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
            projects = json.loads(result.stdout) if result.returncode == 0 else None
            if not isinstance(projects, list):
                self.logger.warning(f"[agentwork] 'list' failed ({result.returncode}): {result.stdout[-300:]}")
                return []
            return [p for p in projects if isinstance(p, dict) and p.get("name")]
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            self.logger.warning(f"[agentwork] 'list' failed: {e}")
            return []

    def add_project(self, name: str, repo: str, description: str, base: str = "main") -> dict[str, Any]:
        """Registers a repo with AgentWork. Returns the new project; raises ValueError with AgentWork's
        reason (bad name, unreachable repo, duplicate...) if it refuses. base: the branch jobs start from (the
        repo's default branch, from the repo browser)."""
        if not self.script.is_file():
            raise ValueError(f"AgentWork wasn't found at {self.home}")
        try:
            result = subprocess.run([sys.executable, str(self.script), "add", name, "--repo", repo,
                                     "--description", description, "--base", base or "main"],
                                    cwd=str(self.home), env=self._env(),
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
        except subprocess.TimeoutExpired:
            raise ValueError("AgentWork took too long checking the repo")
        if result.returncode != 0:
            reason = (result.stderr or result.stdout).strip().removeprefix("[agentwork]").strip()
            raise ValueError(reason[-400:] or f"AgentWork refused (exit {result.returncode})")
        return json.loads(result.stdout)

    def execute_task(self, task: dict[str, Any]) -> tuple[int, str]:
        task_id = str(task.get("id", "unknown"))
        project = str((task.get("metadata") or {}).get("project") or "")
        timeout_seconds = int(task.get("timeout_seconds") or 1800)
        if not project:
            return 2, "This AgentWork task has no project."
        if not self.script.is_file():
            return 127, f"AgentWork was not found at {self.script}."
        cmd = [sys.executable, str(self.script), "run", project, task_id, "--timeout", str(timeout_seconds)]
        self.logger.info(f"[agentwork] Job {task_id} on project '{project}' (timeout {timeout_seconds}s)")
        try:
            # A little extra time so AgentWork's own timeout (which reports what it managed) fires first
            return run_streaming(cmd, self.home, self._env(), task_id, timeout_seconds + 120, self.logger,
                                 "agentwork", db=self.db, stdin_text=task.get("prompt", ""))
        except Exception as e:
            self.logger.exception("[agentwork] Couldn't start AgentWork")
            return 1, f"Couldn't start AgentWork: {e}"
