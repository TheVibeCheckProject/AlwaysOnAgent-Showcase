"""Thumbnails and previews for documents.py §4
===========================================
images, a PDF's cover, a video's keyframe, a card for other files.

Split out of core/documents.py §4 on 2026-11-04; core/documents/__init__.py re-exports
the public names for backwards compatibility.
Moved into the core.documents package on 2026-11-04; the old
core/documents_<name>.py paths remain as thin shims.
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
# §4  THUMBNAILS AND PREVIEWS
#     The small pictures the app shows for each attachment, and the view the image notes are made from.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
import base64
import io
import logging
import subprocess
from pathlib import Path
from typing import Any

try:
    from core.config import resolve_ffmpeg_binary
except ImportError:
    from config import resolve_ffmpeg_binary  # type: ignore[no-redef]

logger = logging.getLogger("Documents")
AI_VIEW_MAX_DIM = 768
THUMB_SIZE = (256, 256)


def _make_thumb_b64(thumb_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(thumb_bytes).decode()


def _open_image(data: bytes):
    """Opens image bytes; None (with a warning) when they are not a readable image."""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        logger.warning(f"Could not open image for processing: {e}")
        return None
    return img


def _flatten_to_rgb(img):
    """EXIF-transposes and flattens to RGB (white behind any transparency)."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        base = img.convert("RGBA")
        bg = Image.new("RGBA", base.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, base).convert("RGB")
    return img.convert("RGB")


def _downscale_to_ai_view(img_rgb):
    """Downscales so the longest side is at most AI_VIEW_MAX_DIM."""
    from PIL import Image
    longest = max(img_rgb.size)
    if longest > AI_VIEW_MAX_DIM:
        scale = AI_VIEW_MAX_DIM / longest
        return img_rgb.resize(
            (max(1, int(img_rgb.width * scale)), max(1, int(img_rgb.height * scale))),
            resample=Image.Resampling.LANCZOS,
        )
    return img_rgb


def _save_thumb(img_rgb, folder: Path) -> bytes:
    """Makes the 256x256 thumbnail, saves thumb.jpg, returns the JPEG bytes."""
    from PIL import Image
    thumb_img = img_rgb.copy()
    thumb_img.thumbnail(THUMB_SIZE, resample=Image.Resampling.LANCZOS)
    thumb_buf = io.BytesIO()
    thumb_img.save(thumb_buf, format="JPEG", quality=80, optimize=True)
    thumb_bytes = thumb_buf.getvalue()
    (folder / "thumb.jpg").write_bytes(thumb_bytes)
    return thumb_bytes


def process_image_attachment(data: bytes, folder: Path) -> dict[str, Any]:
    """
    Saves:
    - ai_view.jpg: High-quality downscaled (max 768px Lanczos, quality 85, stripped of EXIF/metadata)
      for local or cloud AI vision token efficiency.
    - thumb.jpg: Compact 256x256 thumbnail for DeskAI and WebHUD UI display.
    Returns dict with has_thumb, thumb_b64, width, height, ai_view_w, ai_view_h.
    """
    img = _open_image(data)
    if img is None:
        return {"has_thumb": False}

    orig_w, orig_h = img.width, img.height
    img_rgb = _flatten_to_rgb(img)
    ai_img = _downscale_to_ai_view(img_rgb)
    ai_img.save(folder / "ai_view.jpg", format="JPEG", quality=85, optimize=True)
    thumb_bytes = _save_thumb(img_rgb, folder)

    return {
        "has_thumb": True,
        "thumb_b64": _make_thumb_b64(thumb_bytes),
        "width": orig_w,
        "height": orig_h,
        "ai_view_w": ai_img.width,
        "ai_view_h": ai_img.height,
    }


def extract_pdf_cover(data: bytes, folder: Path) -> dict[str, Any]:
    """Extracts first page of PDF as cover thumbnail and ai_view."""
    import pymupdf as fitz
    from PIL import Image
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        if len(doc) == 0:
            return {"has_thumb": False}
        page = doc[0]
        pix = page.get_pixmap(dpi=150, alpha=False, colorspace=fitz.csRGB)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        longest = max(img.size)
        if longest > AI_VIEW_MAX_DIM:
            scale = AI_VIEW_MAX_DIM / longest
            ai_img = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                resample=Image.Resampling.LANCZOS,
            )
        else:
            ai_img = img
        ai_img.save(folder / "ai_view.jpg", format="JPEG", quality=85, optimize=True)

        thumb_img = img.copy()
        thumb_img.thumbnail(THUMB_SIZE, resample=Image.Resampling.LANCZOS)
        thumb_buf = io.BytesIO()
        thumb_img.save(thumb_buf, format="JPEG", quality=80, optimize=True)
        thumb_bytes = thumb_buf.getvalue()
        (folder / "thumb.jpg").write_bytes(thumb_bytes)

        return {
            "has_thumb": True,
            "thumb_b64": _make_thumb_b64(thumb_bytes),
            "width": img.width,
            "height": img.height,
        }
    except Exception as e:
        logger.warning(f"Could not generate PDF cover thumbnail: {e}")
        return {"has_thumb": False}


def extract_video_thumbnail(video_path: Path, folder: Path) -> dict[str, Any]:
    """Extracts a keyframe from a video via FFmpeg and creates ai_view.jpg + thumb.jpg."""
    ffmpeg_bin = resolve_ffmpeg_binary()
    tmp_frame = folder / "temp_frame.jpg"
    extracted = False
    for ss in ("00:00:01", "00:00:00"):
        cmd = [ffmpeg_bin, "-ss", ss, "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(tmp_frame), "-y"]
        try:
            subprocess.run(
                cmd, capture_output=True, timeout=12,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False
            )
            if tmp_frame.is_file() and tmp_frame.stat().st_size > 0:
                extracted = True
                break
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.debug(f"FFmpeg frame capture failed at {ss}: {e}")

    if not extracted:
        return generate_document_card(video_path.name, "video", folder)

    try:
        raw_bytes = tmp_frame.read_bytes()
        meta = process_image_attachment(raw_bytes, folder)
        return meta
    finally:
        try:
            if tmp_frame.is_file():
                tmp_frame.unlink()
        except OSError:
            pass


def _card_frame(draw) -> None:
    """Card border."""
    draw.rounded_rectangle([6, 6, 249, 249], radius=16, outline=(56, 189, 248), width=2)


def _card_badge(draw, name: str, kind: str) -> str:
    """Top badge: extension label (at most 6 chars) on a cyan pill; returns the label."""
    ext = Path(name).suffix.upper().lstrip(".") or kind.upper()
    ext = ext[:6]
    draw.rounded_rectangle([18, 18, 95, 48], radius=6, fill=(14, 116, 144))  # cyan-700
    return ext


def _card_fonts():
    """Bold badge/title and regular body fonts; default font when Arial is missing."""
    from PIL import ImageFont
    try:
        font_badge = ImageFont.truetype("arialbd.ttf", 14)
        font_title = ImageFont.truetype("arialbd.ttf", 13)
        font_body = ImageFont.truetype("arial.ttf", 11)
    except OSError:
        font_badge = ImageFont.load_default()  # type: ignore[assignment]
        font_title = font_badge
        font_body = font_badge
    return font_badge, font_title, font_body


def _card_preview_lines(draw, preview_text: str, kind: str, font_body) -> None:
    """Preview text lines, or simulated document lines when there is no preview."""
    if preview_text.strip():
        clean = " ".join(preview_text.split())
        lines: list[str] = []
        while clean and len(lines) < 6:
            lines.append(clean[:30])
            clean = clean[30:]
        y = 90
        for l in lines:
            draw.text((18, y), l, fill=(148, 163, 184), font=font_body)
            y += 18
    else:
        draw.rounded_rectangle([18, 96, 236, 102], radius=3, fill=(51, 65, 85))
        draw.rounded_rectangle([18, 114, 210, 120], radius=3, fill=(51, 65, 85))
        draw.rounded_rectangle([18, 132, 224, 138], radius=3, fill=(51, 65, 85))
        draw.rounded_rectangle([18, 150, 180, 156], radius=3, fill=(51, 65, 85))
        draw.rounded_rectangle([18, 168, 200, 174], radius=3, fill=(51, 65, 85))
        draw.text((18, 205), f"Type: {kind.capitalize()}", fill=(100, 116, 139), font=font_body)


def generate_document_card(name: str, kind: str, folder: Path, preview_text: str = "") -> dict[str, Any]:
    """Renders a sleek, stylish dark card thumbnail for non-image documents (docx, txt, code, etc.)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (256, 256), (15, 23, 42))  # slate-900
    draw = ImageDraw.Draw(img)
    _card_frame(draw)
    ext = _card_badge(draw, name, kind)
    font_badge, font_title, font_body = _card_fonts()
    draw.text((28, 24), ext, fill=(255, 255, 255), font=font_badge)

    # Document name (truncated)
    display_name = name if len(name) <= 24 else name[:21] + "..."
    draw.text((18, 62), display_name, fill=(241, 245, 249), font=font_title)

    _card_preview_lines(draw, preview_text, kind, font_body)

    thumb_buf = io.BytesIO()
    img.save(thumb_buf, format="JPEG", quality=80, optimize=True)
    thumb_bytes = thumb_buf.getvalue()
    (folder / "thumb.jpg").write_bytes(thumb_bytes)
    return {
        "has_thumb": True,
        "thumb_b64": _make_thumb_b64(thumb_bytes),
        "width": 256,
        "height": 256,
    }
