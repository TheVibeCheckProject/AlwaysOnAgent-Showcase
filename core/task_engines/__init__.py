"""
Task engines: how a background task gets done
=============================================
- AntigravityEngine: the Antigravity CLI (agy), a capable coding agent with a real terminal.
- CloudEngine:       the free cloud models (Codestral first), then local Ollama if every cloud
                     route is unavailable.
- OllamaEngine:      the local model only (offline).
- AgentWorkEngine:   hands a project job (a change to one of the user's repos) to AgentWork, a
                     separate tool in its own folder (C:/Projects/AgentWork).
- FileArtifactHelper: saves the files a model's reply contains into the task's folder, then runs
                     any tests it wrote in the sandbox.

Every engine works inside the task's own folder (AgentWork/workspaces/<task id>/, given by the dispatcher).
The media pipeline is dispatched separately (core/daemon/special.py).

MAP (split 2026-11-04; packaged 2026-11-04 — this __init__ is the re-export shell,
the code lives in this package)
  §2 constants, engine names, prompts, quota_message -> core/task_engines/constants.py
  §3 FileArtifactHelper + §9 _save_and_check        -> core/task_engines/artifacts.py
  §4 run_streaming                                   -> core/task_engines/streaming.py
  §5 AntigravityEngine                               -> core/task_engines/antigravity.py
  §6 AgentWorkEngine                                 -> core/task_engines/agentwork.py
  §7 CloudEngine                                     -> core/task_engines/cloud.py
  §8 OllamaEngine                                    -> core/task_engines/ollama.py

The old flat files (core/task_engines.py, core/task_*.py) were thin shims re-exporting
from this package; they were removed 2026-11-04, so import from this package.
"""

# Kept on purpose: tests patch engines_mod.subprocess.Popen (test_worker.py), i.e. they
# read the subprocess module through this module, so the attribute must still exist here.
import subprocess

try:
    from .agentwork import AgentWorkEngine
    from .antigravity import AntigravityEngine
    from .artifacts import FileArtifactHelper, _save_and_check
    from .cloud import CloudEngine
    from .constants import (
        _QUOTA_MARKERS,
        _QUOTA_RESET,
        CODING_SYSTEM_PROMPT,
        DEFAULT_ENGINE_CHOICES,
        ENGINE_ALIASES,
        ENGINES,
        OUTPUT_HEAD,
        OUTPUT_LOG_LINES,
        OUTPUT_TAIL,
        QUOTA_EXIT_CODE,
        SAFE_TEST_NAME,
        TASK_RULES,
        UNAVAILABLE_EXIT_CODE,
        normalize_engine,
        quota_message,
    )
    from .ollama import OllamaEngine
    from .streaming import run_streaming
except ImportError:
    from core.task_engines.agentwork import AgentWorkEngine
    from core.task_engines.antigravity import AntigravityEngine
    from core.task_engines.artifacts import FileArtifactHelper, _save_and_check
    from core.task_engines.cloud import CloudEngine
    from core.task_engines.constants import (
        _QUOTA_MARKERS,
        _QUOTA_RESET,
        CODING_SYSTEM_PROMPT,
        DEFAULT_ENGINE_CHOICES,
        ENGINE_ALIASES,
        ENGINES,
        OUTPUT_HEAD,
        OUTPUT_LOG_LINES,
        OUTPUT_TAIL,
        QUOTA_EXIT_CODE,
        SAFE_TEST_NAME,
        TASK_RULES,
        UNAVAILABLE_EXIT_CODE,
        normalize_engine,
        quota_message,
    )
    from core.task_engines.ollama import OllamaEngine  # type: ignore[no-redef]
    from core.task_engines.streaming import run_streaming

__all__ = [
    "CODING_SYSTEM_PROMPT",
    "DEFAULT_ENGINE_CHOICES",
    "ENGINES",
    "ENGINE_ALIASES",
    "OUTPUT_HEAD",
    "OUTPUT_LOG_LINES",
    "OUTPUT_TAIL",
    "QUOTA_EXIT_CODE",
    "SAFE_TEST_NAME",
    "TASK_RULES",
    "UNAVAILABLE_EXIT_CODE",
    "_QUOTA_MARKERS",
    "_QUOTA_RESET",
    "AgentWorkEngine",
    "AntigravityEngine",
    "CloudEngine",
    "FileArtifactHelper",
    "OllamaEngine",
    "_save_and_check",
    "normalize_engine",
    "quota_message",
    "run_streaming",
]
