"""CloudEngine: the free cloud models, then local Ollama
================================================
Split out of core/task_engines.py §7 on 2026-11-04; moved into core/task_engines/
on 2026-11-04, re-exported by the package for backwards compatibility.
"""

import logging
import time
from pathlib import Path
from typing import Any

try:
    from core.llm import CloudLLM
    from core.llm_usage import usage_purpose
except ImportError:
    from llm import CloudLLM  # type: ignore[no-redef]
    from llm_usage import usage_purpose  # type: ignore[no-redef]
try:
    from .artifacts import _save_and_check
    from .constants import CODING_SYSTEM_PROMPT, UNAVAILABLE_EXIT_CODE
    from .ollama import OllamaEngine
except ImportError:
    from core.task_engines.artifacts import _save_and_check
    from core.task_engines.constants import CODING_SYSTEM_PROMPT, UNAVAILABLE_EXIT_CODE
    from core.task_engines.ollama import OllamaEngine  # type: ignore[no-redef]

class CloudEngine:
    """The free cloud models (CloudLLM.TASK_MODELS: Codestral first), then local Ollama if none answers."""

    def __init__(self, llm: CloudLLM, ollama: "OllamaEngine", logger: logging.Logger | None = None):
        self.llm = llm
        self.ollama = ollama
        self.logger = logger or logging.getLogger("CloudEngine")

    def execute_task(self, task: dict[str, Any], working_dir: Path) -> tuple[int, str]:
        code, output = self.run_in_cloud(task, working_dir)
        if code != UNAVAILABLE_EXIT_CODE:
            return code, output      # done, or its code failed QA (a weaker local model would not do better)
        self.logger.warning(f"[cloud] No cloud model available ({output}). Falling back to local Ollama.")
        return self.ollama.execute_task(task, working_dir)

    def run_in_cloud(self, task: dict[str, Any], working_dir: Path) -> tuple[int, str]:
        """The cloud step alone: (0, reply) done, (1, report) its code failed QA, or UNAVAILABLE_EXIT_CODE."""
        if not self.llm.is_available():
            return UNAVAILABLE_EXIT_CODE, "no cloud AI provider is configured (.env)"
        prompt = task.get("prompt", "")
        routes = ([task["model"]] if task.get("model") else []) + list(self.llm.TASK_MODELS)
        messages = [{"role": "system", "content": CODING_SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        started = time.time()
        with usage_purpose("task"):
            reply, route = self.llm.complete(messages, max_tokens=4096, temperature=0.2,
                                             timeout=task.get("timeout_seconds", 120), models=routes)
        if not reply:
            return UNAVAILABLE_EXIT_CODE, f"cloud generation failed on every provider: {route}"
        self.logger.info(f"[cloud] Completed via {route} in {time.time() - started:.2f}s (~{len(reply)} chars)")
        return _save_and_check(task, working_dir, reply, prompt, self.logger)
