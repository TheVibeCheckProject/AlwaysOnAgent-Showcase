"""OCR for documents.py §2
========================
Tesseract: preparing a picture, reading it, putting its words back into lines.

Split out of core/documents.py §2 on 2026-11-04; core/documents/__init__.py re-exports
the public names for backwards compatibility.
Moved into the core.documents package on 2026-11-04; the old
core/documents_<name>.py paths were thin shims, removed 2026-11-04.
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §2  OCR
#     Text from pictures and scanned pages with Tesseract (local, no AI), with a confidence score.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
import io
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

OCR_TIMEOUT = 90


def _dc():
    """core.documents, looked up at call time so tests patching names on it take effect."""
    import core.documents
    return core.documents


def tesseract_path() -> str | None:
    for candidate in (os.environ.get("TESSERACT_PATH", ""), shutil.which("tesseract") or "",
                      r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def prepare_for_ocr(image) -> Any:
    """A cleaner copy for Tesseract: upright (phone EXIF), grayscale, small images enlarged, contrast stretched."""
    from PIL import Image, ImageFilter, ImageOps
    img = ImageOps.exif_transpose(image)
    if img.mode in ("RGBA", "LA", "P"):                 # transparent parts become white, not black
        base = img.convert("RGBA")
        img = Image.alpha_composite(Image.new("RGBA", base.size, (255, 255, 255, 255)), base)
    img = img.convert("L")
    longest = max(img.size)
    if longest < 1600:                                  # Tesseract reads best at roughly 300 dpi letter heights
        scale = min(3.0, 1600 / max(1, longest))
        img = img.resize((int(img.width * scale), int(img.height * scale)), resample=3)    # 3 = BICUBIC
    elif longest > 4000:                                # huge photos: slower with no gain
        scale = 4000 / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)), resample=3)
    img = ImageOps.autocontrast(img, cutoff=1)
    return img.filter(ImageFilter.SHARPEN)


def ocr_image(image) -> tuple[str, float | None]:
    """(text, mean word confidence 0-100 or None) for a PIL image; raises DocumentError without Tesseract."""
    exe = tesseract_path()
    if not exe:
        raise _dc().DocumentError("OCR isn't installed on the agent's PC (Tesseract), so images can't be read yet")
    buf = io.BytesIO()
    prepare_for_ocr(image).save(buf, format="PNG")
    try:
        proc = subprocess.run([exe, "stdin", "stdout", "-l", "eng", "--psm", "3", "tsv"], input=buf.getvalue(),
                              capture_output=True, timeout=OCR_TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    except subprocess.TimeoutExpired:
        raise _dc().DocumentError("OCR took too long on this image")
    if proc.returncode != 0:
        raise _dc().DocumentError(f"OCR failed: {proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return text_from_tsv(proc.stdout.decode("utf-8", "replace"))


def text_from_tsv(tsv: str) -> tuple[str, float | None]:
    """Tesseract's TSV (one word per row with its block/paragraph/line) back into lines of text + mean confidence."""
    lines: dict[tuple[int, int, int, int], list[str]] = {}
    confs: list[float] = []
    rows = tsv.splitlines()
    header = rows[0].split("\t") if rows else []
    col = {name: i for i, name in enumerate(header)}
    for row in rows[1:]:
        cells = row.split("\t")
        if len(cells) < len(header) or not cells[col["text"]].strip():
            continue
        try:
            conf = float(cells[col["conf"]])
        except ValueError:
            continue
        if conf < 0:
            continue
        key = cast(tuple[int, int, int, int], tuple(int(cells[col[k]]) for k in ("page_num", "block_num", "par_num", "line_num")))
        lines.setdefault(key, []).append(cells[col["text"]])
        confs.append(conf)
    text, last_par = [], None
    for key in sorted(lines):
        if last_par is not None and key[:3] != last_par:
            text.append("")                             # a blank line between paragraphs
        text.append(" ".join(lines[key]))
        last_par = key[:3]
    return "\n".join(text).strip(), (round(sum(confs) / len(confs), 1) if confs else None)
