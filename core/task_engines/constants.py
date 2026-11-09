"""Task-engine constants and engine-name helpers
===============================================
Split out of core/task_engines.py §2 on 2026-11-04; moved into core/task_engines/
on 2026-11-04, re-exported by the package for backwards compatibility.

Exit codes, output caps, the engine registry (ENGINES / ENGINE_ALIASES /
normalize_engine), the coding prompts, and quota_message live here so the engine
modules can use them without importing core.task_engines (which would be circular).
"""

import re

# Returned when no model could produce an answer (no keys, rate limits everywhere, network
# errors). The caller may try another engine. An answer whose code fails sandbox QA returns 1
# instead and must not fall back: a weaker model would not do better.
UNAVAILABLE_EXIT_CODE = 75
# Antigravity refused because the account's quota for that model is used up: retrying soon is
# pointless (it resets hours later), so the daemon fails the task at once and says when to retry.
QUOTA_EXIT_CODE = 76
_QUOTA_MARKERS = re.compile(r"quota reached|RESOURCE_EXHAUSTED", re.IGNORECASE)
_QUOTA_RESET = re.compile(r"Resets in ([0-9hms]+)")
SAFE_TEST_NAME = re.compile(r"[\w./-]+")        # a test file a task wrote may be run only under a plain name
OUTPUT_HEAD, OUTPUT_TAIL = 200, 400              # lines of a task's output kept (run_streaming)
OUTPUT_LOG_LINES = 100                           # lines logged at INFO; the rest at DEBUG


def quota_message(output: str) -> str | None:
    """A one-line explanation when an engine's output says its quota is used up, else None."""
    if not _QUOTA_MARKERS.search(output or ""):
        return None
    reset = _QUOTA_RESET.search(output)
    return ("Antigravity's quota for this model is used up" +
            (f"; it resets in {reset.group(1)}" if reset else "") + ". Retry the task (/retry) after that.")

ENGINES = ("auto", "antigravity", "cloud", "ollama", "agentwork", "media_pipeline", "social_posts", "weekly_report",
           "computer")
DEFAULT_ENGINE_CHOICES = ("auto", "antigravity", "cloud", "ollama")    # what /engine and the HUD can set
# Older names still accepted everywhere (typed commands, stored settings and tasks)
ENGINE_ALIASES = {"hermes": "cloud", "groq": "cloud",
                  "video": "media_pipeline", "tiktok": "media_pipeline", "social_publisher": "media_pipeline",
                  "pinterest": "social_posts", "pins": "social_posts", "report": "weekly_report",
                  "computer_use": "computer"}


def normalize_engine(name: str | None) -> str:
    """The engine a name refers to; unknown or empty names mean 'auto'."""
    name = (name or "auto").strip().lower()
    name = ENGINE_ALIASES.get(name, name)
    return name if name in ENGINES else "auto"


CODING_SYSTEM_PROMPT = (
    "You are an autonomous AI software engineer executing tasks headlessly for an Always-On Background Worker.\n"
    "Provide the complete, full content of any file requested. Do not merely output the filename. "
    "Format the content clearly so it can be materialized into the project."
)

# Prepended to every task Antigravity runs. It has a real terminal and runs as the PC's user,
# so these rules (plus git_lockdown_env and the audit after each run) keep it to the task's folder.
TASK_RULES = (
    "[Rules for this task]\n"
    "- Work only inside this folder: {folder}\n"
    "  Create, change and delete files only there. Do not read or modify anything outside it.\n"
    "- Never run git push, gh, or anything that changes git configuration, remotes or credentials.\n"
    "- Never kill or restart other programs (especially Google Chrome), and install nothing globally.\n"
    "- If something you need is blocked or missing, stop and explain what you needed. Do not work around it."
)

