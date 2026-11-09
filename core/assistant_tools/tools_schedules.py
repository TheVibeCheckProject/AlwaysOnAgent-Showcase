"""
Schedule tools: create / list / change schedules, proactive settings.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tools_schedules.py).

Split out of core/assistant_tools.py on 2026-11-03; AssistantTools inherits
these methods via _SchedulesToolsMixin, so they are still bound methods on the tools object.
"""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core import feeds, proactive, schedules

    from .tool_constants import ASKS_TO_REMOVE
    from .tool_routing import MAX_NEW_SCHEDULES
except ImportError:
    import feeds  # type: ignore[no-redef]
    import proactive  # type: ignore[no-redef]
    import schedules  # type: ignore[no-redef]

    from core.assistant_tools.tool_constants import ASKS_TO_REMOVE
    from core.assistant_tools.tool_routing import MAX_NEW_SCHEDULES


class _SchedulesToolsMixin:
    # Interface this mixin requires from the host class: these attributes are
    # set in AssistantTools._init_state and these methods are defined on
    # AssistantTools itself, which comes before every mixin in its MRO, so
    # these stubs never shadow the real ones at runtime. (Attribute
    # annotations create no runtime value at all.)
    db: "DatabaseManager"
    source: str
    question: dict[str, Any] | None
    schedules_created: list[int]

    def _asked_for_tiktok(self) -> bool:
        raise NotImplementedError

    def _asked_for_pinterest(self) -> bool:
        raise NotImplementedError

    def _asked_to(self, words: re.Pattern) -> str | None:
        raise NotImplementedError

    @staticmethod
    def _schedule_line(x: dict[str, Any]) -> str:
        what = {"video": "video" + (" + TikTok post" if x["post_to_tiktok"] else ""),
                "pins": f"{x.get('count') or 1} Pinterest pin(s)", "report": "weekly numbers report",
                "reminder": f"reminder \"{x['text']}\"", "task": f"task \"{(x['instruction'] or '')[:80]}\""}[x["kind"]]
        line = f"#{x['id']} {x['name']}: {what}, {x['description']} ({'on' if x['enabled'] else 'paused'})"
        if x["enabled"] and x["next_run_at"]:
            line += f"; next {x['next_run_at'][:16].replace('T', ' ')}"
        if x["last_run_at"]:
            line += f"; last {x['last_run_at'][:16].replace('T', ' ')}: {x['last_result']}"
        return line

    def create_schedule(self, kind: str, cron: str, name: str = "", post_to_tiktok: bool = False,
                        text: str | None = None, instruction: str | None = None,
                        project: str | None = None, quote: str | None = None,
                        count: int | None = None) -> str:
        if self.question:
            return "Error: you asked the user a question in this reply; wait for the answer first."
        if len(self.schedules_created) >= MAX_NEW_SCHEDULES:
            return f"Error: at most {MAX_NEW_SCHEDULES} new schedules per reply."
        if post_to_tiktok and not self._asked_for_tiktok():
            return ("Error: a schedule that posts to TikTok needs the user to ask for TikTok themselves. Make it "
                    "render only, or ask whether each video should be posted.")
        if (kind or "").strip().lower() == "pins" and not self._asked_for_pinterest():
            return "Error: a schedule that posts to Pinterest needs the user to ask for Pinterest or pins themselves."
        try:
            x = schedules.ScheduleStore(self.db).create(name, kind, cron, source=self.source,
                                                        post_to_tiktok=bool(post_to_tiktok), quote=quote, text=text,
                                                        instruction=instruction, project=project, count=count)
        except schedules.CronError as e:
            return f"Error: {e}"
        self.schedules_created.append(x["id"])
        import datetime
        runs = schedules.parse(x["cron"]).next_runs(datetime.datetime.now(), 3)
        return (f"Created {self._schedule_line(x)}. Next runs: " + ", ".join(f"{t:%a %b %d %H:%M}" for t in runs)
                + f" ({schedules.timezone_name()}). The user can see, pause or delete it in DeskAI's Schedules screen.")

    def list_schedules(self) -> str:
        items = schedules.ScheduleStore(self.db).list()
        if not items:
            return "No schedules yet."
        return f"Times are {schedules.timezone_name()}.\n" + "\n".join(f"- {self._schedule_line(x)}" for x in items)

    def change_schedule(self, schedule_id: int, enabled: bool | None = None, cron: str | None = None,
                        name: str | None = None, post_to_tiktok: bool | None = None, text: str | None = None,
                        delete: bool = False) -> str:
        store = schedules.ScheduleStore(self.db)
        x = store.get(int(schedule_id))
        if x is None:
            return f"Error: there's no schedule #{schedule_id} (see list_schedules)."
        if delete:
            refused = self._asked_to(ASKS_TO_REMOVE)
            if refused:
                return refused
            store.delete(x["id"])
            self.db.append_log("INFO", f"Schedule #{x['id']} '{x['name']}' deleted from {self.source}")
            return f"Deleted schedule #{x['id']} {x['name']}."
        if post_to_tiktok and not x["post_to_tiktok"] and not self._asked_for_tiktok():
            return "Error: turning on TikTok posting needs the user to ask for TikTok themselves."
        try:
            x = store.update(x["id"], name=name, cron=cron, enabled=enabled, post_to_tiktok=post_to_tiktok, text=text)
        except schedules.CronError as e:
            return f"Error: {e}"
        return f"Updated: {self._schedule_line(x)}."

    def proactive_settings(self, enabled: bool | None = None, brief_time: str | None = None,
                           quiet_hours: str | None = None, daily_cap: int | None = None,
                           questions_per_day: int | None = None, location: str | None = None,
                           open_loops: bool | None = None, curiosity: bool | None = None,
                           research: bool | None = None, patterns: bool | None = None,
                           add_interest: str | None = None, remove_interest: str | None = None,
                           add_exclusion: str | None = None, remove_exclusion: str | None = None,
                           follow_youtube: str | None = None, unfollow_youtube: str | None = None) -> str:
        p = proactive.Proactive(self.db)
        try:
            s, followed_note = self._proactive_apply_changes(
                p, enabled, brief_time, quiet_hours, daily_cap, questions_per_day, location,
                open_loops, curiosity, research, patterns, add_interest, remove_interest,
                add_exclusion, remove_exclusion, follow_youtube, unfollow_youtube)
        except ValueError as e:
            return f"Error: {e}."
        return followed_note + " ".join(self._proactive_summary_lines(p, s))

    def _proactive_apply_changes(self, p, enabled, brief_time, quiet_hours, daily_cap,
                                 questions_per_day, location, open_loops, curiosity, research,
                                 patterns, add_interest, remove_interest, add_exclusion,
                                 remove_exclusion, follow_youtube, unfollow_youtube):
        followed_note = ""
        if follow_youtube:
            # Looked up by name on YouTube, not followed as a guessed handle (2026-10-28: two of three didn't exist)
            found = feeds.follow_by_name(self.db, follow_youtube)
            followed_note = (f"Now following {found['title']} ({found['handle']}) on YouTube. " if found else
                             f"Couldn't find a YouTube channel for '{follow_youtube}': ask the user for its exact "
                             "name or a link. ")
        if unfollow_youtube:
            feeds.unfollow(self.db, unfollow_youtube)
        if add_interest:
            p.add_interest(add_interest)
        if remove_interest:
            p.remove_interest(remove_interest)
        if add_exclusion:
            p.add_exclusion(add_exclusion)
        if remove_exclusion:
            p.remove_exclusion(remove_exclusion)
        s = p.change(enabled=enabled, brief_time=brief_time, quiet_hours=quiet_hours, daily_cap=daily_cap,
                     questions_per_day=questions_per_day, location=location,
                     open_loops=open_loops, curiosity=curiosity, research=research, patterns=patterns)
        return s, followed_note

    def _proactive_summary_lines(self, p, s) -> list[str]:
        lines = [
            (f"Proactive messages are {'on' if s['enabled'] == '1' else 'off'}: brief at {s['brief_time']}, quiet "
            f"hours {s['quiet_hours']}, at most {s['daily_cap']} messages a day ({s['questions_per_day']} of them "
            f"questions), weather town: {s['location'] or 'not set'}.")
        ]
        sw = []
        for k in ("open_loops", "curiosity", "research", "patterns"):
            name = k.replace("_", " ")
            state = "on" if s.get(k) == "1" else "off"
            sw.append(f"{name} {state}")
        lines.append(f"Switches: {', '.join(sw)}.")
        ints = p.interests()
        if ints:
            lines.append(f"Interests: {', '.join(ints)}.")
        excs = p.exclusions()
        if excs:
            lines.append(f"Exclusions: {', '.join(excs)}.")
        followed = [f"{feeds.title_of(self.db, h)} ({h})" if feeds.title_of(self.db, h) != h else h
                    for h in feeds.channels(self.db)]
        lines.append(f"YouTube channels followed (new videos told): {', '.join(followed) or 'none'}.")
        return lines


    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['_SchedulesToolsMixin']
