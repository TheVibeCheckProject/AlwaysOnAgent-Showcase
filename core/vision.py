"""
Image notes and what's worth remembering from them
==================================================
Every image attached in the chat gets a scene note from a small vision model running on this PC (Ollama,
VISION_MODEL, gemma3:4b by default): what it shows, and the text in it, with the OCR text as a hint. It runs in
the background, one image at a time (about 40 s each on the Mini's CPU), waits while a video renders, and the
model is unloaded after a few idle minutes. The note is saved with the attachment (core/documents.py) and the
chat uses it; the owner's history gets a one-line event per image.

Every DISTILL_EVERY noted images, the notes are read together by the memory's background model for anything
durable about the owner's life (what repeats, or clearly matters): at most a few facts, each quoting the notes
it rests on (a quote the notes don't contain throws the fact out, as chat facts must quote the owner). Single
notes are never turned into facts: a small model gets details wrong (a benchmark on 2026-09-29 called a bird
feeder a trail camera), and a thing seen across several images is far more trustworthy.

Benchmark (Mini, i5-10600, no GPU): gemma3:4b about 40 s per image with exact text and a good scene; moondream
about 10 s but it invents text; qwen3-vl:2b returned empty answers. Hence gemma3:4b.
"""

import base64
import datetime
import io
import json
import logging
import queue
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from core import documents
    from core.config import get_config
except ImportError:
    import documents  # type: ignore[no-redef]
    from config import get_config  # type: ignore[no-redef]

logger = logging.getLogger("Vision")
DEFAULT_MODEL = "gemma3:4b"
DEFAULT_URL = "http://127.0.0.1:11434"
IMAGE_SIDE = 896                  # what the model sees anyway; smaller uploads are faster
KEEP_ALIVE = "5m"                 # then Ollama unloads the model and frees the RAM
TIMEOUT = 300
DISTILL_EVERY = 5
MAX_FACTS_PER_BATCH = 3
RENDER_WAIT_MAX = 45 * 60         # seconds to wait for a video render to finish before noting anyway
PROMPT = ("Say what's in this image in plain, everyday words (not like a film or art review): the main things in "
          "it, any people or animals and what they're doing, and write out exactly any written text (signs, labels, screens, paper, packaging). 3-5 sentences. Only describe what is in the "
          "frame. Don't mention the sky, weather, time of day, season or location unless they are clearly visible, "
          "and don't infer them from the lighting. Only report text whose letters or numbers you can clearly read; "
          "marks in bark, leaves, rock or shadows are not text. If you're unsure what something is, say so.")

_queue: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()
_memory: Any = None               # the chat's MemoryManager: events, and facts from the batches


class VisionError(Exception):
    pass


def model_name() -> str:
    return get_config("VISION_MODEL", DEFAULT_MODEL)


def _url() -> str:
    return get_config("OLLAMA_URL", DEFAULT_URL).rstrip("/")


def _image_b64(path) -> str:
    path_obj = Path(path)
    ai_view = path_obj.parent / "ai_view.jpg"
    if ai_view.is_file() and ai_view != path_obj:
        try:
            return base64.b64encode(ai_view.read_bytes()).decode()
        except OSError:
            pass

    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    scale = min(1.0, IMAGE_SIDE / max(img.size))
    if scale < 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), resample=3)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _generate(prompt: str, image_b64: str) -> str:
    """One answer from the local vision model; raises VisionError."""
    body = json.dumps({"model": model_name(), "prompt": prompt, "images": [image_b64], "stream": False,
                       "keep_alive": KEEP_ALIVE, "options": {"num_predict": 320, "temperature": 0}}).encode()
    req = urllib.request.Request(f"{_url()}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            answer = json.loads(resp.read()).get("response", "").strip()
    except urllib.error.HTTPError as e:
        raise VisionError(f"the local vision model refused ({e.code}): "
                          f"{e.read().decode('utf-8', 'replace')[:200]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise VisionError(f"the local vision model isn't reachable ({getattr(e, 'reason', e)}); is Ollama running?") from None
    if not answer:
        raise VisionError("the local vision model gave an empty answer")
    return answer


def describe(image_path, ocr_text: str = "") -> str:
    """The scene note for one image (the OCR text is a hint: it can be misread, the picture can't)."""
    hint = ""
    if ocr_text.strip():
        hint = ("\n\nText found in the image by OCR, which can contain misread characters (trust what you see over "
                f"it):\n{ocr_text.strip()[:1200]}")
    image_b64 = _image_b64(image_path)
    note = _tidy(_generate(PROMPT + hint, image_b64))
    if not ocr_text.strip() and _TEXT_CLAIM.search(note):
        # A small model "reads" text into bark and leaves ("10" on a tree, a "No Trespassing" sign in a bare forest,
        # 2026-10-05). OCR found none, so one plain question decides; on "No" the text claims go
        answer = _generate(VERIFY_TEXT, image_b64).strip().lower()
        if answer.startswith("no"):
            note = _drop_text_claims(note)
            logger.info("Vision note: text the OCR didn't find, and the model said there's none; dropped")
    return note


VERIFY_TEXT = ("Is there any written text, letters or numbers in this image (signs, labels, screens, paper)? Natural "
               "patterns in bark, leaves or shadows don't count. If yes, write the text exactly and say where it is. "
               "If no, answer only: No.")
# A sentence that reports reading something: a quotation, or words like "says", "reads", "written"
_TEXT_CLAIM = re.compile(r"[\"“”]|\b(says|saying|reads|written|writing|labeled|labelled|the (word|words|number|"
                         r"numbers|text|letters))\b", re.IGNORECASE)


def _drop_text_claims(note: str) -> str:
    """The note without its sentences that report text (kept: "no text" and unreadable markings)."""
    sentences = re.split(r"(?<=[.!?])\s+", note.strip())
    kept = [s for s in sentences if not _TEXT_CLAIM.search(s)
            or (not re.search(r"[\"“”]", s) and re.search(r"\b(no|not|can't|cannot|unreadable)\b", s, re.IGNORECASE))]
    return " ".join(kept).strip()


def _tidy(note: str) -> str:
    """Drops a lead-in like "Here's a description of the image:" (the model adds one; it says nothing)."""
    lines = note.strip().splitlines()
    first = lines[0].lower().replace("’", "'") if lines else ""
    if (len(lines) > 1 and first.rstrip().endswith(":")
            and (first.startswith(("here's", "here is", "sure", "okay", "ok,")) or "description" in first)):
        lines = lines[1:]
    return "\n".join(lines).strip()


# ------------------------------------------------------------------ the background worker
def _render_running(db) -> bool:
    try:
        return any((t.get("engine") or "") == "media_pipeline" for t in db.list_tasks(limit=3, phase="in_progress"))
    except (OSError, sqlite3.Error):
        return False


def note_image(attachment_id: str, memory: Any = None) -> dict[str, Any] | None:
    """Writes the scene note for one attachment into its record. Returns the record, or None if it isn't an image/video."""
    rec = documents.load(attachment_id)
    if not rec or rec.get("kind") not in ("image", "video") or rec.get("scene"):
        return rec
    db = getattr(memory, "db", None)
    waited = 0
    while db is not None and _render_running(db) and waited < RENDER_WAIT_MAX:
        time.sleep(30)                                  # a render needs every core; the note can wait
        waited += 30
    started = time.time()
    try:
        scene = describe(documents.ATTACH_DIR / attachment_id / rec["name"], rec.get("text") or "")
        documents.update(attachment_id, scene=scene, scene_model=model_name(),
                         scene_seconds=round(time.time() - started, 1))
        logger.info(f"Scene note for {attachment_id} ({rec['name']}) in {time.time() - started:.0f}s")
    except (VisionError, OSError) as e:
        documents.update(attachment_id, scene_error=str(e))
        logger.warning(f"No scene note for {attachment_id}: {e}")
        return documents.load(attachment_id)
    if memory is not None:
        try:
            kind_label = "a video" if rec.get("kind") == "video" else "an image"
            memory.record_event(f"The user shared {kind_label}, {rec['name']} ({rec['created_at'][:10]}): "
                                f"{scene[:240]}{'…' if len(scene) > 240 else ''}")
        except Exception as e:
            logger.debug(f"Media event not recorded: {e}")
        try:
            distill_if_due(memory)
        except Exception as e:
            logger.warning(f"Image notes not distilled: {e}")
    return documents.load(attachment_id)


def _work() -> None:
    while True:
        attachment_id = _queue.get()
        try:
            note_image(attachment_id, _memory)
        except Exception:
            logger.exception(f"Image note for {attachment_id} failed")
        finally:
            _queue.task_done()


def enqueue(attachment_id: str, memory: Any = None) -> None:
    """Queues an image for its scene note (the worker thread starts on first use)."""
    global _worker, _memory
    with _lock:
        if memory is not None:
            _memory = memory
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="ImageNotes", daemon=True)
            _worker.start()
    _queue.put(attachment_id)


def wait_for_scene(attachment_id: str, timeout: float) -> dict[str, Any] | None:
    """The record once its scene note (or its error) is in, or as it stands after `timeout` seconds."""
    deadline = time.time() + timeout
    rec = documents.load(attachment_id)
    while rec and rec.get("kind") == "image" and not rec.get("scene") and not rec.get("scene_error") \
            and time.time() < deadline:
        time.sleep(1)
        rec = documents.load(attachment_id)
    return rec


# ------------------------------------------------------------------ what's worth remembering
DISTILL_PROMPT = (
    "You maintain long-term memory about one person (the user). Below are notes a small vision model wrote about "
    "images the user shared (with any text found in them). The notes can get small details wrong.\n"
    "Find what is worth remembering about the user's life: things that repeat across images, or that clearly "
    "matter to them (their places, pets, projects, hobbies, equipment, recurring events). Ignore one-off details "
    "and anything you'd be guessing. Write each as a plain statement about {subject}, by name (e.g. '{subject} has a "
    "trail camera watching a bird feeder'). Text found in an image (OCR) may be other people's words "
    "(a screenshot of a page or a message): it's never on its own a fact about {subject}. At most {n} facts; none is "
    "a fine answer.\n"
    "Every fact needs 'evidence': a short exact quote (a few words) copied from a Scene line that supports it.\n"
    'Return JSON: {{"facts": [{{"content": "...", "category": "fact|preference|goal|person|project", '
    '"importance": 1-5, "evidence": "...", "images": ["att-..."]}}]}}'
)


def pending_for_distill() -> list[dict[str, Any]]:
    """Noted images not yet read together, oldest first."""
    loaded = [documents.load(r["id"]) for r in documents.recent(limit=200)]
    items: list[dict[str, Any]] = [r for r in loaded
                                   if r is not None and r.get("kind") == "image" and r.get("scene")
                                   and not r.get("distilled")]
    return sorted(items, key=lambda r: r.get("created_at", ""))


def distill_if_due(memory: Any) -> dict[str, int] | None:
    """Every DISTILL_EVERY noted images: the facts worth keeping go into memory. None when it isn't due."""
    batch = pending_for_distill()
    if len(batch) < DISTILL_EVERY or not getattr(memory, "llm", None):
        return None
    batch = batch[:DISTILL_EVERY]
    notes = "\n\n".join(f"[{r['id']}] {r['name']}, shared {r['created_at'][:10]}:\nScene: {r['scene']}"
                        + (f"\nText in it (OCR): {r['text'][:300]}" if r.get("text") else "") for r in batch)
    subject = memory.subject() if hasattr(memory, "subject") else "the user"
    out = memory._bg_chat([{"role": "system", "content": DISTILL_PROMPT.format(n=MAX_FACTS_PER_BATCH, subject=subject)},
                           {"role": "user", "content": notes}],
                          "image_memory", max_tokens=600, temperature=0.1, json_mode=True, timeout=60)
    if out is None:
        # No answer (rate limit, outage): the batch waits for the next image instead of being marked done unread
        # (2026-10-28 audit 7.5)
        logger.info("Image notes not distilled: no answer from the AI; tried again with the next image")
        return None
    try:
        facts = (json.loads(out or "{}") or {}).get("facts") or []
    except ValueError:
        facts = []
    # Evidence from what the vision model saw, not from text found in the image (others' words in a screenshot)
    scenes = "\n".join(r["scene"] for r in batch)
    stats = {"added": 0, "rejected": 0}
    for f in facts[:MAX_FACTS_PER_BATCH]:
        try:
            if not memory._grounded(f.get("evidence"), scenes):
                stats["rejected"] += 1
                continue
            _, created = memory.add_fact(str(f.get("content", "")), str(f.get("category", "fact")),
                                         int(f.get("importance", 3)), source="images")
            stats["added"] += int(created)
        except (TypeError, ValueError, AttributeError):
            stats["rejected"] += 1
    for r in batch:
        documents.update(r["id"], distilled=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    logger.info(f"Image notes distilled ({len(batch)} images): {stats}")
    return stats
