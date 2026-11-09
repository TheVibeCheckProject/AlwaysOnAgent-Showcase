"""
Documents attached in the chat
==============================
DeskAI uploads a file (POST /api/chat/attachments); this keeps it and pulls out its text, and the chat turn it's
sent with gets that text beside the owner's message. Nothing here is an AI model:
- Word (.docx), text-like files (.txt, .md, .csv, .json, code) and ordinary PDFs: the text stored in the file,
  read directly. Exact and instant.
- Images, and PDF pages that are only a picture (scans): OCR with Tesseract, on this PC. The image is cleaned up
  first (turned upright, grayscale, small ones enlarged, contrast stretched), and the result carries Tesseract's
  own confidence, so the chat is told when the reading is shaky. OCR text can contain mistakes; the chat is told
  it came from OCR.

Kept in ATTACH_DIR/<id>/ (the original file + meta.json with the text) for KEEP_DAYS, so the assistant can
re-read one later in the conversation (the read_attachment tool). The chat history stores the owner's message
with "[attached: name]", not the document's text, so long-term memory isn't filled with whole documents.

How an attachment goes: the app uploads it -> save() (§5) keeps it and extract() (§3) reads its text (OCR for
images and scans, §2), with a thumbnail (§4). The chat turn it's sent with gets chat_context() (§6) beside your
message; a follow-up a message or two later gets earlier_attachments() again.

MAP OF THIS FILE
----------------
Split 2026-11-04: the sections first lived in core/documents_*.py, then moved into this
package (core/documents/) on 2026-11-04; this __init__ re-exports them, so
`from core.documents import ...` and patching names on this module (documents.ATTACH_DIR,
documents.ocr_image, documents.load, ...) keep working exactly as before.
The old flat paths (core/documents.py, core/documents_*.py) were thin shims; they were removed 2026-11-04.

  §1   Setup                         imports, where attachments are kept, the error type (here)
  §2   OCR                           core/documents/ocr.py
  §3   Reading a file's text         core/documents/read.py
  §4   Thumbnails and previews       core/documents/previews.py
  §5   Keeping attachments           core/documents/store.py
  §6   For the chat's prompt         core/documents/chat.py
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §1  SETUP
#     Imports, the attachments folder; DocumentError carries the reason in words for you.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
import logging
import os
from pathlib import Path

try:
    from core.config import WORKSPACE_DIR
except ImportError:
    from config import WORKSPACE_DIR  # type: ignore[no-redef]

logger = logging.getLogger("Documents")
ATTACH_DIR = Path(os.environ.get("ATTACHMENTS_DIR") or (WORKSPACE_DIR / "data" / "attachments"))


class DocumentError(Exception):
    """Why a file can't be used, in words for the owner."""


from .chat import (
    ATTACHMENT_REF,
    EARLIER_MESSAGES,
    EARLIER_MINUTES,
    INLINE_CHARS,
    MISREAD_NOTE,
    chat_context,
    describe,
    earlier_attachments,
    scene_block,
)
from .ocr import OCR_TIMEOUT, ocr_image, prepare_for_ocr, tesseract_path, text_from_tsv
from .previews import (
    AI_VIEW_MAX_DIM,
    THUMB_SIZE,
    _make_thumb_b64,
    extract_pdf_cover,
    extract_video_thumbnail,
    generate_document_card,
    process_image_attachment,
)
from .read import (
    IMAGE_EXTENSIONS,
    LOW_CONFIDENCE,
    MAX_OCR_PAGES,
    MAX_TEXT_CHARS,
    SUPPORTED,
    TEXT_EXTENSIONS,
    VIDEO_EXTENSIONS,
    _decode,
    _docx_text,
    _pdf_text,
    extract,
)
from .store import KEEP_DAYS, MAX_BYTES, RESERVED_NAMES, _safe_name, load, prune, recent, save, update

__all__ = [
    "AI_VIEW_MAX_DIM",
    "ATTACHMENT_REF",
    "ATTACH_DIR",
    "EARLIER_MESSAGES",
    "EARLIER_MINUTES",
    "IMAGE_EXTENSIONS",
    "INLINE_CHARS",
    "KEEP_DAYS",
    "LOW_CONFIDENCE",
    "MAX_BYTES",
    "MAX_OCR_PAGES",
    "MAX_TEXT_CHARS",
    "MISREAD_NOTE",
    "OCR_TIMEOUT",
    "RESERVED_NAMES",
    "SUPPORTED",
    "TEXT_EXTENSIONS",
    "THUMB_SIZE",
    "VIDEO_EXTENSIONS",
    "DocumentError",
    "_decode",
    "_docx_text",
    "_make_thumb_b64",
    "_pdf_text",
    "_safe_name",
    "chat_context",
    "describe",
    "earlier_attachments",
    "extract",
    "extract_pdf_cover",
    "extract_video_thumbnail",
    "generate_document_card",
    "load",
    "ocr_image",
    "prepare_for_ocr",
    "process_image_attachment",
    "prune",
    "recent",
    "save",
    "scene_block",
    "tesseract_path",
    "text_from_tsv",
    "update",
]
