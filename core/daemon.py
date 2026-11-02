#!/usr/bin/env python3
"""
Always-On Autonomous AI Background Worker Daemon
=================================================
Runs one background task at a time from the SQLite queue (data/state.db), each in its own folder,
on the engine it asks for (core/task_engines.py: Antigravity, the free cloud models, local Ollama)
or the media pipeline, then records the outcome. Also runs memory upkeep, daily backups and the
stuck-task watchdog.
"""

import argparse
import concurrent.futures
import datetime
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import psutil

try:
    from core import backup, connectors, events, heartbeat_ping, llm_usage, maintenance, proactive, push, watches
    from core import memory as memory_module
    from core import memory_review, open_loops, procedures
    from core.config import AGENT_WORK_DIR, LogRedactionFilter, notify_owner, redact_secrets
    from core.database import DB_FILE, NEEDS_INPUT_EXIT_CODE, DatabaseManager, get_iso_now
    from core.embedder import get_memory_embedder
    from core.housekeeping import LOG_MAX_AGE_DAYS, prune_old_logs
    from core.llm import CloudLLM, OllamaClient
    from core.memory import MemoryManager
    from core.processes import HEADLESS_BROWSER_NAMES, kill_process_tree
    from core.task_engines import (QUOTA_EXIT_CODE, AgentWorkEngine, AntigravityEngine, CloudEngine, OllamaEngine,
                                   normalize_engine, quota_message)
except ImportError:
    import backup
    import connectors
    import events
    import watches
    import heartbeat_ping
    import llm_usage
    import maintenance
    import memory as memory_module
    import memory_review
    import open_loops
    import procedures
    import proactive
    import push
    from config import AGENT_WORK_DIR, LogRedactionFilter, notify_owner, redact_secrets
    from database import DB_FILE, NEEDS_INPUT_EXIT_CODE, DatabaseManager, get_iso_now
    from embedder import get_memory_embedder
    from housekeeping import LOG_MAX_AGE_DAYS, prune_old_logs
    from llm import CloudLLM, OllamaClient
    from memory import MemoryManager
    from processes import HEADLESS_BROWSER_NAMES, kill_process_tree
    from task_engines import (QUOTA_EXIT_CODE, AgentWorkEngine, AntigravityEngine, CloudEngine, OllamaEngine,
                              normalize_engine, quota_message)


TASK_ROOT = AGENT_WORK_DIR / "workspaces"   # each task works in AgentWork/workspaces/<task id>/, outside this project
# Repeating status lines (idle, standby, still running) are logged when they change and then every
# this many heartbeats (20 x 30 s = 10 minutes); logging them every pulse was ~90% of worker.log.
ROUTINE_LOG_EVERY = 20
ACTIVITY_LOG_KEEP = 150      # plain-language lines kept on a computer task (metadata.activity_log)


# Scrubs live API keys / tokens (and ?token=/&sig= values in URLs) from log records; lives in core/config.py
# so the API server and the supervisor's console use the same one.
SecretRedactionFilter = LogRedactionFilter


class EngineDispatcher:
    """Runs a task on its engine (auto, antigravity, cloud, ollama, agentwork) or the media pipeline, in the task's folder."""

    def __init__(
        self,
        workspace_dir: Path,
        custom_agy_path: Optional[str] = None,
        db: Optional[DatabaseManager] = None,
        logger: Optional[logging.Logger] = None,
        task_root: Optional[Path] = None,
        agent_work_dir: Optional[Path] = None
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

    def task_dir(self, task: Dict[str, Any], create: bool = True) -> Path:
        """The folder a task works in: <task root>/<task id>, outside the agent's own files."""
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task.get("id") or "task")) or "task"
        folder = self.task_root / safe_id
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _guard_snapshot(self) -> Optional[Dict[str, Any]]:
        """What a task must not change: the agent's own files (git status) and .env."""
        try:
            status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(self.workspace_dir),
                                    capture_output=True, text=True, timeout=20)
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
        except Exception:
            return None

    def _guard_changes(self, before: Optional[Dict[str, Any]]) -> list:
        after = self._guard_snapshot()
        if before is None or after is None:
            return []
        changed = sorted(l[3:] for l in after["status"] ^ before["status"])
        if after["env"] != before["env"]:
            changed.append(".env")
        return changed

    def execute(self, task: Dict[str, Any], default_engine: str = "auto") -> Tuple[int, str, str, bool]:
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
            notify_owner("⚠️ " + alert)
            return 1, f"{out}\n\n[SAFETY] {alert}", used, fallback
        return code, out, used, fallback

    def _run_engine(self, task: Dict[str, Any], engine: str, work: Path) -> Tuple[int, str, str, bool]:
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

    def _execute_weekly_report(self, task: Dict[str, Any]) -> Tuple[int, str, str, bool]:
        """The weekly numbers: VibeStats, TiktokVideos and SocialPostEngine, put together (core/weekly_report.py)."""
        try:
            from core import weekly_report
        except ImportError:
            import weekly_report
        task_id = task.get("id")

        def progress(p: Dict[str, Any]) -> None:
            if self.db and task_id:
                self.db.set_task_progress(task_id, p)
        try:
            report = weekly_report.build(int((task.get("metadata") or {}).get("days", 7)), on_progress=progress,
                                         db=self.db)
        except Exception as e:
            self.logger.exception("[report] failed")
            return 1, f"The weekly report couldn't be made: {e}", "weekly_report", False
        if self.db:
            weekly_report.remember(self.db, report)
        # Only when every source failed is it a failure; otherwise the report names what's missing
        code = 1 if len(report["failed"]) == 3 else 0
        text = report["text"]
        # The Sunday review (IDEAS #24): the numbers turned into a canvas draft and one chat message. Any problem
        # leaves the plain report, with its usual push
        try:
            try:
                from core import growth_review
            except ImportError:
                import growth_review
            progress({"stage": "report", "label": "Writing the review", "percent": 90, "detail": ""})
            review = growth_review.run(self.db, report, self.llm, proactive=getattr(self, "proactive", None),
                                       name=self.memory.subject() if getattr(self, "memory", None) else "the owner")
            if review:
                text = review["text"]
                meta = task.get("metadata") or {}
                self.db.update_task(task_id, {"metadata": {**meta, "announced": "growth_review"}})
        except Exception as e:
            self.logger.warning(f"[report] the review couldn't be made; sending the plain report: {e}")
        return code, text, "weekly_report", False

    def _execute_social_posts(self, task: Dict[str, Any]) -> Tuple[int, str, str, bool]:
        """Pins go out through SocialPostEngine, a separate program; its progress goes to the task card."""
        try:
            from core import social_posts
        except ImportError:
            import social_posts
        task_id = task.get("id")
        count = (task.get("metadata") or {}).get("count", 1)
        current = self.db.get_task(task_id) if self.db and task_id else None
        if current and current.get("phase") == "failed":
            return 1, "Cancelled before it started: no pins were posted.", "social_posts", False

        def progress(p: Dict[str, Any]) -> None:
            if self.db and task_id:
                self.db.set_task_progress(task_id, p)

        def running(pid: int) -> None:
            if self.db and task_id:
                self.db.set_task_pid(task_id, pid)       # a cancel ends this process tree
        try:
            result = social_posts.post_pins(count, on_progress=progress, on_start=running)
            return 0, social_posts.summary(result), "social_posts", False
        except social_posts.SocialPostError as e:
            self.logger.warning(f"[pins] {e}")
            return 1, f"The pins couldn't be posted: {e}", "social_posts", False
        finally:
            if self.db and task_id:
                self.db.set_task_pid(task_id, None)

    def _execute_computer(self, task: Dict[str, Any]) -> Tuple[int, str, str, bool]:
        """
        A computer task: AgentComputerUse (a separate program) does the goal on this PC's screen. It runs from the
        agent's own process, which is in the owner's desktop session, so the tool can see the screen and use the
        mouse and keyboard. Its steps go to the task card; it stops before anything that submits or posts.
        """
        try:
            from core import computer_use
        except ImportError:
            import computer_use
        meta = task.get("metadata", {})
        task_id = task.get("id")
        goal = meta.get("goal") or task.get("prompt") or ""
        # What it did, in plain sentences ("Opening Notepad", "Pressing Enter"), kept on the task for its card's
        # details (2026-10-10, the owner: "a log ... on the task card, so I can see the logs and any errors").
        # The tool writes the sentences (AgentComputerUse activity.say); typed secrets are already hidden in them.
        log: List[Dict[str, Any]] = []

        def note(text: str, level: str = "info", step: Any = None, kind: str = "") -> None:
            # kind: what kind of step (open, type, click, save, plan), so DeskAI colors the log by it
            log.append({"t": datetime.datetime.now().strftime("%H:%M:%S"), "step": step, "text": text[:240],
                        "level": level, "kind": kind or ""})
            del log[:-ACTIVITY_LOG_KEEP]

        def progress(p: Dict[str, Any]) -> None:
            if p.get("say"):
                note(p["say"], p.get("level") or "info", p.get("step"), p.get("kind") or "")
            if self.db and task_id:
                if p.get("say"):
                    p = {**p, "message": (f"Step {p['step']} · " if p.get("step") else "") + p["say"]}
                elif p.get("step"):
                    p = {**p, "message": f"Step {p['step']}: {p.get('action')}"
                                         + (f" - {p['observation']}" if p.get("observation") else "")}
                self.db.set_task_progress(task_id, p)

        def running(pid: int) -> None:
            if self.db and task_id:
                self.db.set_task_pid(task_id, pid)       # a cancel ends this process tree

        def needs_you(req: Dict[str, Any]) -> None:
            """The tool waits for the owner: a loud alert with the screenshot, reminders until answered."""
            if self.db and task_id:
                self.db.set_task_progress(task_id, {"message": f"Needs you: {req.get('question', '')[:160]}",
                                                    "request_id": req.get("id")})
            computer_use.alert_owner(req, task_id)

        current = self.db.get_task(task_id) if self.db and task_id else None
        if current and current.get("phase") == "failed":
            return 1, "Cancelled before it started: nothing was done on the computer.", "computer", False
        try:
            try:
                result = computer_use.run(["run", goal, "--go", "--close", "--ask-owner",
                                           "--max-steps", str(meta.get("max_steps", 25))],
                                          on_progress=progress, on_start=running, on_needs_you=needs_you,
                                          timeout=4 * 3600)
            except computer_use.ComputerUseError as e:
                self.logger.warning(f"[computer] {e}")
                note(f"Error: {e}", "error")
                self._save_activity_log(task_id, meta, log)
                return 1, f"The computer task couldn't run: {e}", "computer", False
            meta.update(result=result)
            text = computer_use.describe(result)
            status = result.get("status")
            if not log or not log[-1]["text"].startswith(("Done", "Needs", "Stopped", "Failed", "Ran out")):
                note(text.split("\n")[0], "ok" if status == "done" else "warn" if str(status).startswith("needs")
                     else "error")
            self._save_activity_log(task_id, meta, log)
            return (0 if status == "done" else 1), text, "computer", False
        finally:
            if task_id:
                try:
                    computer_use.cancel_for_task(task_id, reason="computer task ended")
                except Exception as ex:
                    self.logger.debug(f"[computer] cancel_for_task cleanup: {ex}")

    def _save_activity_log(self, task_id: Optional[str], meta: Dict[str, Any], log: List[Dict[str, Any]]) -> None:
        """A computer task's plain-language log on the task (metadata.activity_log), for its card's details."""
        meta["activity_log"] = log
        if self.db and task_id:
            self.db.update_task(task_id, {"metadata": meta})

    def _thumb_for(self, task: Dict[str, Any], video_path: Path, meta: Dict[str, Any]) -> bool:
        """Saves the task's small thumbnail (core/task_thumbs.py), which outlives the video's pruning, and notes
        it in `meta`. Cosmetic: a failure never fails the task. True when `meta` changed."""
        try:
            try:
                from core import task_thumbs
            except ImportError:
                import task_thumbs
            made = task_thumbs.make(task, video_path)
            if made and meta.get("thumb") != made.name:
                meta["thumb"] = made.name
                return True
        except Exception as e:
            self.logger.debug(f"[media] Task thumbnail skipped: {e}")
        return False

    def _execute_media_pipeline(self, task: Dict[str, Any]) -> Tuple[int, str, str, bool]:
        """
        Videos are made by TiktokVideos, a separate program: it writes the quote (unless the owner gave one),
        renders, and posts to TikTok. This runs it, relays its progress to the task card and records its AI
        usage; the owner watches the finished video in DeskAI. A cancel ends the program (its process tree).
        """
        try:
            from core import tiktok_videos
            from core.llm_usage import UsageLog, usage_purpose
        except ImportError:
            import tiktok_videos
            from llm_usage import UsageLog, usage_purpose
        meta = task.get("metadata", {})
        action = meta.get("action", "video")
        task_id = task.get("id")

        def cancelled() -> bool:
            current = self.db.get_task(task_id) if self.db and task_id else None
            return bool(current) and current.get("phase") == "failed"

        def progress(p: Dict[str, Any]) -> None:
            if self.db and task_id:
                self.db.set_task_progress(task_id, p)

        def running(pid: int) -> None:
            if self.db and task_id:
                self.db.set_task_pid(task_id, pid)       # a cancel ends this process tree

        existing = meta.get("video_path")
        video_path = Path(existing) if existing and Path(existing).exists() else None
        if cancelled():                                   # cancelled while it waited: don't start at all
            return 1, "Cancelled before it started: nothing was rendered, sent or posted.", "media_pipeline", False
        try:
            if video_path is None:
                args = ["render"]
                if meta.get("quote"):
                    args += ["--quote", str(meta["quote"])]
                if meta.get("scene"):                      # 'chest' / 'bottle' / 'card'; none: TiktokVideos rotates
                    args += ["--scene", str(meta["scene"])]
                self.logger.info(f"[media] TiktokVideos: rendering (quote: {meta.get('quote') or 'a fresh one'})")
                result = tiktok_videos.run(args, on_progress=progress, on_start=running)
                if self.db and result.get("ai_usage"):
                    log = UsageLog(self.db)
                    with usage_purpose("affirmation"):
                        for call in result["ai_usage"]:
                            log.record(call.get("model", "?"), call, provider="gemini")
                video_path = Path(result["video"])
                meta.update(video_path=str(video_path), quote=result.get("quote"), title=result.get("title"))
                self._thumb_for(task, video_path, meta)
                if self.db:
                    self.db.update_task(task_id, {"metadata": meta})
            else:
                self.logger.info(f"[media] Using the existing video {video_path.name}")
                if self._thumb_for(task, video_path, meta) and self.db:
                    self.db.update_task(task_id, {"metadata": meta})

            if action in ("tiktok", "publish_tiktok"):
                if cancelled():
                    return 1, f"Cancelled before posting: {video_path.name} was not posted to TikTok.", "social_publisher", False
                self.logger.info(f"[media] TiktokVideos: posting {video_path.name} to TikTok")
                # TiktokVideos waits up to 7 min for TikTok to finish taking the file before it presses Post
                tiktok_videos.run(["post", "--video", video_path.name], on_progress=progress, on_start=running,
                                  timeout=900)
                return 0, f"Successfully posted video to TikTok: {video_path.name}", "social_publisher", False

            return 0, f"Rendered {video_path.name}: \"{meta.get('quote') or ''}\"", "media_pipeline", False
        except tiktok_videos.TiktokVideosError as e:
            if cancelled():
                return 1, "Cancelled: the video was stopped and nothing was posted.", "media_pipeline", False
            engine = "social_publisher" if video_path is not None else "media_pipeline"
            what = (f"Video ready ({video_path.name}) but TikTok auto-upload failed" if video_path is not None
                    else "The video couldn't be made")
            self.logger.warning(f"[media] {what}: {e}")
            return 1, f"{what}: {e}", engine, False
        except Exception as e:
            self.logger.exception(f"[media] Error in media pipeline: {e}")
            return 1, f"Media pipeline execution failed: {e}", "media_pipeline", False
        finally:
            if self.db and task_id:
                self.db.set_task_pid(task_id, None)


class SQLiteLogHandler(logging.Handler):
    """Writes log events directly into SQLite system_logs table (capped at ~500 rows)."""
    def __init__(self, db: DatabaseManager, max_rows: int = 500):
        super().__init__()
        self.db = db
        self.max_rows = max_rows

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.db.append_log(record.levelname, msg, max_rows=self.max_rows)
        except Exception:
            self.handleError(record)


class AutonomousDaemon:
    """The heartbeat pulse daemon with asynchronous execution pool and SQLite WAL backend."""

    def __init__(
        self,
        workspace_dir: Path,
        interval_seconds: int = 30,
        db_file: str = "data/state.db",
        log_file: str = "logs/worker.log",
        agy_path: Optional[str] = None,
        task_root: Optional[Path] = None,
        agent_work_dir: Optional[Path] = None
    ):
        self.workspace_dir = workspace_dir
        self.interval_seconds = interval_seconds
        self.db_path = workspace_dir / db_file
        self.log_path = workspace_dir / log_file
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.running = True

        self.logger = self._setup_logging()
        self.db = DatabaseManager(self.db_path)

        # Attach SQLite Log Handler for zero-lock log streaming
        sql_handler = SQLiteLogHandler(self.db)
        sql_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        sql_handler.addFilter(SecretRedactionFilter())
        self.logger.addHandler(sql_handler)

        # The live agent keeps task work in AgentWork; a test daemon (its own database) keeps it next to that database
        if task_root is None and self.db_path.resolve() != DB_FILE.resolve():
            task_root = self.db_path.parent / "workspaces"
        self.dispatcher = EngineDispatcher(workspace_dir=workspace_dir, custom_agy_path=agy_path, db=self.db,
                                           logger=self.logger, task_root=task_root)
        # Shared long-term memory: tasks get a briefing about the user, results are remembered
        self.memory = MemoryManager(self.db, llm=self.dispatcher.llm,
                                    embedder=get_memory_embedder(), log=self.logger)
        self._memory_upkeep: Optional[concurrent.futures.Future] = None
        self._task_recipes: Dict[str, Any] = {}      # task id -> the learned recipes it was given (scored when done)
        self._backup_thread: Optional[threading.Thread] = None
        self._mail_thread: Optional[threading.Thread] = None
        self._proactive_thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._tiktok_stats_thread: Optional[threading.Thread] = None
        self._pins_topup_thread: Optional[threading.Thread] = None
        # (memory upkeep and the AI-opportunities research run in the away shift: core/away_shift.py)
        self.proactive = proactive.Proactive(self.db, memory=self.memory, llm=self.dispatcher.llm,
                                             send_push=push.send, connectors_mod=connectors)
        self.proactive.presence = events.PRESENCE        # whether Alex is around: what's not urgent waits for him
        self._offline_checked = False                    # the "I was off" check runs on the first heartbeat
        # The Sunday growth review runs inside the dispatcher and needs both (until 2026-10-06 it had neither, and
        # every review fell back to the plain report)
        self.dispatcher.proactive = self.proactive
        self.watches = watches.Watches(self.db, connectors, self.proactive)     # docs/WATCHES.md
        # The weekly memory check (Saturday), run on the proactive thread
        self.memory_review = memory_review.MemoryReview(self.db, self.memory, self.dispatcher.llm, self.proactive)
        # Memories waiting on something, checked for how they came out: a step of the proactive cycle (core/open_loops.py)
        self.open_loops = open_loops.OpenLoops(self.db, self.memory, self.proactive, connectors)
        self.proactive.open_loops = self.open_loops
        try:
            from core import away_shift
        except ImportError:
            import away_shift
        self.away_shift = away_shift.AwayShift(self.db, memory=self.memory, llm=self.dispatcher.llm,
                                               proactive=self.proactive, open_loops=self.open_loops,
                                               connectors=connectors)
        self.dispatcher.memory = self.memory
        self._last_routine: Optional[str] = None      # the last repeating status line logged, and when
        self._last_routine_hb = 0

        # Worker pool for asynchronous background task execution
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="TaskWorker")
        self.active_future: Optional[concurrent.futures.Future] = None
        self.active_task: Optional[Dict[str, Any]] = None
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

    def heartbeat_cycle(self) -> None:
        """Executes a non-blocking heartbeat pulse."""
        iso_now = get_iso_now()

        # Update heartbeat metadata
        curr_hb = int(self.db.get_state("total_heartbeats", "0")) + 1
        self.db.set_state("total_heartbeats", str(curr_hb))
        if not self._offline_checked:
            # Before last_heartbeat is overwritten: a long gap means the agent (or the Mini) was off. 2026-10-17 to 22
            # it was off 5 days after a power cut, 10 posts were missed, and nothing said so (AUDIT_BACKGROUND B1)
            self._offline_checked = True
            if self.db_path.resolve() == DB_FILE.resolve():
                try:
                    self.proactive.offline_notice(self.db.get_state("last_heartbeat", ""))
                except Exception as e:
                    self.logger.warning(f"offline check failed: {e}")
        self.db.set_state("last_heartbeat", iso_now)

        # Periodic WAL compaction and storage maintenance every 20 heartbeats
        if curr_hb % 20 == 0:
            self.db.checkpoint_wal("PASSIVE")
            task_running = self.active_future is not None and not self.active_future.done()
            if task_running:
                # A render writes/reads its temp frames for minutes; cleaning now would truncate its video
                self.logger.info("Skipping storage maintenance while a task is running.")
            else:
                try:
                    # Thumbnails for finished video tasks that lack one (older tasks, a failed grab), taken before
                    # the cleanup below can prune their videos; at most a few frames per pass
                    from core import task_thumbs
                    if self.db:
                        made = task_thumbs.backfill(self.db, limit=10)
                        if made:
                            self.logger.info(f"Saved {made} task thumbnail(s)")
                except Exception as e:
                    self.logger.debug(f"Task thumbnail backfill: {e}")
                try:
                    try:
                        from core import tiktok_videos
                    except ImportError:
                        import tiktok_videos
                    # Only stale leftovers: a render running elsewhere (e.g. a preview) may be using fresh files
                    tiktok_videos.cleanup(older_than=3600)
                except Exception as e:
                    self.logger.debug(f"Storage maintenance check: {e}")
                removed = prune_old_logs(self.log_path.parent)
                if removed:
                    self.logger.info(f"Removed log files unused for {LOG_MAX_AGE_DAYS}+ days: {', '.join(removed)}")
                try:
                    from core.housekeeping import prune_old_workspaces
                    from core.config import AGENT_WORK_DIR
                    removed_ws = prune_old_workspaces(Path(AGENT_WORK_DIR) / "workspaces", max_age_days=7)
                    if removed_ws:
                        self.logger.info(f"Storage maintenance: Pruned {len(removed_ws)} abandoned workspace(s) ({', '.join(removed_ws[:5])})")
                except Exception as e:
                    self.logger.debug(f"Workspace cleanup check: {e}")
                try:
                    from core import documents
                    old_uploads = documents.prune()
                    if old_uploads:
                        self.logger.info(f"Removed {old_uploads} chat attachment(s) older than {documents.KEEP_DAYS} days")
                except Exception as e:
                    self.logger.debug(f"Attachment cleanup: {e}")
                try:
                    # Shared repo downloads left by a failed or interrupted send, and the chat's video thumbnails
                    # (one per video ever shown): nothing else removes them (2026-10-28 audit 4.5)
                    from core.housekeeping import prune_old_files
                    from core.config import VIDEOS_DIR
                    left = prune_old_files(self.db_path.parent / "tmp" / "share", 3600)
                    thumbs = prune_old_files(Path(VIDEOS_DIR).parent / ".file_thumbnails", 30 * 86400)
                    if left or thumbs:
                        self.logger.info(f"Removed {left} leftover share download(s), {thumbs} old chat thumbnail(s)")
                except Exception as e:
                    self.logger.debug(f"Share/thumbnail cleanup: {e}")
                # Memory upkeep (consolidate) moved to AwayShift (docs/archive/PROACTIVE_RESEARCH.md 1a, 1l)

        # Daily memory backup (also while in standby), on its own thread
        if (self._backup_thread is None or not self._backup_thread.is_alive()) and backup.backup_due(self.db):
            # not a daemon thread: shutdown waits for it (~a second) instead of killing it mid-copy
            self._backup_thread = threading.Thread(target=self._run_backup, name="Backup")
            self._backup_thread.start()

        # Gmail into the subscription database every connectors.MAIL_SYNC_MINUTES (live agent only), on its own
        # thread: the first sync reads months of mail
        live = self.db_path.resolve() == DB_FILE.resolve()
        if live and (self._mail_thread is None or not self._mail_thread.is_alive()) and connectors.mail_sync_due(self.db):
            self._mail_thread = threading.Thread(target=connectors.run_mail_sync, args=(self.db, backup.notify_owner_of),
                                                 name="MailSync", daemon=True)
            self._mail_thread.start()

        # An outside service notices if these pings stop (docs/IDEAS.md #1; live agent only, off without HEALTHCHECK_URL)
        if live:
            heartbeat_ping.maybe_ping(self.logger, db=self.db)

        # The agent reaching out first (brief, notices, questions) every PROACTIVE_EVERY_MINUTES (live agent only)
        # ...and at once when Alex opens the app (events.OWNER_ARRIVED): the brief and held messages arrive as he does
        arrived = events.OWNER_ARRIVED.is_set()
        if live and (self._proactive_thread is None or not self._proactive_thread.is_alive()) and \
                (arrived or self.proactive.due()):
            events.OWNER_ARRIVED.clear()
            self._proactive_thread = threading.Thread(target=self._run_proactive, name="Proactive", daemon=True)
            self._proactive_thread.start()

        # The TikTok numbers once a day for the quote writer's "what resonates" (live agent only; not during a task:
        # a render is using the machine)
        if live and self.active_future is None and \
                (self._tiktok_stats_thread is None or not self._tiktok_stats_thread.is_alive()):
            try:
                from core import tiktok_videos as _tv
            except ImportError:
                import tiktok_videos as _tv
            if _tv.stats_due(self.db):
                self._tiktok_stats_thread = threading.Thread(target=_tv.run_daily_stats, args=(self.db, self.logger),
                                                             name="TikTokStats", daemon=True)
                self._tiktok_stats_thread.start()

        # Pinterest never runs dry: once a day, when few pins wait, new drafts and their preview go to the owner
        # (core/social_posts.py run_topup; live agent only, not during a task)
        if live and self.active_future is None and \
                (self._pins_topup_thread is None or not self._pins_topup_thread.is_alive()):
            try:
                from core import social_posts as _sp
                from core.config import IMAGES_DIR as _images
            except ImportError:
                import social_posts as _sp
                from config import IMAGES_DIR as _images
            if _sp.topup_due(self.db):
                self._pins_topup_thread = threading.Thread(
                    target=_sp.run_topup, args=(self.db, self.proactive, self.logger, _images),
                    name="PinsTopUp", daemon=True)
                self._pins_topup_thread.start()

        # Mail watches every watches.WATCH_EVERY_MINUTES (live agent only): plain searches, no AI
        if live and (self._watch_thread is None or not self._watch_thread.is_alive()) and self.watches.due():
            self._watch_thread = threading.Thread(target=self._run_watches, name="Watches", daemon=True)
            self._watch_thread.start()
        # Away shift: background memory upkeep, open loops research, interests, patterns, and opportunity scans
        # when the app has been closed for 2 hours (docs/archive/PROACTIVE_RESEARCH.md 1a, 1l)
        if live and not self.away_shift.is_running() and self.away_shift.due():
            self.away_shift.start()

        status = self.db.get_state("daemon_status", "idle")

        # Check if daemon was placed in Standby/Sleep mode via DeskAI / the Web HUD
        if status in ("paused", "stopped", "sleep"):
            if self.active_future is not None and not self.active_future.done() and self._task_overdue():
                self._abandon_stuck_task()
            if self.active_future is not None:
                if not self.active_future.done():
                    elapsed = time.time() - self.active_task_start_time
                    self._log_routine(curr_hb, f"standby-running:{self.active_task['id']}",
                                      f"Heartbeat #{curr_hb}: standby, finishing task [{self.active_task['id']}] "
                                      f"({elapsed:.0f}s elapsed).")
                    return
                # Finalize active task that just completed
                try:
                    exit_code, output_text, engine_used, fallback_triggered = self.active_future.result()
                except Exception as e:
                    self.logger.exception(f"Unhandled exception in task worker thread: {e}")
                    exit_code, output_text, engine_used, fallback_triggered = 1, str(e), "unknown", False

                self._finalize_task_completion(
                    self.active_task, exit_code, output_text, engine_used, fallback_triggered
                )
                self.active_future = None
                self.active_task = None
                self.active_task_start_time = 0.0

            self._log_routine(curr_hb, "standby", f"Heartbeat #{curr_hb}: standby (0% CPU/GPU). "
                                                  "Send /start_daemon or any task to wake it.")
            return

        # 1. Check on currently running background task (if any)
        if self.active_future is not None and not self.active_future.done() and self._task_overdue():
            self._abandon_stuck_task()
        if self.active_future is not None:
            if not self.active_future.done():
                elapsed = time.time() - self.active_task_start_time
                self._log_routine(curr_hb, f"running:{self.active_task['id']}",
                                  f"Heartbeat #{curr_hb}: task [{self.active_task['id']}] running ({elapsed:.0f}s elapsed).")
                return

            # Active future is done! Process completion
            try:
                exit_code, output_text, engine_used, fallback_triggered = self.active_future.result()
            except Exception as e:
                self.logger.exception(f"Unhandled exception in task worker thread: {e}")
                exit_code, output_text, engine_used, fallback_triggered = 1, str(e), "unknown", False

            self._finalize_task_completion(
                self.active_task, exit_code, output_text, engine_used, fallback_triggered
            )
            self.active_future = None
            self.active_task = None
            self.active_task_start_time = 0.0

        # 2. Check for next pending task in queue (none while a restart waits for the queue to drain)
        if maintenance.holding():
            self._log_routine(curr_hb, "held", f"Heartbeat #{curr_hb}: a restart is waiting; not starting new tasks.")
            return
        task = self.db.get_next_backlog_task()
        if not task:
            self.db.set_state("daemon_status", "idle")
            self._log_routine(curr_hb, "idle", f"Heartbeat #{curr_hb}: idle, no tasks waiting.")
            return

        # 3. Dispatch new task asynchronously
        task_id = task["id"]
        task_title = task["title"]
        self.logger.info(f"Picked up pending task: [{task_id}] '{task_title}'")

        # Mark in_progress in DB
        self.db.update_task(task_id, {
            "phase": "in_progress",
            "started_at": iso_now
        })
        self.db.set_state("daemon_status", "running_task")

        default_engine = self.db.get_state("default_engine", "auto")
        self.active_task = task
        self.active_task_start_time = time.time()

        # Submit to non-blocking worker thread
        self.active_future = self.executor.submit(
            self.dispatcher.execute, self._with_memory_briefing(task), default_engine
        )
        # The finished task is recorded (and "done" announced) on the next heartbeat: wake it now, not in 30 s
        self.active_future.add_done_callback(lambda _f: events.wake_daemon())
        self.logger.info(f"Task [{task_id}] dispatched to asynchronous execution worker.")

    def _log_routine(self, heartbeat: int, kind: str, message: str) -> None:
        """Logs a repeating status line when it changes, and otherwise every ROUTINE_LOG_EVERY heartbeats."""
        if kind != self._last_routine or heartbeat - self._last_routine_hb >= ROUTINE_LOG_EVERY:
            self.logger.info(message)
            self._last_routine, self._last_routine_hb = kind, heartbeat

    WATCHDOG_MIN_SECONDS = 1800      # renders legitimately take ~7 min despite a 300 s task limit

    def _task_overdue(self) -> bool:
        """A backstop for true hangs: twice the task's limit, never under 30 minutes."""
        limit = max(2 * int((self.active_task or {}).get("timeout_seconds") or 300), self.WATCHDOG_MIN_SECONDS)
        return time.time() - self.active_task_start_time > limit

    def _abandon_stuck_task(self) -> None:
        """
        Fails a task that never returned, stops what can be stopped, and frees the queue. A Python
        thread can't be killed, so the stuck worker is left behind and a fresh worker takes over;
        whatever it eventually returns is ignored.
        """
        task = self.active_task or {}
        task_id = task.get("id", "?")
        elapsed = time.time() - self.active_task_start_time
        self.logger.error(f"Watchdog: task [{task_id}] has run {elapsed / 60:.0f} min without finishing; abandoning it.")
        pid_info = self.db.get_active_task_pid()
        if pid_info and pid_info[0] == task_id:
            kill_process_tree(pid_info[1], logger=self.logger)
        # In-process work (video renders) runs browsers and FFmpeg as our own child processes
        for child in psutil.Process().children(recursive=True):
            try:
                if any(name in child.name().lower() for name in HEADLESS_BROWSER_NAMES):
                    kill_process_tree(child.pid, logger=self.logger)
            except psutil.Error:
                pass
        self.db.update_task(task_id, {
            "phase": "failed", "completed_at": get_iso_now(), "exit_code": -1,
            "last_error": f"Watchdog: no result after {elapsed / 60:.0f} minutes; the task was stopped.",
        })
        self.db.set_task_pid(task_id, None)
        self.executor.shutdown(wait=False)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="TaskWorker")
        self.active_future, self.active_task, self.active_task_start_time = None, None, 0.0
        self.db.end_busy_status()
        notify_owner(f"⚠️ Task {task_id} \"{task.get('title', '')}\" ran {elapsed / 60:.0f} minutes without "
                     f"finishing, so it was stopped and marked failed. The queue continues.")

    def _run_watches(self) -> None:
        try:
            self.watches.run_check()
        except Exception as e:
            self.logger.warning(f"Watch check failed: {e}")

    def _run_proactive(self) -> None:
        try:
            self.proactive.run_cycle()
        except Exception as e:
            self.logger.warning(f"Proactive cycle failed: {e}")
        try:
            now = self.proactive.now()
            if self.proactive.settings()["enabled"] == "1" and not self.proactive.quiet(now) and \
                    self.memory_review.due(now):
                self.memory_review.run(now)
        except Exception as e:
            self.logger.warning(f"Memory check failed: {e}")

    def _run_backup(self) -> None:
        live = self.db_path.resolve() == DB_FILE.resolve()          # test databases never go to the mirror
        try:
            result = backup.create_backup(self.db_path, local_dir=self.db_path.parent / "backups",
                                          mirror=backup.mirror_dir() if live else None)
        except Exception as e:
            result = backup.BackupResult(ok=False, errors=[str(e)])
        backup.record_result(self.db, result)
        if result.complete:
            self.logger.info(result.summary())
        else:
            self.logger.warning(result.summary())
            if live:
                backup.notify_owner_of(f"⚠️ Memory backup problem\n{result.summary()}")

    def _with_memory_briefing(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Copy of the task whose prompt starts with what memory knows that's relevant to it, and the learned recipes
        for its area (core/procedures.py; their outcome is scored when the task finishes)."""
        engine = normalize_engine(task.get("engine") or "auto")
        if engine in ("media_pipeline", "social_posts", "weekly_report", "computer"):
            return task                  # no AI prompt to brief: these run fixed tools
        try:
            recipes = self.memory.procedures.relevant(task.get("prompt", ""), areas=[procedures.task_area(task)])
        except Exception as e:
            self.logger.warning(f"Could not look up procedures for task [{task.get('id')}]: {e}")
            recipes = []
        if recipes:
            self._task_recipes[task["id"]] = recipes
        lessons = self.memory.procedures.block(recipes, "[Lessons from earlier work like this]") if recipes else ""
        if engine == "agentwork":
            # AgentWork gets the owner's instruction (the project has its own rules), plus what earlier jobs taught
            return {**task, "prompt": f"{task.get('prompt', '')}\n\n{lessons}"} if lessons else task
        try:
            briefing = self.memory.task_context(task.get("prompt", ""))
        except Exception as e:
            self.logger.warning(f"Could not build memory briefing for task [{task.get('id')}]: {e}")
            briefing = ""
        preamble = "\n\n".join(p for p in (briefing, lessons) if p)
        if not preamble:
            return task
        return {**task, "prompt": f"{preamble}\n\n[Task]\n{task.get('prompt', '')}"}

    def _learn_from_task(self, task: Dict[str, Any], exit_code: int, earlier: Optional[Dict[str, Any]],
                         summary: str) -> None:
        """Scores the recipes the task was given, and learns one when it succeeded only after failing."""
        try:
            recipes = self._task_recipes.pop(task["id"], [])
            if recipes:
                ok = None if exit_code == NEEDS_INPUT_EXIT_CODE else exit_code == 0
                self.memory.procedures.record_outcome(recipes, {r["id"]: ok for r in recipes})
            earlier_output = (earlier or {}).get("output_summary") or (earlier or {}).get("last_error")
            if exit_code == 0 and task.get("retry_count", 0) > 0 and earlier_output:
                self.memory._bg.submit(self.memory.procedures.learn_from_task, task, earlier_output, summary)
        except Exception as e:
            self.logger.warning(f"Procedure upkeep for task [{task.get('id')}] failed: {e}")

    def _remember_task_result(self, task: Dict[str, Any], exit_code: int, engine_used: str, summary: str) -> None:
        try:
            outcome = "finished successfully" if exit_code == 0 else f"failed (exit code {exit_code})"
            excerpt = " ".join((summary or "").split())[:400]
            folder = self.dispatcher.task_dir(task, create=False)
            files = folder.is_dir() and any(p.is_file() for p in folder.rglob("*"))
            self.memory.record_event(
                f"Background task {task['id']} \"{task['title']}\" {outcome} via {engine_used}."
                + (f" Its files are in {folder}." if files else "")
                + (f" Result excerpt: {excerpt}" if excerpt else "")
            )
        except Exception as e:
            self.logger.warning(f"Could not record task result in memory: {e}")

    def _finalize_task_completion(
        self,
        task: Dict[str, Any],
        exit_code: int,
        output_text: str,
        engine_used: str,
        fallback_triggered: bool
    ):
        task_id = task["id"]
        task_title = task["title"]
        finish_iso = get_iso_now()
        self.db.set_task_progress(task_id, None)

        # Format output summary (secrets scrubbed before it reaches the DB, HUD, or DeskAI)
        output_text = redact_secrets(output_text or "")
        if len(output_text) > 1500:
            summary = output_text[:500] + "\n...[TRUNCATED]...\n" + output_text[-1000:]
        else:
            summary = output_text

        # If the task was cancelled (or otherwise moved) while running, keep the user's
        # decision: record the output but do not overwrite phase / requeue it.
        current = self.db.get_task(task_id)
        if current and current.get("phase") != "in_progress":
            self.logger.info(
                f"Task [{task_id}] finished (exit {exit_code}) but is now '{current.get('phase')}' "
                "(cancelled or modified externally). Preserving its current phase."
            )
            self.db.update_task(task_id, {
                "engine_used": engine_used,
                "fallback_triggered": fallback_triggered,
                "output_summary": summary
            })
            self._task_recipes.pop(task_id, None)        # cancelled: no verdict on its recipes
            self.db.end_busy_status()
            self.db.recalculate_overall_phase()
            return

        update_payload = {
            "exit_code": exit_code,
            "engine_used": engine_used,
            "fallback_triggered": fallback_triggered,
            "completed_at": finish_iso,
            "output_summary": summary
        }

        needs_input = exit_code == NEEDS_INPUT_EXIT_CODE and normalize_engine(task.get("engine") or "") == "agentwork"
        if exit_code == 0 or needs_input:
            if needs_input:
                # AgentWork ran but changed nothing: blocked or unsure. Not a success, and retrying won't help
                self.logger.info(f"Task [{task_id}] changed nothing: needs the owner's input.")
                update_payload["output_summary"] = ("⚠️ Needs your input: the job changed no files. Its explanation "
                                                    "is below.\n\n" + summary)
            else:
                self.logger.info(f"Task [{task_id}] succeeded via engine '{engine_used}'.")
                if (task.get("metadata") or {}).get("project") == "alwaysonagent":
                    # A change to the agent itself waits in AgentWork's copy until the owner applies it
                    update_payload["output_summary"] = (
                        summary + f"\n\n▶ To put this change live, say \"apply {task_id}\": the tests run first, "
                        "then a restart and a health check, with an automatic rollback if anything's wrong.")
            update_payload["phase"] = "completed"
            update_payload["last_error"] = None
            self.db.clear_blockers_for_task(task_id)

        else:
            retries = task.get("retry_count", 0) + 1
            max_retries = task.get("max_retries", 2)
            update_payload["retry_count"] = retries
            # The run's own last words lead (list_tasks shows the first 80 characters): task-231's said only "exit
            # code: 1" while the reason (a JSONDecodeError) sat in output_summary, so the chat made one up (2026-10-26)
            reason = next((ln.strip() for ln in reversed(output_text.splitlines()) if ln.strip()), "")[:200]
            update_payload["last_error"] = (f"{reason} (engine '{engine_used}', exit code {exit_code})" if reason
                                            else f"Engine '{engine_used}' failed with exit code: {exit_code}")

            if exit_code == QUOTA_EXIT_CODE:
                # Retrying now just hits the same wall; the quota resets hours later
                quota = quota_message(output_text) or "Antigravity's quota is used up."
                self.logger.warning(f"Task [{task_id}] stopped: {quota}")
                update_payload["phase"] = "failed"
                update_payload["last_error"] = quota
            elif engine_used == "social_publisher":
                # A failed TikTok post isn't retried by itself: if TikTok took it after all, a retry posts it
                # twice. The owner checks TikTok, then posts again from the card.
                self.logger.warning(f"Task [{task_id}] couldn't post to TikTok; not retrying automatically.")
                update_payload["phase"] = "failed"
            elif engine_used == "computer":
                # A computer task isn't retried by itself either (2026-10-10): the screen still holds the first try's
                # windows, which the new run doesn't own, so it acts on leftovers (it pressed Ctrl+W on the first
                # try's Notepad tab). The owner reads the card and runs it again if they want.
                self.logger.warning(f"Task [{task_id}] (computer) didn't finish; not retrying automatically.")
                update_payload["phase"] = "failed"
            elif retries >= max_retries:
                self.logger.error(f"Task [{task_id}] failed and exceeded maximum retries ({max_retries}). Marked failed.")
                update_payload["phase"] = "failed"

                self.db.add_blocker(
                    task_id=task_id,
                    message=f"Task '{task_title}' failed with exit code {exit_code}. See worker.log."
                )
            else:
                self.logger.warning(
                    f"Task [{task_id}] failed (exit code {exit_code}). Will retry ({retries}/{max_retries}) on future pulse."
                )
                update_payload["phase"] = "backlog"

        self.db.update_task(task_id, update_payload)
        self._remember_task_result(task, exit_code, engine_used, summary)
        self._learn_from_task(task, exit_code, current, summary)
        self.db.end_busy_status()
        phase = self.db.recalculate_overall_phase()
        self.logger.info(f"Task cycle complete for [{task_id}]. Current project phase: {phase}.")

    def _sleep_until_woken(self) -> bool:
        """
        Sleeps up to interval_seconds, but returns at once when events.DAEMON_WAKE is set (a task was queued, or
        the running task finished), so work starts and results land in about a second instead of up to 30 s
        (IDEAS #26). The timer stays as the fallback for work written by other processes. Checks self.running
        every second so shutdown stays quick. Returns True when woken early.
        """
        sleep_start = time.time()
        while self.running:
            left = self.interval_seconds - (time.time() - sleep_start)
            if left <= 0:
                return False
            if events.DAEMON_WAKE.wait(min(1.0, left)):
                events.DAEMON_WAKE.clear()
                return True
        return False

    def run(self, run_once: bool = False) -> None:
        """Main daemon loop."""
        if os.environ.get("SUPERVISOR_ACTIVE") != "1":
            self.logger.info("=" * 60)
            self.logger.info("Always-On Autonomous AI Background Daemon Starting")
            self.logger.info(f"Workspace Directory : {self.workspace_dir}")
            self.logger.info(f"Database File       : {self.db_path}")
            self.logger.info(f"Log File            : {self.log_path}")
            self.logger.info(f"Heartbeat Interval  : {self.interval_seconds}s")
            self.logger.info(f"Antigravity CLI     : {self.dispatcher.antigravity.agy_path}")
            self.logger.info(f"Cloud providers     : {', '.join(p for p in self.dispatcher.llm.PROVIDERS if self.dispatcher.llm.keys.get(p)) or 'none configured'}")
            ollama_alive, ollama_models = self.dispatcher.ollama.client.is_alive()
            self.logger.info(f"Local Ollama Status : {'ONLINE' if ollama_alive else 'OFFLINE'} (Models: {ollama_models})")
            self.logger.info("=" * 60)

        # Crash recovery: tasks left in_progress by a crash go back to the backlog
        reconciled = self.db.reconcile_orphaned_tasks()
        if reconciled:
            self.logger.info(f"Startup crash recovery: Reconciled {len(reconciled)} orphaned task(s): {reconciled}")

        self.db.end_busy_status()
        if self.db_path.resolve() == DB_FILE.resolve():
            llm_usage.NOTIFY = notify_owner          # daily-quota warnings (IDEAS #12); the live agent only
            memory_module.NOTIFY = notify_owner      # a memory delete the second opinion held (IDEAS #11)

        while self.running:
            try:
                self.heartbeat_cycle()
            except Exception as e:
                self.logger.exception(f"Unhandled exception during heartbeat cycle: {e}")

            if run_once:
                # If running once and task is active, wait for it to finish
                if self.active_future and not self.active_future.done():
                    self.logger.info("Waiting for active background task to complete (--once mode)...")
                    try:
                        self.active_future.result(timeout=self.active_task.get("timeout_seconds", 300))
                        self.heartbeat_cycle()
                    except Exception as ex:
                        self.logger.error(f"Task wait error in --once mode: {ex}")
                self.logger.info("Single run requested (--once). Exiting daemon.")
                break

            self._sleep_until_woken()

        # Graceful shutdown
        self.logger.info("Shutting down worker threads...")
        self.executor.shutdown(wait=False)
        self.away_shift.shutdown(wait=False)
        self.db.set_state("daemon_status", "stopped")
        self.db.checkpoint_wal("TRUNCATE")
        self.logger.info("Autonomous AI Background Daemon has exited cleanly.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Always-On Autonomous AI Background Worker Daemon",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("DAEMON_INTERVAL", 30)),
        help="Heartbeat loop interval in seconds"
    )
    parser.add_argument(
        "--db-file",
        type=str,
        default="data/state.db",
        help="Path to SQLite database file relative to workspace"
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="logs/worker.log",
        help="Path to log file relative to workspace"
    )
    parser.add_argument(
        "--agy-path",
        type=str,
        default=None,
        help="Explicit path to Antigravity CLI binary (agy / agy.exe)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute a single heartbeat cycle and exit"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    workspace = Path(__file__).resolve().parent.parent

    daemon = AutonomousDaemon(
        workspace_dir=workspace,
        interval_seconds=args.interval,
        db_file=args.db_file,
        log_file=args.log_file,
        agy_path=args.agy_path
    )
    daemon.run(run_once=args.once)
