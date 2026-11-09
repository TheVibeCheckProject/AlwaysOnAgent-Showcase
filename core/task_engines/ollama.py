"""OllamaEngine: the local model only (offline)
===========================================
Split out of core/task_engines.py §8 on 2026-11-04; moved into core/task_engines/
on 2026-11-04, re-exported by the package for backwards compatibility.
"""

import logging
import time
from pathlib import Path
from typing import Any

try:
    from core.llm import OllamaClient
except ImportError:
    from llm import OllamaClient  # type: ignore[no-redef]
try:
    from .artifacts import _save_and_check
except ImportError:
    from core.task_engines.artifacts import _save_and_check  # type: ignore[no-redef]

class OllamaEngine:
    """The local model only (offline); also the cloud engine's last resort."""

    def __init__(self, client: OllamaClient, logger: logging.Logger | None = None):
        self.client = client
        self.logger = logger or logging.getLogger("OllamaEngine")

    def execute_task(self, task: dict[str, Any], working_dir: Path) -> tuple[int, str]:
        prompt = task.get("prompt", "")
        model = self.client.select_best_model(task.get("model"))
        self.logger.info(f"[ollama] Connecting to {self.client.host} with model: {model}")
        full_prompt = (
            "You are an autonomous AI software engineer executing tasks headlessly for an Always-On Agent.\n"
            "If the prompt asks you to create or modify a file, include the filename and complete code.\n\n"
            f"TASK INSTRUCTION:\n{prompt}\n\n"
            "Produce clear, high-quality, executable code or solution."
        )
        started = time.time()
        reply = self.client.generate(full_prompt, timeout=task.get("timeout_seconds", 300), model=model,
                                     temperature=0.2, max_tokens=4096)
        if not reply:
            return 1, f"Ollama generation failed (is Ollama running at {self.client.host}?)"
        self.logger.info(f"[ollama] Completed in {time.time() - started:.2f}s ({len(reply)} characters)")
        return _save_and_check(task, working_dir, reply, prompt, self.logger)
