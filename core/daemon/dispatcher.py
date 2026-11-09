"""
EngineDispatcher core: the task's folder, the guard, the engine, fallback.
(daemon.py §2)

Moved into the core.daemon package on 2026-11-04.
"""
import logging
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from core.config import AGENT_WORK_DIR, notify_owner
    from core.database import DatabaseManager
    from core.llm import CloudLLM, OllamaClient
    from core.task_engines import AgentWorkEngine, AntigravityEngine, CloudEngine, OllamaEngine, normalize_engine
except ImportError:
    from config import AGENT_WORK_DIR, notify_owner  # type: ignore[no-redef]
    from database import DatabaseManager  # type: ignore[no-redef]
    from llm import CloudLLM, OllamaClient  # type: ignore[no-redef]
    from task_engines import (  # type: ignore[no-redef]
        AgentWorkEngine,
        AntigravityEngine,
        CloudEngine,
        OllamaEngine,
        normalize_engine,  # type: ignore[no-redef]
    )


TASK_ROOT = AGENT_WORK_DIR / "workspaces"   # each task works in AgentWork/workspaces/<task id>/, outside this project


class _DispatcherMixin:
    if TYPE_CHECKING:
        # Real implementations live on _SpecialEnginesMixin (later in the MRO
        # for EngineDispatcher): declarations only — a runtime stub here
        # would shadow the real methods.
        def _execute_media_pipeline(self, task: dict[str, Any]) -> tuple[int, str, str, bool]: ...
        def _execute_social_posts(self, task: dict[str, Any]) -> tuple[int, str, str, bool]: ...
        def _execute_weekly_report(self, task: dict[str, Any]) -> tuple[int, str, str, bool]: ...
        def _execute_computer(self, task: dict[str, Any]) -> tuple[int, str, str, bool]: ...

    def __init__(
        self,
        workspace_dir: Path,
        custom_agy_path: str | None = None,
        db: DatabaseManager | None = None,
        logger: logging.Logger | None = None,
        task_root: Path | None = None,
        agent_work_dir: Path | None = None
    ):
        self.workspace_dir = workspace_dir
        self.task_root = Path(task_root) if task_root else TASK_ROOT
        self.db = db
        self.logger = logger or logging.getLogger("EngineDispatcher")
        self.llm = CloudLLM(logger=self.logger)          # shared with the daemon's memory upkeep
        self.ollama = OllamaEngine(OllamaClient.from_config(self.logger), logger=self.logger)
        self.cloud = CloudEngine(self.llm, self.ollama, logger=self.logger)
        self.antigravity = AntigravityEngine(custom_agy_path=custom_agy_path, db=self.db, logger=self.logger)
        self.agentwork = AgentWorkEngine(agent_work_dir or AGENT_WORK_DIR, db=self.db, logger=self.logger)

    def task_dir(self, task: dict[str, Any], create: bool = True) -> Path:
        """The folder a task works in: <task root>/<task id>, outside the agent's own files."""
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task.get("id") or "task")) or "task"
        folder = self.task_root / safe_id
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _guard_snapshot(self) -> dict[str, Any] | None:
        """What a task must not change: the agent's own files (git status) and .env."""
        try:
            status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(self.workspace_dir),
                                    capture_output=True, text=True, timeout=20, check=False)
            if status.returncode != 0:
                return None
            env_file = self.workspace_dir / ".env"
            env_sig = (env_file.stat().st_mtime_ns, env_file.stat().st_size) if env_file.exists() else None
            # A task's own folder is the one place it may change (it is only inside this repo in tests)
            try:
                own = self.task_root.resolve().relative_to(Path(self.workspace_dir).resolve()).as_posix() + "/"
            except ValueError:
                own = None
            lines = {l for l in status.stdout.splitlines() if not (own and l[3:].strip('"').startswith(own))}
            return {"status": lines, "env": env_sig}
        except (OSError, subprocess.TimeoutExpired):  # git missing/stat failed, or git timed out
            return None

    def _guard_changes(self, before: dict[str, Any] | None) -> list:
        after = self._guard_snapshot()
        if before is None or after is None:
            return []
        changed = sorted(l[3:] for l in after["status"] ^ before["status"])
        if after["env"] != before["env"]:
            changed.append(".env")
        return changed

    def execute(self, task: dict[str, Any], default_engine: str = "auto") -> tuple[int, str, str, bool]:
        """
        Executes task using specified engine with resilient fallback, in the task's own folder.
        Afterwards it checks that the agent's own files and .env are unchanged; if not, the task
        fails and the owner is alerted.
        Returns: (exit_code, output_text, engine_used, fallback_triggered)
        """
        engine = normalize_engine(task.get("engine") or default_engine)
        if engine == "media_pipeline":
            self.logger.info(f"[dispatcher] Routing task '{task.get('id')}' to the media pipeline")
            return self._execute_media_pipeline(task)
        if engine == "social_posts":
            self.logger.info(f"[dispatcher] Routing task '{task.get('id')}' to SocialPostEngine")
            return self._execute_social_posts(task)
        if engine == "weekly_report":
            self.logger.info(f"[dispatcher] Routing task '{task.get('id')}' to the weekly report")
            return self._execute_weekly_report(task)
        if engine == "computer":
            self.logger.info(f"[dispatcher] Routing task '{task.get('id')}' to AgentComputerUse")
            return self._execute_computer(task)

        # AgentWork makes its own folder for a job; every other engine works in the task's folder
        work = self.task_dir(task, create=engine != "agentwork")
        before = self._guard_snapshot()
        code, out, used, fallback = self._run_engine(task, engine, work)
        changed = self._guard_changes(before)
        if changed:
            listed = ", ".join(changed[:15]) + (f" and {len(changed) - 15} more" if len(changed) > 15 else "")
            alert = (f"Safety check failed for task {task.get('id')}: files outside its folder changed while it ran "
                     f"({listed}). The task was marked failed; review these changes.")
            self.logger.error(f"[dispatcher] {alert}")
            # Resolve notify_owner from core.daemon at call time, not from this
            # module's import-time binding: tests patch core.daemon.notify_owner
            # (e.g. test_task_isolation_and_safety_audit) and the patch must be
            # what this call sees, as it was when execute() lived in daemon.py.
            try:
                from core.daemon import notify_owner as _notify_owner
            except ImportError:
                _notify_owner = notify_owner
            _notify_owner("⚠️ " + alert)
            return 1, f"{out}\n\n[SAFETY] {alert}", used, fallback
        return code, out, used, fallback

    def _run_engine(self, task: dict[str, Any], engine: str, work: Path) -> tuple[int, str, str, bool]:
        """(exit code, output, engine used, whether it fell back). engine is normalized."""
        task_id = task.get("id")
        if engine == "agentwork":
            self.logger.info(f"[dispatcher] Routing task '{task_id}' to AgentWork")
            code, out = self.agentwork.execute_task(task)
            return code, out, "agentwork", False
        if engine == "antigravity":
            self.logger.info(f"[dispatcher] Routing task '{task_id}' to Antigravity")
            code, out = self.antigravity.execute_task(task, work)
            return code, out, "antigravity", False
        if engine == "cloud":
            self.logger.info(f"[dispatcher] Routing task '{task_id}' to the free cloud models")
            code, out = self.cloud.execute_task(task, work)
            return code, out, "cloud", False
        if engine == "ollama":
            self.logger.info(f"[dispatcher] Routing task '{task_id}' to local Ollama")
            code, out = self.ollama.execute_task(task, work)
            return code, out, "ollama", False

        # auto: Antigravity first; if it fails (quota, errors), the free cloud models
        self.logger.info(f"[dispatcher] Engine 'auto': trying Antigravity for task '{task_id}'...")
        code, out = self.antigravity.execute_task(task, work)
        if code == 0:
            return code, out, "antigravity", False
        self.logger.warning(f"[dispatcher] Antigravity returned exit code {code}; falling back to the free cloud models.")
        fb_code, fb_out = self.cloud.execute_task(task, work)
        return fb_code, fb_out, "cloud_fallback", True

