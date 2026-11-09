"""
Web tools: reading a page, AI-opportunity research, videos.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tools_web.py).

Split out of core/assistant_tools.py on 2026-11-03; AssistantTools inherits
these methods via _WebToolsMixin, so they are still bound methods on the tools object.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core import actions, web_tools

    from .tool_constants import _url_key
except ImportError:
    import actions  # type: ignore[no-redef]
    import web_tools  # type: ignore[no-redef]

    from core.assistant_tools.tool_constants import _url_key


class _WebToolsMixin:
    # Interface this mixin requires from the host class: these attributes are
    # set in AssistantTools._init_state and these methods are defined on
    # AssistantTools itself, which comes before every mixin in its MRO, so
    # these stubs never shadow the real ones at runtime. (Attribute
    # annotations create no runtime value at all.)
    db: "DatabaseManager"
    memory: Any
    source: str
    started: list[str]
    videos_dir: Path
    _known_urls: set
    _read_a_pc_file: bool

    def _can_start(self) -> str | None:
        raise NotImplementedError

    def _propose_post(self, kind: str, instruction: str, add: dict[str, Any]) -> str | None:
        raise NotImplementedError

    def fetch_webpage(self, url: str, max_chars: int = 3000) -> str:
        if self._read_a_pc_file and _url_key(url) not in {_url_key(u) for u in self._known_urls}:
            # After a file on the PC was read, a page or email could have the model send its text out in an address
            # (audit 0.2): only addresses the user gave or this turn's searches showed
            return ("Error: after reading a file on the PC, only pages from the user's message or this turn's search "
                    "results can be opened. Search for it first, or ask the user for the link.")
        return web_tools.fetch_webpage(url, max_chars)

    def research_ai_opportunities(self) -> str:
        """Runs the automated research worker on-demand, saving the digest to Canvas and memory."""
        try:
            from core import research_worker
        except ImportError:
            import research_worker  # type: ignore[no-redef]
        res = research_worker.run_research_cycle(
            self.db, memory=self.memory, llm=None, proactive=None, force=True,
            name=self.memory.subject() if self.memory else "Alex"
        )
        if not res or not res.get("count"):
            return "The AI research worker ran but found no new opportunities right now."
        doc = res.get("doc", {})
        count = res.get("count", 0)
        card_info = f" (Canvas #{doc['id']})" if doc.get("id") else ""
        if doc.get("id"):
            self.canvas_shown = doc
        return (f"Research digest compiled with {count} opportunities: \"{doc.get('title')}\"{card_info}. "
                "Saved to memory and Canvas.")

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
    def render_video(self, quote: str | None = None, post_to_tiktok: bool = False, scene: str | None = None) -> str:
        # post_to_tiktok is accepted but never acted on: a public post needs the user's tap (the finished card's
        # "🚀 Post to TikTok" chip), not words in a message
        if (err := self._can_start()):
            return err
        scene = actions.video_scene(scene)
        task_id = actions.queue_video(self.db, quote, post_to_tiktok=False, source=self.source, scene=scene)
        self.started.append(task_id)
        what = f"a {actions.VIDEO_SCENES[scene].lower()} video" if scene else "a video"
        return (f"Started {task_id}: rendering {what}{' with the quote you gave' if quote else ' with a fresh affirmation'}. "
                "It takes about 6 minutes (the rainy window about 15); the user sees a live card and gets a notification. When it's done, the card "
                "has a 🚀 Post to TikTok chip: posting is their tap.")

    def post_video_to_tiktok(self, video: str = "latest") -> str:
        path = actions.resolve_video(self.videos_dir, video)
        if path is None:
            return f"Error: no video called '{video}'. Use list_videos to see the file names."
        if (err := self._propose_post("tiktok", f"Post {path.name} to TikTok", {"video": path.name})):
            return err
        return (f"The user sees a card to post {path.name} to TikTok; it posts when they tap Run. Nothing is posted "
                "yet: say so in a sentence.")

    def list_videos(self, limit: int = 5) -> str:
        if not self.videos_dir.is_dir():
            return "No videos yet."
        videos = sorted((p for p in self.videos_dir.glob("*.mp4") if not p.stem.endswith("_tg")),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, min(int(limit or 5), 20))]
        if not videos:
            return "No videos yet."
        import datetime
        # The full path, so the chat can show one with mini_files (2026-10-27: asked to attach the latest, it had
        # only the name)
        return "\n".join(f"- {p.name}: {datetime.datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}, "
                         f"{p.stat().st_size / 1e6:.1f} MB ({p.resolve()})" for p in videos) + \
            "\nTo play one in the chat: mini_files show with its path."

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['_WebToolsMixin']
