"""
AutonomousDaemon setup: database, memory, proactive, watches, logging, shutdown.
(daemon.py §5)

Moved into the core.daemon package on 2026-11-04.
"""
import concurrent.futures
import logging
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.away_shift import AwayShift
    from core.daemon import EngineDispatcher
    from core.memory_review import MemoryReview
    from core.open_loops import OpenLoops
    from core.proactive import Proactive
    from core.watches import Watches

try:
    from core import connectors, events, memory_review, open_loops, proactive, push, watches
    from core.config import LogRedactionFilter
    from core.database import DB_FILE, DatabaseManager
    from core.embedder import get_memory_embedder
    from core.memory import MemoryManager
    from core.processes import kill_process_tree
except ImportError:
    import connectors  # type: ignore[no-redef]
    import events  # type: ignore[no-redef]
    import memory_review  # type: ignore[no-redef]
    import open_loops  # type: ignore[no-redef]
    import proactive  # type: ignore[no-redef]
    import push  # type: ignore[no-redef]
    import watches  # type: ignore[no-redef]
    from config import LogRedactionFilter  # type: ignore[no-redef]
    from database import DB_FILE, DatabaseManager  # type: ignore[no-redef]
    from embedder import get_memory_embedder  # type: ignore[no-redef]
    from memory import MemoryManager  # type: ignore[no-redef]
    from processes import kill_process_tree  # type: ignore[no-redef]


# Scrubs live API keys / tokens from log records; lives in core/config.py
# so the API server and the supervisor's console use the same one.
SecretRedactionFilter = LogRedactionFilter


class _SetupMixin:
    # State this mixin builds in __init__ and the other mixins use. Declared
    # here so mypy can determine their types: several are set from the
    # try/except fallback imports, whose types mypy cannot infer on its own.
    # Annotation-only: these create no runtime attributes, so they cannot
    # shadow any sibling mixin's method in the MRO.
    db: DatabaseManager
    dispatcher: "EngineDispatcher"
    memory: MemoryManager
    proactive: "Proactive"
    watches: "Watches"
    memory_review: "MemoryReview"
    open_loops: "OpenLoops"
    away_shift: "AwayShift"
    logger: logging.Logger
    _last_routine_hb: int
    _offline_checked: bool

    def __init__(
        self,
        workspace_dir: Path,
        interval_seconds: int = 30,
        db_file: str = "data/state.db",
        log_file: str = "logs/worker.log",
        agy_path: str | None = None,
        task_root: Path | None = None,
        agent_work_dir: Path | None = None
    ):
        self._init_paths(workspace_dir, interval_seconds, db_file, log_file)
        self.logger = self._setup_logging()
        self.db = DatabaseManager(self.db_path)
        self._attach_sqlite_log_handler()
        self.dispatcher = self._init_dispatcher(workspace_dir, agy_path, task_root)
        self.memory = self._init_memory()
        self._init_proactive_stack()
        self._init_executor()

    def _init_paths(self, workspace_dir: Path, interval_seconds: int, db_file: str, log_file: str) -> None:
        """Workspace/database/log paths and the running flag."""
        self.workspace_dir = workspace_dir
        self.interval_seconds = interval_seconds
        self.db_path = workspace_dir / db_file
        self.log_path = workspace_dir / log_file
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.running = True

    def _attach_sqlite_log_handler(self) -> None:
        """Attach SQLite Log Handler for zero-lock log streaming."""
        try:
            from core.daemon import SQLiteLogHandler
        except ImportError:
            from daemon import SQLiteLogHandler  # type: ignore[no-redef]
        sql_handler = SQLiteLogHandler(self.db)
        sql_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        sql_handler.addFilter(SecretRedactionFilter())
        self.logger.addHandler(sql_handler)

    def _init_dispatcher(self, workspace_dir: Path, agy_path: str | None, task_root: Path | None) -> "EngineDispatcher":
        """The engine dispatcher; a test daemon (its own database) keeps task work next to that database."""
        try:
            from core.daemon import EngineDispatcher
        except ImportError:
            from daemon import EngineDispatcher  # type: ignore[no-redef]
        # The live agent keeps task work in AgentWork; a test daemon (its own database) keeps it next to that database
        if task_root is None and self.db_path.resolve() != DB_FILE.resolve():
            task_root = self.db_path.parent / "workspaces"
        return EngineDispatcher(workspace_dir=workspace_dir, custom_agy_path=agy_path, db=self.db,
                                logger=self.logger, task_root=task_root)

    def _init_memory(self) -> MemoryManager:
        """Shared long-term memory: tasks get a briefing about the user, results are remembered."""
        memory = MemoryManager(self.db, llm=self.dispatcher.llm,
                               embedder=get_memory_embedder(), log=self.logger)
        self._memory_upkeep: concurrent.futures.Future | None = None
        self._task_recipes: dict[str, Any] = {}      # task id -> the learned recipes it was given (scored when done)
        self._backup_thread: threading.Thread | None = None
        self._mail_thread: threading.Thread | None = None
        self._proactive_thread: threading.Thread | None = None
        self._watch_thread: threading.Thread | None = None
        self._tiktok_stats_thread: threading.Thread | None = None
        self._pins_topup_thread: threading.Thread | None = None
        return memory

    def _init_proactive_stack(self) -> None:
        """Proactive outreach, watches, memory review, open loops, and the away shift."""
        # (memory upkeep and the AI-opportunities research run in the away shift: core/away_shift/)
        self.proactive = proactive.Proactive(self.db, memory=self.memory, llm=self.dispatcher.llm,
                                             send_push=push.send, connectors_mod=connectors)
        self.proactive.presence = events.PRESENCE        # whether Alex is around: what's not urgent waits for him
        self._offline_checked = False                    # the "I was off" check runs on the first heartbeat
        # The Sunday growth review runs inside the dispatcher and needs both (until 2026-10-06 it had neither, and
        # every review fell back to the plain report)
        self.dispatcher.proactive = self.proactive
        # docs/WATCHES.md; the model only for a page watch's opt-in AI check, run when the page really changed
        self.watches = watches.Watches(self.db, connectors, self.proactive, llm=self.dispatcher.llm)
        # The weekly memory check (Saturday), run on the proactive thread
        self.memory_review = memory_review.MemoryReview(self.db, self.memory, self.dispatcher.llm, self.proactive)
        # Memories waiting on something, checked for how they came out: a step of the proactive cycle (core/open_loops/)
        self.open_loops = open_loops.OpenLoops(self.db, self.memory, self.proactive, connectors)
        self.proactive.open_loops = self.open_loops
        try:
            from core import away_shift
        except ImportError:
            import away_shift  # type: ignore[no-redef]
        self.away_shift = away_shift.AwayShift(self.db, memory=self.memory, llm=self.dispatcher.llm,
                                               proactive=self.proactive, open_loops=self.open_loops,
                                               connectors=connectors)
        self.dispatcher.memory = self.memory
        self._last_routine: str | None = None      # the last repeating status line logged, and when
        self._last_routine_hb = 0

    def _init_executor(self) -> None:
        """Worker pool for asynchronous background task execution."""
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="TaskWorker")
        self.active_future: concurrent.futures.Future | None = None
        self.active_task: dict[str, Any] | None = None
        self.active_task_start_time: float = 0.0

        self._setup_signals()

    def _setup_logging(self) -> logging.Logger:
        logger = logging.getLogger("AlwaysOnAgent")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        # This logger has its own file + console handlers; don't also echo through the root logger
        logger.propagate = False

        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] [PID:%(process)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        rf_handler = RotatingFileHandler(
            str(self.log_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        rf_handler.setFormatter(formatter)
        rf_handler.addFilter(SecretRedactionFilter())
        logger.addHandler(rf_handler)

        c_handler = logging.StreamHandler(sys.stdout)
        c_handler.setFormatter(formatter)
        c_handler.addFilter(SecretRedactionFilter())
        logger.addHandler(c_handler)

        return logger

    def _setup_signals(self):
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        self.logger.info(f"Received termination signal ({signum}). Initiating graceful shutdown...")
        self.running = False
        pid_info = self.db.get_active_task_pid()
        if pid_info:
            tid, pid = pid_info
            self.logger.warning(f"Terminating active worker process tree PID {pid} for task [{tid}] on shutdown...")
            kill_process_tree(pid, logger=self.logger)
