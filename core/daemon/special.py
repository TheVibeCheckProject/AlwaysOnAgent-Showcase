"""
EngineDispatcher special engines: weekly report, pins, computer tasks, videos.
(daemon.py §3)

Moved into the core.daemon package on 2026-11-04.
"""
import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.llm import CloudLLM
    from core.memory import MemoryManager


ACTIVITY_LOG_KEEP = 150      # plain-language lines kept on a computer task (metadata.activity_log)


class _SpecialEnginesMixin:
    # Host state, provided by _DispatcherMixin.__init__ / _SetupMixin on EngineDispatcher
    # (EngineDispatcher(_DispatcherMixin, _SpecialEnginesMixin)); annotation-only, no runtime value.
    # Quoted: the names live under TYPE_CHECKING only, so an unquoted annotation would break import.
    db: "DatabaseManager | None"
    logger: logging.Logger
    llm: "CloudLLM"
    memory: "MemoryManager"
    def _execute_weekly_report(self, task: dict[str, Any]) -> tuple[int, str, str, bool]:
        """The weekly numbers: VibeStats, TiktokVideos and SocialPostEngine, put together (core/weekly_report.py)."""
        try:
            from core import weekly_report
        except ImportError:
            import weekly_report  # type: ignore[no-redef]
        task_id = task.get("id")

        def progress(p: dict[str, Any]) -> None:
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
                import growth_review  # type: ignore[no-redef]
            progress({"stage": "report", "label": "Writing the review", "percent": 90, "detail": ""})
            review = growth_review.run(self.db, report, self.llm, proactive=getattr(self, "proactive", None),
                                       name=self.memory.subject() if getattr(self, "memory", None) else "the owner")
            if review:
                text = review["text"]
                meta = task.get("metadata") or {}
                if self.db and task_id:
                    self.db.update_task(task_id, {"metadata": {**meta, "announced": "growth_review"}})
        except Exception as e:
            self.logger.warning(f"[report] the review couldn't be made; sending the plain report: {e}")
        return code, text, "weekly_report", False

    def _execute_social_posts(self, task: dict[str, Any]) -> tuple[int, str, str, bool]:
        """Pins go out through SocialPostEngine, a separate program; its progress goes to the task card."""
        try:
            from core import social_posts
        except ImportError:
            import social_posts  # type: ignore[no-redef]
        task_id = task.get("id")
        count = (task.get("metadata") or {}).get("count", 1)
        current = self.db.get_task(task_id) if self.db and task_id else None
        if current and current.get("phase") == "failed":
            return 1, "Cancelled before it started: no pins were posted.", "social_posts", False

        def progress(p: dict[str, Any]) -> None:
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

    def _computer_callbacks(self, task_id: str | None, log: list[dict[str, Any]]):
        """Builds the note/progress/running/needs_you callbacks for a computer task, sharing one activity log."""
        try:
            from core import computer_use
        except ImportError:
            import computer_use  # type: ignore[no-redef]

        def note(text: str, level: str = "info", step: Any = None, kind: str = "") -> None:
            # kind: what kind of step (open, type, click, save, plan), so DeskAI colors the log by it
            log.append({"t": datetime.datetime.now().strftime("%H:%M:%S"), "step": step, "text": text[:240],
                        "level": level, "kind": kind or ""})
            del log[:-ACTIVITY_LOG_KEEP]

        def progress(p: dict[str, Any]) -> None:
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

        def needs_you(req: dict[str, Any]) -> None:
            """The tool waits for the owner: a loud alert with the screenshot, reminders until answered."""
            if self.db and task_id:
                self.db.set_task_progress(task_id, {"message": f"Needs you: {req.get('question', '')[:160]}",
                                                    "request_id": req.get("id")})
            computer_use.alert_owner(req, task_id or "")

        return note, progress, running, needs_you

    def _execute_computer(self, task: dict[str, Any]) -> tuple[int, str, str, bool]:
        """
        A computer task: AgentComputerUse (a separate program) does the goal on this PC's screen. It runs from the
        agent's own process, which is in the owner's desktop session, so the tool can see the screen and use the
        mouse and keyboard. Its steps go to the task card; it stops before anything that submits or posts.
        """
        try:
            from core import computer_use
        except ImportError:
            import computer_use  # type: ignore[no-redef]
        meta = task.get("metadata", {})
        task_id = task.get("id")
        goal = meta.get("goal") or task.get("prompt") or ""
        # What it did, in plain sentences ("Opening Notepad", "Pressing Enter"), kept on the task for its card's
        # details (2026-10-10, the owner: "a log ... on the task card, so I can see the logs and any errors").
        # The tool writes the sentences (AgentComputerUse activity.say); typed secrets are already hidden in them.
        log: list[dict[str, Any]] = []
        note, progress, running, needs_you = self._computer_callbacks(task_id, log)

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

    def _save_activity_log(self, task_id: str | None, meta: dict[str, Any], log: list[dict[str, Any]]) -> None:
        """A computer task's plain-language log on the task (metadata.activity_log), for its card's details."""
        meta["activity_log"] = log
        if self.db and task_id:
            self.db.update_task(task_id, {"metadata": meta})

    def _thumb_for(self, task: dict[str, Any], video_path: Path, meta: dict[str, Any]) -> bool:
        """Saves the task's small thumbnail (core/task_thumbs.py), which outlives the video's pruning, and notes
        it in `meta`. Cosmetic: a failure never fails the task. True when `meta` changed."""
        try:
            try:
                from core import task_thumbs
            except ImportError:
                import task_thumbs  # type: ignore[no-redef]
            made = task_thumbs.make(task, video_path)
            if made and meta.get("thumb") != made.name:
                meta["thumb"] = made.name
                return True
        except Exception as e:
            self.logger.debug(f"[media] Task thumbnail skipped: {e}")
        return False

    def _media_callbacks(self, task_id: str | None):
        """Builds the cancelled/progress/running callbacks for a media pipeline task."""
        def cancelled() -> bool:
            current = self.db.get_task(task_id) if self.db and task_id else None
            return current is not None and current.get("phase") == "failed"

        def progress(p: dict[str, Any]) -> None:
            if self.db and task_id:
                self.db.set_task_progress(task_id, p)

        def running(pid: int) -> None:
            if self.db and task_id:
                self.db.set_task_pid(task_id, pid)       # a cancel ends this process tree

        return cancelled, progress, running

    def _render_video(self, task: dict[str, Any], meta: dict[str, Any], task_id: str | None,
                      progress, running) -> Path:
        """Renders a fresh video with TiktokVideos, records its AI usage, and saves its thumbnail."""
        try:
            from core import tiktok_videos
            from core.llm_usage import UsageLog, usage_purpose
        except ImportError:
            import tiktok_videos  # type: ignore[no-redef]
            from llm_usage import UsageLog, usage_purpose  # type: ignore[no-redef]
        args = ["render"]
        if meta.get("quote"):
            args += ["--quote", str(meta["quote"])]
        if meta.get("scene"):                      # 'chest' / 'bottle' / 'window' / 'card'; none: rotates
            args += ["--scene", str(meta["scene"])]
        self.logger.info(f"[media] TiktokVideos: rendering (quote: {meta.get('quote') or 'a fresh one'})")
        result = tiktok_videos.run(args, on_progress=progress, on_start=running, timeout=2200)   # see queue_video
        if self.db and result.get("ai_usage"):
            log = UsageLog(self.db)
            with usage_purpose("affirmation"):
                for call in result["ai_usage"]:
                    log.record(call.get("model", "?"), call, provider="gemini")
        video_path = Path(result["video"])
        meta.update(video_path=str(video_path), quote=result.get("quote"), title=result.get("title"))
        self._thumb_for(task, video_path, meta)
        if self.db and task_id:
            self.db.update_task(task_id, {"metadata": meta})
        return video_path

    def _publish_or_report(self, meta: dict[str, Any], video_path: Path, action: str,
                           progress, running, cancelled) -> tuple[int, str, str, bool]:
        """Posts the video to TikTok when asked, otherwise reports the render."""
        try:
            from core import tiktok_videos
        except ImportError:
            import tiktok_videos  # type: ignore[no-redef]
        if action in ("tiktok", "publish_tiktok"):
            if cancelled():
                return 1, f"Cancelled before posting: {video_path.name} was not posted to TikTok.", "social_publisher", False
            self.logger.info(f"[media] TiktokVideos: posting {video_path.name} to TikTok")
            # TiktokVideos waits up to 7 min for TikTok to finish taking the file before it presses Post
            tiktok_videos.run(["post", "--video", video_path.name], on_progress=progress, on_start=running,
                              timeout=900)
            return 0, f"Successfully posted video to TikTok: {video_path.name}", "social_publisher", False

        return 0, f"Rendered {video_path.name}: \"{meta.get('quote') or ''}\"", "media_pipeline", False

    def _execute_media_pipeline(self, task: dict[str, Any]) -> tuple[int, str, str, bool]:
        """
        Videos are made by TiktokVideos, a separate program: it writes the quote (unless the owner gave one),
        renders, and posts to TikTok. This runs it, relays its progress to the task card and records its AI
        usage; the owner watches the finished video in DeskAI. A cancel ends the program (its process tree).
        """
        try:
            from core import tiktok_videos
        except ImportError:
            import tiktok_videos  # type: ignore[no-redef]
        meta = task.get("metadata", {})
        action = meta.get("action", "video")
        task_id = task.get("id")
        cancelled, progress, running = self._media_callbacks(task_id)

        existing = meta.get("video_path")
        video_path = Path(existing) if existing and Path(existing).exists() else None
        if cancelled():                                   # cancelled while it waited: don't start at all
            return 1, "Cancelled before it started: nothing was rendered, sent or posted.", "media_pipeline", False
        try:
            if video_path is None:
                video_path = self._render_video(task, meta, task_id, progress, running)
            else:
                self.logger.info(f"[media] Using the existing video {video_path.name}")
                if self._thumb_for(task, video_path, meta) and self.db and task_id:
                    self.db.update_task(task_id, {"metadata": meta})

            return self._publish_or_report(meta, video_path, action, progress, running, cancelled)
        except tiktok_videos.TiktokVideosError as e:
            if cancelled():
                return 1, "Cancelled: the video was stopped and nothing was posted.", "media_pipeline", False
            engine = "social_publisher" if video_path is not None else "media_pipeline"
            what = (f"Video ready ({video_path.name}) but TikTok auto-upload failed" if video_path is not None
                    else "The video couldn't be made")
            self.logger.warning(f"[media] {what}: {e}")
            return 1, f"{what}: {e}", engine, False
        except Exception as e:
            self.logger.exception("[media] Error in media pipeline")
            return 1, f"Media pipeline execution failed: {e}", "media_pipeline", False
        finally:
            if self.db and task_id:
                self.db.set_task_pid(task_id, None)
