"""
The API server's shared state: its database, the assistant's memory, AgentWork, the media folders and the
access token. The route modules read these as `state.db`, `state.VIDEOS_DIR`, ... at request time, so a test
can swap any of them (set_db, set_memory, set_agentwork, or assigning the attribute).
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional

from core.config import AGENT_WORK_DIR, IMAGES_DIR as CONFIG_IMAGES_DIR, VIDEOS_DIR as CONFIG_VIDEOS_DIR, get_config
from core.database import DatabaseManager
from core.embedder import get_memory_embedder
from core.llm import CloudLLM
from core.memory import MemoryManager
from core.task_engines import AgentWorkEngine

logger = logging.getLogger("WebHUD")
SERVER_START_TIME = time.time()
API_VERSION = "2.1.0"

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = os.environ.get("STATE_DB_PATH")
DB_FILE = Path(DB_PATH) if DB_PATH else (WORKSPACE_DIR / "data" / "state.db")

# The finished videos live in TiktokVideos and the images in AgentGallery (ImageGen saves them there). The
# folders are created if they're missing so the server still starts (making one then reports the tool isn't installed).
VIDEOS_DIR = CONFIG_VIDEOS_DIR
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR = CONFIG_IMAGES_DIR
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Without HUD_AUTH_TOKEN only this machine (loopback) may use the API; with it, any client presenting it
HUD_AUTH_TOKEN = get_config("HUD_AUTH_TOKEN", "").strip()
# It opens everything, Take over (the Mini's mouse and keyboard) included: a short one is said loudly at every start
# (2026-10-28 audit 1.4; the Mini's is 48 characters)
MIN_TOKEN_LENGTH = 32
if HUD_AUTH_TOKEN and len(HUD_AUTH_TOKEN) < MIN_TOKEN_LENGTH:
    logging.getLogger("AlwaysOnAgent").warning(
        f"HUD_AUTH_TOKEN is only {len(HUD_AUTH_TOKEN)} characters: it guards the whole API, Take over included. "
        f"Use {MIN_TOKEN_LENGTH} or more (python -c \"import secrets; print(secrets.token_urlsafe(36))\") and give "
        "DeskAI the same value.")

db = DatabaseManager(DB_FILE)


def set_db(database_manager: DatabaseManager):
    """Allows test suites to point the server at an isolated temporary database."""
    global db
    db = database_manager


_memory: Optional[MemoryManager] = None


def get_memory() -> MemoryManager:
    """The assistant's shared memory, bound to the server's current database."""
    global _memory
    if _memory is None or _memory.db is not db:
        # A child of the daemon's logger, so chat's model failures land in logs/worker.log (the root logger's
        # console output is discarded when the agent runs hidden on the Mini)
        _memory = MemoryManager(db, llm=CloudLLM(logger=logging.getLogger("AlwaysOnAgent.chat")), embedder=get_memory_embedder())
    return _memory


def set_memory(memory: MemoryManager):
    """Test hook: use a memory with a fake LLM / embedder."""
    global _memory
    _memory = memory


_agentwork: Optional[AgentWorkEngine] = None


def get_agentwork() -> AgentWorkEngine:
    """The separate AgentWork project tool (the agent only lists its projects and queues jobs)."""
    global _agentwork
    if _agentwork is None:
        _agentwork = AgentWorkEngine(AGENT_WORK_DIR, logger=logger)
    return _agentwork


def set_agentwork(engine):
    """Test hook: use a fake AgentWork."""
    global _agentwork
    _agentwork = engine
