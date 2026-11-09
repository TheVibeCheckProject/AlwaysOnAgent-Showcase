"""Today §7: pictures — card images: fetched in parallel, checked, shrunk, pruned.

Moved into core/today/pictures.py on 2026-11-04 (was core/today_pictures.py, split out of
core/today.py §7 on 2026-11-03 as _PicturesMixin; Today inherits it). core/today_pictures.py
is now a re-export shim.

BUILD_FETCH_SECONDS is read from core.today at call time (_td), not imported: the tests
patch it on core.today (test_today_feeds_fixes.py), and an import-time binding would ignore
the patch — the same lesson as guard_on in core/memory_prompt.py.
"""
import concurrent.futures
import datetime
import hashlib
import io
import logging
import re
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .constants import FETCH_WORKERS, IMAGE_MAX_WIDTH, KEEP_IMAGES, MAX_IMAGE_PIXELS, MIN_IMAGE_WIDTH
except ImportError:
    from today_constants import FETCH_WORKERS, IMAGE_MAX_WIDTH, KEEP_IMAGES, MAX_IMAGE_PIXELS, MIN_IMAGE_WIDTH  # type: ignore[no-redef]

try:
    from .links import page_preview
except ImportError:
    from today_links import page_preview  # type: ignore[no-redef]


logger = logging.getLogger("AlwaysOnAgent.Today")


def _td():
    """core.today, looked up at call time, so a test patching it takes effect."""
    import core.today as _m
    return _m


class _PicturesMixin:
    # Host state set by Today.__init__ (core/today/__init__.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime. Types mirror Today.__init__.
    db: "DatabaseManager"  # quoted: imported only under TYPE_CHECKING
    images_dir: Path
    fetch: Callable[[str], tuple[bytes, str]]

    def _with_media(self, cards: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str | None]]:
        """Each card with its page title and site filled in and its image kept, fetched in parallel within
        BUILD_FETCH_SECONDS in all: one after another, at 15 s a fetch, six stories could keep the phone waiting
        minutes (audit 0.4). A card whose downloads didn't finish in time goes in as text."""
        def prepare(card: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
            c = dict(card)
            image_url = c.pop("image_url", None)
            if c.pop("preview", False):
                image_url = self._preview_into(c) or None
            if image_url and image_url.startswith("youtube-thumb:"):
                return c, self._youtube_thumb(image_url.split(":", 1)[1])
            return c, (self._keep_image(image_url) if image_url else None)

        def as_text(card: dict[str, Any]) -> tuple[dict[str, Any], None]:
            c = dict(card)
            c.pop("image_url", None)
            if c.pop("preview", False):
                c["title"], c["site"] = "", re.sub(r"^www\.", "", urllib.parse.urlparse(c.get("link") or "").netloc)
            return c, None

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=FETCH_WORKERS, thread_name_prefix="TodayFetch")
        try:
            futures = [pool.submit(prepare, c) for c in cards]
            concurrent.futures.wait(futures, timeout=_td().BUILD_FETCH_SECONDS)
            return [f.result() if f.done() and not f.exception() else as_text(card)
                    for f, card in zip(futures, cards)]
        finally:
            pool.shutdown(wait=False, cancel_futures=True)      # a download still running is left to finish unused

    def _preview_into(self, card: dict[str, Any]) -> str:
        """Fills a story's title and site from its page; returns the page's preview image link ('' if none)."""
        try:
            body, ctype = self.fetch(card["link"])
            if "html" not in ctype and ctype:
                raise ValueError(f"not a page ({ctype})")
            p = page_preview(body.decode("utf-8", errors="replace"), card["link"])
        except Exception as e:
            logger.info(f"Today: no preview for a story ({type(e).__name__})")
            p = {"title": "", "site": re.sub(r"^www\.", "", urllib.parse.urlparse(card["link"]).netloc), "image": ""}
        # A section page's generic name ("Newsroom", "Blog") says nothing: the finding's own line is the headline then
        generic = len(p["title"].split()) < 3 or p["title"].strip().lower() == p["site"].strip().lower()
        card["title"] = "" if generic else p["title"]
        card["site"] = p["site"]
        return p["image"]

    def _youtube_thumb(self, vid: str) -> str | None:
        """A video's 16:9 thumbnail. hqdefault.jpg (always there) is 4:3 with black bars baked into its top and bottom,
        which showed on the cards (Alex, 2026-10-29): the full-size 16:9 ones first, else hqdefault with its bars cut."""
        base = f"https://i.ytimg.com/vi/{urllib.parse.quote(vid)}/"
        for name in ("maxresdefault.jpg", "hq720.jpg"):
            kept = self._keep_image(base + name)
            if kept:
                return kept
        return self._keep_image(base + "hqdefault.jpg", crop_16x9=True)

    def _keep_image(self, url: str, crop_16x9: bool = False) -> str | None:
        """Downloads, checks and shrinks a card's image into images_dir; None if it's missing, broken or too small.
        crop_16x9: the middle 16:9 of a letterboxed 4:3 picture."""
        try:
            from PIL import Image
            body, _ = self.fetch(url)
            img: Image.Image = Image.open(io.BytesIO(body))  # reads the header only: the size is known before decoding
            if img.width < MIN_IMAGE_WIDTH or img.width * img.height > MAX_IMAGE_PIXELS:
                return None                               # too small to show, or a decompression bomb (audit 0.11)
            img.draft("RGB", (IMAGE_MAX_WIDTH, IMAGE_MAX_WIDTH * 4))   # a JPEG decodes at a smaller scale
            img.load()
            if crop_16x9:
                h = round(img.width * 9 / 16)
                top = max(0, (img.height - h) // 2)
                img = img.crop((0, top, img.width, top + h))
            if img.width > IMAGE_MAX_WIDTH:
                img = img.resize((IMAGE_MAX_WIDTH, round(img.height * IMAGE_MAX_WIDTH / img.width)))
            name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".jpg"
            img.convert("RGB").save(self.images_dir / name, "JPEG", quality=85)
            return name
        except Exception as e:
            logger.info(f"Today: image not kept ({type(e).__name__})")
            return None

    def _prune(self, now: datetime.datetime) -> None:
        """Images of old, unsaved cards go (the cards stay, as text)."""
        cutoff = (now - KEEP_IMAGES).isoformat()
        with self.db.get_connection() as conn:
            old = conn.execute("SELECT id, image FROM today_items WHERE image IS NOT NULL AND state != 'saved' "
                               "AND created_at < ?", (cutoff,)).fetchall()
            keep = {r[0] for r in conn.execute("SELECT image FROM today_items WHERE image IS NOT NULL "
                                               "AND (state = 'saved' OR created_at >= ?)", (cutoff,))}
            for r in old:
                if r["image"] not in keep:
                    (self.images_dir / r["image"]).unlink(missing_ok=True)
                conn.execute("UPDATE today_items SET image = NULL WHERE id = ?", (r["id"],))
