"""
Centralized Configuration & Safe Environment Management
========================================================
Manages configuration from .env and environment variables, provides dynamic
binary discovery (FFmpeg, agy), and exposes secure environment sanitization
for subprocess and sandbox execution.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sys
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))
ENV_FILE = WORKSPACE_DIR / ".env"


def load_env_file(filepath: Optional[Path] = None) -> Dict[str, str]:
    """
    Parses key-value pairs from .env and also sets them in os.environ, so .env is the single source of
    truth (it overrides values inherited from the shell or a service manager) and child tools see the
    active config. Sensitive keys are stripped again for task subprocesses.
    """
    env_path = filepath or ENV_FILE
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values

    try:
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                values[k] = v
                # Always update os.environ with .env value so child tools get active config
                os.environ[k] = v
    except Exception:
        pass
    return values


# Load on module import
_ENV_VALUES = load_env_file()


def get_config(key: str, default: str = "") -> str:
    """Retrieves a configuration value from .env with fallback to os.environ."""
    return _ENV_VALUES.get(key) or os.environ.get(key) or default


# Sensitive keys that must NEVER be passed to arbitrary child processes or AI test runners
SENSITIVE_ENV_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "ALLOWED_CHAT_ID",
    "HUD_AUTH_TOKEN",
    "TIKTOK_USERNAME",
    "TIKTOK_PASSWORD",
    "GROQ_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "MISTRAL_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "ELEVENLABS_API_KEY",
    "GITHUB_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "TAVILY_API_KEY",
    "TAVILY-API_KEY",
    "SERPER_API_KEY",
    "SERPER-API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "BRAVE_API_KEY",
    "HEALTHCHECK_URL",           # its UUID is the only thing that lets anyone ping the check
}
# Any other name that says it holds a secret counts too, so a key added to .env is covered without a code change
# (2026-10-28 audit 1.3: LAPTOP_TAKEOVER_TOKEN, AI_HORDE_API_KEY, GOOGLE_API_KEY and others weren't in the list).
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS|WEBHOOK|CREDENTIAL", re.I)
_secret_names_cache: Tuple[Tuple[int, int], List[str]] = ((-1, -1), [])


def is_secret_name(name: str) -> bool:
    return name.upper() in SENSITIVE_ENV_KEYS or bool(SECRET_NAME.search(name))


def secret_names() -> List[str]:
    """The names in the environment and .env whose values are secret (recomputed when either changes size)."""
    global _secret_names_cache
    mark = (len(os.environ), len(_ENV_VALUES))
    if _secret_names_cache[0] != mark:
        names = {*SENSITIVE_ENV_KEYS, *(k for k in {*os.environ, *_ENV_VALUES} if SECRET_NAME.search(k))}
        _secret_names_cache = (mark, sorted(names))
    return _secret_names_cache[1]


# A credential carried in a URL's query string (?token=<HUD token>, a signed media link's &sig=). Redacted from
# logs whatever its value, so a wrong, old or rotated token in an access-log line never lands on disk either
# (2026-10-07: older DeskAI builds put the HUD token on every image/video URL).
_URL_SECRET_PARAM = re.compile(r"(?i)([?&](?:token|sig|hud_token|access_token)=)[^&\s\"'#<>)\]]+")


def redact_url_tokens(text: str) -> str:
    """Hides the value of credential query parameters (token, sig, ...) in any URL inside text."""
    if not text or "=" not in text:
        return text
    return _URL_SECRET_PARAM.sub(r"\1[REDACTED]", text)


def redact_secrets(text: str) -> str:
    """Replaces the live values of all sensitive environment keys in text with a placeholder."""
    if not text:
        return text
    for k in secret_names():
        val = os.environ.get(k) or _ENV_VALUES.get(k)
        # Skip short values (e.g. chat IDs) to avoid mangling unrelated output
        if val and len(val) >= 8:
            text = text.replace(val, f"[REDACTED:{k}]")
    return text


class LogRedactionFilter(logging.Filter):
    """Scrubs live API keys / tokens, and credential query parameters in URLs, from log records before any
    handler writes them. Used by the daemon's handlers, the supervisor's console and uvicorn's loggers."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = redact_url_tokens(redact_secrets(msg))
        if redacted != msg:
            record.msg = redacted
            record.args = None
        return True


def get_safe_subprocess_env(extra_vars: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Returns a sanitized environment dictionary stripped of all API tokens,
    credentials, and bot secrets. Safe for invoking agent-generated code or tests.
    """
    # Whitelist of standard base system keys needed for process execution
    base_whitelist = [
        "PATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "TEMP",
        "TMP",
        "COMSPEC",
        "PATHEXT",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERPROFILE",
        "WINDIR",
        "APPDATA",
        "LOCALAPPDATA",
        "PYTHONPATH",
        "PYTHONHOME",
    ]

    safe_env: Dict[str, str] = {}
    for k in base_whitelist:
        val = os.environ.get(k)
        if val is not None:
            safe_env[k] = val

    # Enforce non-buffered python and no bytecode caching
    safe_env["PYTHONUNBUFFERED"] = "1"
    safe_env["PYTHONDONTWRITEBYTECODE"] = "1"

    # Merge any safe caller-provided variables, filtering out sensitive names
    if extra_vars:
        for k, v in extra_vars.items():
            if not is_secret_name(k):
                safe_env[k] = str(v)

    return safe_env


LOCKDOWN_DIR = WORKSPACE_DIR / "data" / "lockdown"
# All task work lives outside the agent's own folder: workspaces/<task id> (each task's folder) and
# repos/<name> (the agent's clones of project repos). Default: C:\Projects\AgentWork next to this project.
AGENT_WORK_DIR = Path(get_config("AGENT_WORK_DIR") or (WORKSPACE_DIR.parent / "AgentWork"))
# TiktokVideos: the separate tool that writes, renders and posts the promo videos (its videos/ folder is
# what the HUD and DeskAI serve)
TIKTOK_VIDEOS_DIR = Path(get_config("TIKTOK_VIDEOS_DIR") or (WORKSPACE_DIR.parent / "TiktokVideos"))
VIDEOS_DIR = TIKTOK_VIDEOS_DIR / "videos"


def _int_config(key: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(get_config(key, str(default)).strip()))
    except ValueError:
        return default


# Finished videos TiktokVideos keeps in VIDEOS_DIR (newest first). Until 2026-10-07 the tool's own default (3)
# applied, because the agent strips its environment before starting tools; the agent now passes this on as
# MAX_SAVED_VIDEOS. Alex chose 50 (the Mini has room). A MAX_SAVED_VIDEOS line in TiktokVideos' own .env still
# wins over this one (that tool reads its .env first).
VIDEO_KEEP_COUNT = _int_config("VIDEO_KEEP_COUNT", 50)
# Small JPEG per finished video task, kept after the video itself is pruned, so a task's details still show a
# picture (GET /api/tasks/{id}/thumb). Not under VIDEOS_DIR on purpose: TiktokVideos' pruning sweeps that folder.
TASK_THUMBS_DIR = Path(get_config("TASK_THUMBS_DIR") or (WORKSPACE_DIR / "data" / "task_thumbs"))
TASK_THUMBS_KEEP = _int_config("TASK_THUMBS_KEEP", 200)
# AgentGallery: separate media folder for generated AI images (kept outside the agent repository)
AGENT_GALLERY_DIR = Path(get_config("AGENT_GALLERY_DIR") or (WORKSPACE_DIR.parent / "AgentGallery"))
IMAGES_DIR = AGENT_GALLERY_DIR / "images"
# ImageGen: the separate tool that makes images from prompts (it saves them into IMAGES_DIR)
IMAGEGEN_DIR = Path(get_config("IMAGEGEN_DIR") or (WORKSPACE_DIR.parent / "ImageGen"))
# SocialPostEngine: the separate tool that posts the site's pins to Pinterest
SOCIALPOSTENGINE_DIR = Path(get_config("SOCIALPOSTENGINE_DIR") or (WORKSPACE_DIR.parent / "SocialPostEngine"))
# VibeStats: the separate tool that collects the site's numbers (visits, cards sent, subscribers)
VIBESTATS_DIR = Path(get_config("VIBESTATS_DIR") or (WORKSPACE_DIR.parent / "VibeStats"))
# Connectors: the separate tool linking the owner's own accounts (Gmail, read-only) and its subscription database
CONNECTORS_DIR = Path(get_config("CONNECTORS_DIR") or (WORKSPACE_DIR.parent / "Connectors"))
# AgentComputerUse: the separate tool that uses the computer like a person (Edge and apps, real mouse and keyboard)
COMPUTER_USE_DIR = Path(get_config("COMPUTER_USE_DIR") or (WORKSPACE_DIR.parent / "AgentComputerUse"))



def git_lockdown_env() -> Dict[str, str]:
    """
    Variables that hide this PC's GitHub login from a task's tools: git ignores the system and
    user config (where the credential manager is set) and never prompts, and gh sees an empty
    config folder, so a push or gh command fails instead of acting as the owner. It hides the
    login from ordinary commands; the protection rule on GitHub is the backstop.
    """
    gh_dir = LOCKDOWN_DIR / "gh"
    gh_dir.mkdir(parents=True, exist_ok=True)
    empty_config = LOCKDOWN_DIR / "empty.gitconfig"
    if not empty_config.exists():
        empty_config.write_text("", encoding="utf-8")
    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(empty_config),
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GH_CONFIG_DIR": str(gh_dir),
    }


def notify_owner(text: str) -> bool:
    """Alerts from background work: a push notification to the DeskAI app. True if one went out."""
    try:
        from core import push
    except ImportError:
        import push
    try:
        return push.send_alert(text) > 0
    except Exception:
        return False



def resolve_ffmpeg_binary() -> str:
    """Dynamically locates the FFmpeg binary."""
    custom = get_config("FFMPEG_PATH")
    if custom and os.path.isfile(custom):
        return custom

    found = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if found:
        return found

    # Fallback to known local Windows installations if available
    local_candidates = [
        r"C:\ffmpeg\ffmpeg-2025-09-08-git-45db6945e9-full_build\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"
    ]
    for c in local_candidates:
        if os.path.isfile(c):
            return c

    return "ffmpeg"


def resolve_agy_binary() -> str:
    """Dynamically locates the Antigravity CLI binary."""
    custom = get_config("AGY_PATH")
    if custom and os.path.isfile(custom):
        return custom

    found = shutil.which("agy") or shutil.which("agy.exe")
    if found:
        return found

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidate = Path(local_app_data) / "agy" / "bin" / "agy.exe"
        if candidate.is_file():
            return str(candidate)

    return "agy"
