"""Reading a file's text for documents.py §3
==========================================
text files, Word, PDF (OCR for scanned pages); extract() picks the way.

Split out of core/documents.py §3 on 2026-11-04; core/documents/__init__.py re-exports
the public names for backwards compatibility.
Moved into the core.documents package on 2026-11-04; the old
core/documents_<name>.py paths remain as thin shims.
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
# §3  READING A FILE'S TEXT
#     extract() reads any supported file and says how (text, ocr, mixed, video) and how sure it is.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
import io
import re
from pathlib import Path
from typing import Any

MAX_TEXT_CHARS = 400_000          # kept per document (a few hundred pages)
MAX_OCR_PAGES = 20                # scanned PDF pages run through OCR per document
LOW_CONFIDENCE = 80               # Tesseract's mean word confidence (0-100) below which the reading is flagged
                                  # (a trail-cam banner read "71°F" as "11°F" at 72 on 2026-09-29)

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".xml", ".html", ".htm", ".yaml",
                   ".yml", ".ini", ".cfg", ".toml", ".py", ".js", ".ts", ".kt", ".java", ".c", ".cpp", ".h", ".cs",
                   ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat", ".sql", ".css", ".srt", ".vtt"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v"}
SUPPORTED = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | {".pdf", ".docx"}


def _dc():
    """core.documents, looked up at call time so tests patching names on it take effect."""
    import core.documents
    return core.documents


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _docx_text(data: bytes) -> str:
    import docx
    doc = docx.Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        parts.append("")
        for row in table.rows:
            parts.append(" | ".join(" ".join(c.text.split()) for c in row.cells))
    return "\n".join(parts)


def _pdf_text(data: bytes) -> tuple[str, int, int, list[float], list[str]]:
    """(text, pages, pages read by OCR, OCR confidences, warnings)."""
    import pymupdf as fitz  # "fitz" is its old, deprecated name
    from PIL import Image
    try:
        doc: Any = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise _dc().DocumentError(f"this PDF can't be opened ({e})")
    if doc.needs_pass:
        raise _dc().DocumentError("this PDF is password-protected")
    parts: list[str] = []
    ocr_pages = 0
    confs: list[float] = []
    warnings: list[str] = []
    for number, page in enumerate(doc, 1):
        text = page.get_text("text").strip()
        if len(text) < 25 and page.get_images():        # a picture of a page (a scan): read it with OCR
            if ocr_pages >= MAX_OCR_PAGES:
                if not any("scanned pages" in w for w in warnings):
                    warnings.append(f"only the first {MAX_OCR_PAGES} scanned pages were read")
                continue
            pix = page.get_pixmap(dpi=250)
            image = Image.frombytes("RGB" if pix.n < 4 else "RGBA", (pix.width, pix.height), pix.samples)
            text, conf = _dc().ocr_image(image)
            ocr_pages += 1
            if conf is not None:
                confs.append(conf)
        parts.append(f"--- Page {number} ---\n{text}")
    return "\n\n".join(parts), len(doc), ocr_pages, confs, warnings


def extract(name: str, data: bytes) -> dict[str, Any]:
    """{"kind", "method" (text | ocr | mixed | video), "text", "pages"?, "confidence"?, "warnings"}; raises DocumentError."""
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise _dc().DocumentError(f"'{ext or name}' files aren't supported yet (PDF, Word .docx, text files, images and videos are)")
    warnings: list[str] = []
    result: dict[str, Any] = {"warnings": warnings}
    if ext in TEXT_EXTENSIONS:
        result.update(kind="text", method="text", text=_decode(data))
    elif ext == ".docx":
        try:
            result.update(kind="word", method="text", text=_docx_text(data))
        except Exception as e:
            raise _dc().DocumentError(f"this Word file can't be read ({e})")
    elif ext == ".pdf":
        text, pages, ocr_pages, confs, pdf_warnings = _pdf_text(data)
        warnings += pdf_warnings
        method = "text" if not ocr_pages else ("ocr" if ocr_pages == pages else "mixed")
        result.update(kind="pdf", method=method, text=text, pages=pages, ocr_pages=ocr_pages,
                      confidence=round(sum(confs) / len(confs), 1) if confs else None)
    elif ext in VIDEO_EXTENSIONS:
        result.update(kind="video", method="video", text=f"[Video attachment: {Path(name).name}]")
    else:
        from PIL import Image
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as e:
            raise _dc().DocumentError(f"this image can't be opened ({e})")
        text, conf = _dc().ocr_image(image)
        result.update(kind="image", method="ocr", text=text, confidence=conf,
                      size=f"{image.width}x{image.height}")
    text = re.sub(r"\n{3,}", "\n\n", (result["text"] or "").replace("\r\n", "\n")).strip()
    if len(text) > MAX_TEXT_CHARS:
        warnings.append(f"only the first {MAX_TEXT_CHARS:,} characters were kept")
        text = text[:MAX_TEXT_CHARS]
    result["text"] = text
    if not text:
        warnings.append("no text was found" + (" (a photo with no readable writing?)" if result["kind"] == "image"
                                               else ""))
    conf = result.get("confidence")
    if conf is not None and conf < LOW_CONFIDENCE:
        warnings.append(f"the OCR reading is uncertain (confidence {conf:.0f}/100): expect mistakes")
    return result
