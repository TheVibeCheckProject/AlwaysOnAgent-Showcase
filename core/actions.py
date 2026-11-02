"""
Things the agent can start, shared by every surface
===================================================
The HUD / DeskAI endpoints and the chat assistant's action tools queue work through these, so a video
rendered from a button and one asked for in chat are the same task. Each returns the new task's id; the
daemon picks it up on its next heartbeat (wake_daemon() takes it out of standby first).
"""

from pathlib import Path
from typing import Optional

try:
    from core.database import DatabaseManager
except ImportError:
    from database import DatabaseManager


def wake_daemon(db: DatabaseManager) -> bool:
    """New work wakes a daemon in standby. Returns True if it was asleep."""
    if db.get_state("daemon_status", "idle") in ("paused", "stopped", "sleep"):
        db.set_state("daemon_status", "idle")
        db.append_log("INFO", "New task: daemon woken from standby.")
        return True
    return False


# TiktokVideos' scenes (its --scene takes these short names). Without one it takes turns between the chest and
# the bottle (MEDIA_SCENE=rotate there, 2026-10-13). "card" is the first version: only when asked for by name.
VIDEO_SCENES = {"chest": "Treasure Chest", "bottle": "Message in a Bottle", "card": "Card Flip"}
_SCENE_NAMES = {"chest": "chest", "treasure": "chest", "treasure_chest": "chest", "bottle": "bottle",
                "message": "bottle", "message_bottle": "bottle", "card": "card", "card_flip": "card", "flip": "card"}


def video_scene(name: Optional[str]) -> Optional[str]:
    """The scene's short name for TiktokVideos' --scene ('chest', 'bottle', 'card'), or None if `name` isn't one."""
    return _SCENE_NAMES.get((name or "").strip().lower().replace(" ", "_").replace("-", "_"))


def queue_video(db: DatabaseManager, quote: Optional[str] = None, post_to_tiktok: bool = False,
                source: str = "", scene: Optional[str] = None) -> str:
    """Renders a new affirmation video (and posts it to TikTok when asked). quote=None picks a fresh one;
    scene ('chest', 'bottle', 'card', see video_scene) picks the scene, None lets TiktokVideos rotate."""
    quote = (quote or "").strip() or None
    scene = video_scene(scene)
    action = "tiktok" if post_to_tiktok else "video"
    kind = f"{VIDEO_SCENES[scene]} Video" if scene else "Video"
    title = f"{'Auto-Post to TikTok' if post_to_tiktok else 'Render ' + kind} ({quote[:30] if quote else 'Daily Affirmation'})"
    prompt = (f"Render {kind.lower()}{' and publish to TikTok (@Theexamplesite)' if post_to_tiktok else ''} "
              f"with quote: '{quote or 'random'}'")
    task_id = db.create_task(title=title, prompt=prompt, priority="high", engine="media_pipeline",
                             timeout_seconds=600,
                             metadata={"action": action, "quote": quote, **({"scene": scene} if scene else {}),
                                       **({"source": source} if source else {})})
    db.append_log("INFO", f"Queued video task [{task_id}] (action: {action}, quote: '{quote or 'random'}'"
                          f"{', scene: ' + scene if scene else ''}{', from ' + source if source else ''})")
    wake_daemon(db)
    return task_id


def resolve_video(videos_dir: Path, name: Optional[str] = None) -> Optional[Path]:
    """A finished video by file name, or the newest one for None / 'latest'. None if there's no such file."""
    videos = Path(videos_dir).resolve()
    if not videos.is_dir():
        return None
    name = (name or "").strip()
    if not name or name.lower() in ("latest", "last", "newest", "most recent"):
        mp4s = [p for p in videos.glob("*.mp4") if not p.stem.endswith("_tg")]
        return max(mp4s, key=lambda p: p.stat().st_mtime) if mp4s else None
    path = (videos / Path(name).name).resolve()
    if path.suffix.lower() != ".mp4":
        path = path.with_suffix(".mp4")
    return path if path.is_relative_to(videos) and path.is_file() else None


def queue_tiktok_post(db: DatabaseManager, video_path: Path, source: str = "") -> str:
    """Posts an already-rendered video to TikTok without re-rendering it."""
    task_id = db.create_task(
        title=f"Post Video to TikTok ({video_path.name})",
        prompt=f"Publish existing rendered video '{video_path.name}' directly to TikTok (@Theexamplesite)",
        engine="media_pipeline", priority="high",
        metadata={"action": "tiktok", "quote": None, "video_path": str(video_path),
                  **({"source": source} if source else {})},
    )
    db.append_log("INFO", f"Queued TikTok post of {video_path.name} [{task_id}]{' from ' + source if source else ''}")
    wake_daemon(db)
    return task_id


def queue_pins(db: DatabaseManager, count: int = 1, source: str = "") -> str:
    """Posts the next pins to Pinterest (SocialPostEngine picks them and never posts one twice)."""
    count = max(1, min(int(count or 1), 5))
    plural = "s" if count != 1 else ""
    task_id = db.create_task(
        title=f"Post {count} pin{plural} to Pinterest",
        prompt=f"Post the next {count} pin{plural} to Pinterest (SocialPostEngine)",
        engine="social_posts", priority="high", timeout_seconds=600,
        metadata={"action": "pins", "count": count, **({"source": source} if source else {})},
    )
    db.append_log("INFO", f"Queued Pinterest post of {count} pin(s) [{task_id}]{' from ' + source if source else ''}")
    wake_daemon(db)
    return task_id


TITLE_MAX = 200


def task_title(text: str, prefix: str = "", limit: int = TITLE_MAX) -> str:
    """A task's title from what the owner asked: whole, or cut at a word with "…" when it's very long. (2026-10-11,
    the owner: cards ended "...into my Downloads folde"; titles were cut at 50-70 characters, mid-word.)"""
    text = " ".join((text or "").split())
    room = limit - len(prefix)
    if len(text) > room:
        text = text[:room - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return prefix + text


def queue_computer(db: DatabaseManager, goal: str, source: str = "") -> str:
    """A computer task: AgentComputerUse does it on the Mini's screen (its own Edge and apps, real mouse and
    keyboard) and stops before anything is submitted, sent, paid or posted."""
    goal = " ".join((goal or "").split())[:500]
    task_id = db.create_task(
        title=task_title(goal, "Computer: "), prompt=goal, engine="computer", priority="high", timeout_seconds=3600,
        metadata={"action": "computer", "goal": goal, **({"source": source} if source else {})},
    )
    db.append_log("INFO", f"Queued a computer task [{task_id}]{' from ' + source if source else ''}: {goal[:80]}")
    wake_daemon(db)
    return task_id


def queue_report(db: DatabaseManager, days: int = 7, source: str = "") -> str:
    """The weekly numbers report (site visits, cards sent, subscribers, TikTok, Pinterest), as a task."""
    task_id = db.create_task(
        title="Weekly report", prompt=f"Put together the numbers for the last {days} days",
        engine="weekly_report", priority="high", timeout_seconds=900,
        metadata={"action": "report", "days": days, **({"source": source} if source else {})},
    )
    db.append_log("INFO", f"Queued the weekly report [{task_id}]{' from ' + source if source else ''}")
    wake_daemon(db)
    return task_id


def queue_instruction(db: DatabaseManager, prompt: str, project: Optional[str] = None,
                      source: str = "deskai") -> str:
    """Background work: the default engine, or an AgentWork job on one of the user's projects."""
    title = prompt[:50] + ("..." if len(prompt) > 50 else "")
    engine = db.get_state("default_engine", "auto")
    if project:
        title, engine = f"[{project}] {title}", "agentwork"
    task_id = db.create_task(
        title=title, prompt=prompt, engine=engine, priority="high", mode="accept-edits",
        # Coding a real change takes longer than the 5-minute default
        timeout_seconds=1800 if project else 300,
        metadata={"source": source, **({"project": project} if project else {})},
    )
    db.append_log("INFO", f"Queued task [{task_id}] from {source}: '{title}'")
    wake_daemon(db)
    return task_id
