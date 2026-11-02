"""Media: the finished videos (TiktokVideos) and generated images (ImageGen) for the DeskAI gallery."""

import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from core import actions, task_thumbs
from interfaces.hud import auth, common, state
from interfaces.hud.models import MediaLinkRequest

router = APIRouter()


@router.get("/api/videos")
async def list_videos(response: Response):
    """Lists generated MP4, MOV, MKV, and WEBM videos for DeskAI and Web HUD."""
    videos = []
    if state.VIDEOS_DIR.exists():
        for p in state.VIDEOS_DIR.iterdir():
            if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
                try:
                    stat = p.stat()
                    videos.append({
                        "filename": p.name,
                        "size_mb": round(stat.st_size / (1024 * 1024), 2),
                        "created_at": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%b %d %H:%M"),
                        "mtime": stat.st_mtime,
                        "url": f"/videos/{p.name}",
                        "thumbnail_url": f"/api/videos/{p.name}/thumbnail"
                    })
                except Exception:
                    pass
    videos.sort(key=lambda x: x["mtime"], reverse=True)
    return {"videos": videos}


@router.get("/api/videos/{filename}/thumbnail")
def get_video_thumbnail(filename: str, response: Response):
    """Generates or serves a cached JPEG thumbnail for a video."""

    videos_dir = state.VIDEOS_DIR.resolve()
    clean_name = Path(filename).name
    candidate = (videos_dir / clean_name).resolve()
    if not candidate.is_relative_to(videos_dir) or not candidate.is_file():
        candidate = actions.resolve_video(state.VIDEOS_DIR, filename)
    if not candidate or not candidate.is_file() or candidate.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
        raise HTTPException(status_code=404, detail="Video file not found")
    video_path = candidate

    thumbs_dir = state.VIDEOS_DIR / ".thumbnails"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumbs_dir / f"{video_path.name}.jpg"

    # Re-generate if thumbnail is missing, empty, or older than the video file
    if not thumb_path.is_file() or thumb_path.stat().st_mtime < video_path.stat().st_mtime or thumb_path.stat().st_size == 0:
        generated = task_thumbs.extract_frame(video_path, thumb_path, width=360, quality=3)
        if not generated:
            raise HTTPException(status_code=500, detail="Failed to generate video thumbnail")

    return FileResponse(
        str(thumb_path),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"}
    )


@router.get("/api/images")
async def list_images(response: Response):
    """Lists generated JPG, PNG, and WEBP images for DeskAI and Web HUD gallery."""
    images = []
    if state.IMAGES_DIR.exists():
        for p in state.IMAGES_DIR.iterdir():
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and p.name != ".gitkeep":
                try:
                    stat = p.stat()
                    images.append({
                        "filename": p.name,
                        "size_mb": round(stat.st_size / (1024 * 1024), 2),
                        "created_at": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%b %d %H:%M"),
                        "mtime": stat.st_mtime,
                        "url": f"/images/{p.name}"
                    })
                except Exception:
                    pass
    images.sort(key=lambda x: x["mtime"], reverse=True)
    return {"images": images}


@router.delete("/api/images/{filename}")
async def delete_image(filename: str, response: Response):
    """Deletes an image from the gallery."""
    safe_path = (state.IMAGES_DIR / filename).resolve()
    if not safe_path.is_relative_to(state.IMAGES_DIR.resolve()) or not safe_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")
    try:
        safe_path.unlink()
        return {"status": "success", "message": f"Deleted {filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/media/link")
def media_link(req: MediaLinkRequest):
    """A short-lived signed link to one media file, for the cases DeskAI can't send its token in a header (an
    external video player, a link shared to another app). Needs the token like every other route; the link
    itself carries no token, only an expiry and an HMAC signature (auth.sign_media_link)."""
    if auth.media_link_path(req.url) is None:
        raise HTTPException(status_code=400, detail="Not a media file path")
    signed = auth.sign_media_link(req.url, req.ttl_seconds)
    if signed is None:      # no HUD_AUTH_TOKEN: only this machine can use the API, so the plain path is the link
        return {"url": auth.media_link_path(req.url), "expires_at": None, "signed": False}
    url, expires = signed
    return {"url": url, "expires_at": datetime.datetime.fromtimestamp(expires, datetime.timezone.utc).isoformat(),
            "signed": True}


@router.post("/api/videos/{filename}/publish")
def publish_video(filename: str, task_source: Optional[str] = common.TaskSourceHeader):
    """Posts an already-rendered video to TikTok without re-rendering it."""
    video_path = actions.resolve_video(state.VIDEOS_DIR, filename) if filename.lower().endswith(".mp4") else None
    if video_path is None:
        raise HTTPException(status_code=404, detail="Video file not found")
    task_id = actions.queue_tiktok_post(state.db, video_path,
                                        source=common.clean_source(task_source, default="DeskAI Video Hub"))
    return {"status": "success", "task_id": task_id}
