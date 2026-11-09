"""
AutonomousDaemon before/after a task: briefing, learning recipes, recording.
(daemon.py §9)

Moved into the core.daemon package on 2026-11-04.
"""
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.daemon import EngineDispatcher
    from core.database import DatabaseManager
    from core.memory import MemoryManager

try:
    from core import procedures
    from core.config import redact_secrets
    from core.database import NEEDS_INPUT_EXIT_CODE, get_iso_now
    from core.task_engines import QUOTA_EXIT_CODE, normalize_engine, quota_message
except ImportError:
    import procedures  # type: ignore[no-redef]
    from config import redact_secrets  # type: ignore[no-redef]
    from database import NEEDS_INPUT_EXIT_CODE, get_iso_now  # type: ignore[no-redef]
    from task_engines import QUOTA_EXIT_CODE, normalize_engine, quota_message  # type: ignore[no-redef]


REPORT_SUMMARY_MAX = 12000   # the weekly report's task card text (a real one is ~2,500); other tasks keep 1,500


class _CompletionMixin:
    # Host state provided by _SetupMixin on AutonomousDaemon (core/daemon/setup.py).
    # Annotation-only: these create no runtime attributes, so they cannot shadow
    # any sibling mixin's method in the MRO.
    db: "DatabaseManager"
    dispatcher: "EngineDispatcher"
    logger: logging.Logger
    memory: "MemoryManager"
    _task_recipes: dict[str, Any]

    def _with_memory_briefing(self, task: dict[str, Any]) -> dict[str, Any]:
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
        try:
            briefing = self.memory.task_context(task.get("prompt", ""))
        except Exception as e:
            self.logger.warning(f"Could not build memory briefing for task [{task.get('id')}]: {e}")
            briefing = ""
        if engine == "agentwork":
            # AgentWork gets the owner's instruction first (the project has its own rules), then what earlier jobs
            # taught and, since 2026-11-03, what he's said that relates to it (it's his assistant, his projects)
            extra = "\n\n".join(p for p in (lessons, briefing) if p)
            return {**task, "prompt": f"{task.get('prompt', '')}\n\n{extra}"} if extra else task
        preamble = "\n\n".join(p for p in (briefing, lessons) if p)
        if not preamble:
            return task
        return {**task, "prompt": f"{preamble}\n\n[Task]\n{task.get('prompt', '')}"}

    def _learn_from_task(self, task: dict[str, Any], exit_code: int, earlier: dict[str, Any] | None,
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

    def _remember_task_result(self, task: dict[str, Any], exit_code: int, engine_used: str, summary: str) -> None:
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

    def _truncate_output_summary(self, output_text: str, engine_used: str) -> str:
        """Truncates the output for the task card, keeping head and tail around a marker."""
        # The weekly report is the result itself, not a log: kept whole (2026-11-03: its middle, the TikTok details
        # and the self-check, was cut to "...[TRUNCATED]..." on the task card). Other tasks keep the 1500 cap.
        cap = REPORT_SUMMARY_MAX if engine_used == "weekly_report" else 1500
        if len(output_text) > cap:
            return output_text[:cap // 3] + "\n...[TRUNCATED]...\n" + output_text[-(cap - cap // 3):]
        return output_text

    def _record_moved_task(self, task_id: str, engine_used: str, fallback_triggered: bool, summary: str) -> None:
        """Records the output of a task that was cancelled (or otherwise moved) while running, without
        overwriting its phase or requeueing it: the owner's decision stands."""
        self.db.update_task(task_id, {
            "engine_used": engine_used,
            "fallback_triggered": fallback_triggered,
            "output_summary": summary
        })
        self._task_recipes.pop(task_id, None)        # cancelled: no verdict on its recipes
        self.db.end_busy_status()
        self.db.recalculate_overall_phase()

    def _apply_success_outcome(self, task: dict[str, Any], task_id: str, update_payload: dict[str, Any],
                               summary: str, needs_input: bool, engine_used: str) -> None:
        """Fills in the update payload for a task that succeeded or needs the owner's input."""
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

    def _apply_failure_outcome(self, task: dict[str, Any], task_id: str, task_title: str,
                               update_payload: dict[str, Any], output_text: str, exit_code: int,
                               engine_used: str) -> None:
        """Fills in the update payload for a failed task: quota, no-retry engines, max retries, or backlog."""
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

    def _finalize_task_completion(
        self,
        task: dict[str, Any],
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
        summary = self._truncate_output_summary(output_text, engine_used)

        # If the task was cancelled (or otherwise moved) while running, keep the user's
        # decision: record the output but do not overwrite phase / requeue it.
        current = self.db.get_task(task_id)
        if current and current.get("phase") != "in_progress":
            self.logger.info(
                f"Task [{task_id}] finished (exit {exit_code}) but is now '{current.get('phase')}' "
                "(cancelled or modified externally). Preserving its current phase."
            )
            self._record_moved_task(task_id, engine_used, fallback_triggered, summary)
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
            self._apply_success_outcome(task, task_id, update_payload, summary, needs_input, engine_used)
        else:
            self._apply_failure_outcome(task, task_id, task_title, update_payload, output_text, exit_code, engine_used)

        self.db.update_task(task_id, update_payload)
        self._remember_task_result(task, exit_code, engine_used, summary)
        self._learn_from_task(task, exit_code, current, summary)
        self.db.end_busy_status()
        phase = self.db.recalculate_overall_phase()
        self.logger.info(f"Task cycle complete for [{task_id}]. Current project phase: {phase}.")
