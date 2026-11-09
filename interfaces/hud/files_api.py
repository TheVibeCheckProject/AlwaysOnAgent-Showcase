"""The Mini's files for the chat: GET /api/files/m/{ref}/{name} serves one image, video, audio file or PDF that the
chat's mini_files tool showed (core/mini_files.py). The ref is checked again here: the same roots and the same
off-limits rules (secrets, browser data, the agent's own data). Read only."""
import hashlib
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core import mini_files, task_thumbs
from interfaces.hud import state

router = APIRouter()


def _thumb_path(video: Path) -> Path:
    """Where a video's thumbnail is cached. A video in the TikTok folder shares the videos hub's cache
    (media.get_video_thumbnail), so the chat shows the very same picture; any other gets its own, by path."""
    try:
        if video.parent.resolve() == state.VIDEOS_DIR.resolve():
            return state.VIDEOS_DIR / ".thumbnails" / f"{video.name}.jpg"
    except OSError:
        pass
    key = hashlib.sha1(str(video).lower().encode("utf-8")).hexdigest()[:16]
    return state.VIDEOS_DIR.parent / ".file_thumbnails" / f"{key}.jpg"


@router.get("/api/files/m/{ref}/{name}/thumbnail")
def mini_file_thumbnail(ref: str, name: str):
    """A still from a video the chat showed (2026-10-27, Alex: "That video really really needs a thumbnail")."""
    try:
        path = mini_files.path_from_ref(ref)
    except mini_files.FileAccessError:
        raise HTTPException(status_code=404, detail="no such file")
    if not path.is_file() or mini_files.kind_of(path) != "video":
        raise HTTPException(status_code=404, detail="no such video")
    thumb = _thumb_path(path)
    stale = not thumb.is_file() or thumb.stat().st_size == 0 or thumb.stat().st_mtime < path.stat().st_mtime
    if stale and not task_thumbs.extract_frame(path, thumb, width=360, quality=3):
        raise HTTPException(status_code=404, detail="no thumbnail")
    return FileResponse(str(thumb), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/files/m/{ref}/{name}")
def mini_file(ref: str, name: str):
    try:
        path = mini_files.path_from_ref(ref)
    except mini_files.FileAccessError:
        raise HTTPException(status_code=404, detail="no such file")
    if not path.is_file() or mini_files.kind_of(path) not in ("image", "video", "audio", "document"):
        raise HTTPException(status_code=404, detail="no such file")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # FileResponse answers Range requests, so a player can seek in a long video
    return FileResponse(str(path), media_type=media_type, filename=path.name,
                        headers={"Cache-Control": "private, max-age=3600"})
