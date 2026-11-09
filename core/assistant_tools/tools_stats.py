"""
Stats tools: Pinterest pins and drafts, the weekly report, its own numbers.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tools_stats.py).

Split out of core/assistant_tools.py on 2026-11-03; AssistantTools inherits
these methods via _StatsToolsMixin, so they are still bound methods on the tools object.
"""

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core import actions
except ImportError:
    import actions  # type: ignore[no-redef]


class _StatsToolsMixin:
    if TYPE_CHECKING:
        # Host-class attributes, set by AssistantTools.__init__.
        db: DatabaseManager
        source: str
        started: list[str]

    # Interface this mixin requires from the host class: _can_start and
    # _propose_post live on AssistantTools itself, which comes before this
    # mixin in AssistantTools' MRO, so these stubs never shadow them at runtime.
    def _can_start(self) -> str | None:
        raise NotImplementedError

    def _propose_post(self, kind: str, instruction: str, add: dict[str, Any]) -> str | None:
        raise NotImplementedError

    def post_pins(self, count: int = 1) -> str:
        n = max(1, min(int(count or 1), 5))
        if (err := self._propose_post("pins", f"Post {n} pin{'s' if n != 1 else ''} to Pinterest", {"count": n})):
            return err
        return (f"The user sees a card to post the next {n} pin{'s' if n != 1 else ''} to Pinterest; they post when "
                "they tap Run. Nothing is posted yet: say so in a sentence.")

    def make_pins(self, count: int = 8, new: bool = True) -> str:
        try:
            from core import social_posts
            from core.config import IMAGES_DIR
        except ImportError:
            import social_posts  # type: ignore[no-redef]
            from config import IMAGES_DIR  # type: ignore[no-redef]
        try:
            r = social_posts.make_pins(count, IMAGES_DIR) if new else social_posts.pin_drafts(IMAGES_DIR)
        except social_posts.SocialPostError as e:
            return f"Error: the pin maker failed ({e}). Nothing was made."
        if not r.get("drafts"):
            return ("No new pins could be made: every message on the website's guides already has a pin (or was "
                    "turned down). Say so; new guides on the site give new pins.")
        dead = f" (left out pages that aren't live: {', '.join(r['dead_links'])})" if r.get("dead_links") else ""
        return ("Show the user this, exactly as written (the image is the preview):\n\n"
                + social_posts.drafts_message(r) + f"\n\n[for you: {r.get('made', 0)} made now{dead}; nothing is "
                "posted or queued until they approve.]")

    def approve_pins(self, pins: str) -> str:
        try:
            from core import social_posts
        except ImportError:
            import social_posts  # type: ignore[no-redef]
        try:
            r = social_posts.approve_pins(str(pins))
        except social_posts.SocialPostError as e:
            return f"Error: {e}. Nothing was approved."
        return (f"Approved {len(r.get('approved') or [])} pin(s): they're in the posting queue ({r.get('pins_left')} "
                f"pins waiting; the schedule posts them). {r.get('drafts_left', 0)} draft(s) still waiting.")

    def reject_pins(self, pins: str) -> str:
        try:
            from core import social_posts
        except ImportError:
            import social_posts  # type: ignore[no-redef]
        try:
            r = social_posts.reject_pins(str(pins))
        except social_posts.SocialPostError as e:
            return f"Error: {e}. Nothing was rejected."
        return (f"Dropped {len(r.get('rejected') or [])} draft(s); their messages won't be drafted again. "
                f"{r.get('drafts_left', 0)} draft(s) still waiting.")

    def weekly_report(self, days: int = 7, new: bool = False) -> str:
        n = max(1, min(int(days or 7), 31))
        # The latest report's own text when it's recent: 2026-10-05 the chat started a new report for "what did my
        # numbers look like", got only "started", and the backup model made up zeros
        if not new:
            import datetime
            try:
                from core import weekly_report as weekly_report_mod
            except ImportError:
                import weekly_report as weekly_report_mod  # type: ignore[no-redef]
            try:
                kept = json.loads(self.db.get_state(weekly_report_mod.STATE_REPORTS, "[]") or "[]")
                last = kept[-1] if kept else None
                if last and last.get("text"):
                    made = datetime.datetime.fromisoformat(last["at"])
                    age = datetime.datetime.now().astimezone() - made
                    if age < datetime.timedelta(hours=24):
                        return (f"The latest report, made {made:%a %b %d at %I:%M %p} (quote these numbers exactly; "
                                f"say when it was made):\n{last['text']}")
            except (ValueError, KeyError, TypeError):
                pass
        if (err := self._can_start()):
            return err
        task_id = actions.queue_report(self.db, n, source=self.source)
        self.started.append(task_id)
        return (f"Started {task_id}: the numbers for the last {n} days (about a minute). The user reads the report "
                "in the live card; don't quote numbers until it has finished.")

    def my_numbers(self) -> str:
        """The agent's own week in chat (core/self_check.py), read-only."""
        try:
            from core import self_check
        except ImportError:
            import self_check  # type: ignore[no-redef]
        return self_check.my_numbers(self.db)

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['_StatsToolsMixin']
