"""AntigravityEngine: run a task with the Antigravity CLI (agy)
=======================================================
Split out of core/task_engines.py §5 on 2026-11-04; moved into core/task_engines/
on 2026-11-04, re-exported by the package for backwards compatibility.
"""

import logging
from pathlib import Path
from typing import Any

try:
    from core.config import get_safe_subprocess_env, git_lockdown_env, resolve_agy_binary
except ImportError:
    from config import get_safe_subprocess_env, git_lockdown_env, resolve_agy_binary  # type: ignore[no-redef]
try:
    from .constants import TASK_RULES
    from .streaming import run_streaming
except ImportError:
    from core.task_engines.constants import TASK_RULES
    from core.task_engines.streaming import run_streaming  # type: ignore[no-redef]

class AntigravityEngine:
    """Runs a task with the Antigravity CLI (agy): a capable coding agent with a real terminal."""

    def __init__(
        self,
        custom_agy_path: str | None = None,
        db: Any | None = None,
        logger: logging.Logger | None = None
    ):
        self.logger = logger or logging.getLogger("AntigravityEngine")
        self.agy_path = custom_agy_path or resolve_agy_binary()
        self.db = db

    def execute_task(
        self,
        task: dict[str, Any],
        working_dir: Path
    ) -> tuple[int, str]:
        """
        Executes a prompt via Antigravity CLI non-interactively in headless mode.
        Captures exit code and streams real-time stdout/stderr.
        """
        mode = task.get("mode", "accept-edits")
        timeout_seconds = task.get("timeout_seconds", 300)
        task_id = task.get("id", "unknown")
        prompt = TASK_RULES.format(folder=Path(working_dir).resolve()) + "\n\n" + task.get("prompt", "")

        cmd = [
            self.agy_path,
            "--prompt", prompt,
            "--dangerously-skip-permissions",
            "--mode", mode,
            "--add-dir", str(Path(working_dir).resolve()),
        ]

        self.logger.info(f"Triggering Antigravity CLI in {working_dir} (task rules + task prompt, {len(prompt)} chars)")
        self.logger.info(f"Task ID: {task_id} | Mode: {mode} | Timeout: {timeout_seconds}s")
        try:
            return run_streaming(cmd, working_dir, get_safe_subprocess_env(git_lockdown_env()), task_id,
                                 timeout_seconds, self.logger, "agy", db=self.db)
        except FileNotFoundError:
            err_msg = f"Antigravity CLI binary '{self.agy_path}' was not found."
            self.logger.critical(err_msg)
            return 127, err_msg
        except Exception as e:
            err_msg = f"Unexpected error launching Antigravity CLI: {e!s}"
            self.logger.exception(err_msg)
            return 1, err_msg
