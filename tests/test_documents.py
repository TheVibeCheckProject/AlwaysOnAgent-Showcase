"""
Documents attached in the chat: reading them (text, Word, PDF, OCR), keeping them, and the chat turn that gets
their text. The chat's AI is a stand-in; OCR tests run only where Tesseract is installed.
"""

import base64
import io
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real tools: point the agent at folders where they aren't installed
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))
os.environ.setdefault("OLLAMA_URL", "http://127.0.0.1:9")          # never the real vision model
_IMPORT_TEMP_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ.setdefault("STATE_DB_PATH", str(Path(_IMPORT_TEMP_DIR.name) / "documents_test.db"))

from core import documents  # noqa: E402
from core.assistant_tools import AssistantTools  # noqa: E402
from core.database import DatabaseManager  # noqa: E402

needs_ocr = pytest.mark.skipif(documents.tesseract_path() is None, reason="Tesseract isn't installed here")


@pytest.fixture(autouse=True)
def attach_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "ATTACH_DIR", tmp_path / "attachments")
    return tmp_path / "attachments"


def _screenshot(lines=("Invoice 4471 for Example Site", "Total due 42.50 by October 15")) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (900, 60 + 50 * len(lines)), (255, 255, 255, 0))      # transparent, like many PNGs
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((20, 20 + 50 * i), line, fill=(0, 0, 0, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_text_and_word_files_are_read_directly():
    r = documents.extract("notes.txt", "café menu\r\nline 2".encode("utf-8"))
    assert r == {"kind": "text", "method": "text", "text": "café menu\nline 2", "warnings": []}
    assert documents.extract("old.txt", "naïve".encode("cp1252"))["text"] == "naïve"
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("Meeting notes")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Task", "Owner"
    buf = io.BytesIO()
    doc.save(buf)
    r = documents.extract("m.docx", buf.getvalue())
    assert r["kind"] == "word" and r["text"] == "Meeting notes\n\nTask | Owner"


def test_unsupported_empty_and_huge_files_are_refused():
    with pytest.raises(documents.DocumentError, match="aren't supported"):
        documents.extract("setup.exe", b"MZ")
    with pytest.raises(documents.DocumentError, match="empty"):
        documents.save("a.txt", b"")
    with pytest.raises(documents.DocumentError, match="limit is 15 MB"):
        documents.save("a.txt", b"x" * (documents.MAX_BYTES + 1))


@needs_ocr
def test_images_are_read_by_ocr_with_a_confidence():
    r = documents.extract("shot.png", _screenshot())
    assert r["kind"] == "image" and r["method"] == "ocr" and r["confidence"] > 80
    assert "Invoice 4471" in r["text"] and "October 15" in r["text"]


@needs_ocr
def test_a_pdf_reads_text_pages_directly_and_ocrs_scanned_ones():
    fitz = pytest.importorskip("pymupdf")
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Page one has real text.", fontsize=14)
    scanned = pdf.new_page()
    scanned.insert_image(scanned.rect, stream=_screenshot())
    r = documents.extract("mixed.pdf", pdf.tobytes())
    assert r["method"] == "mixed" and r["pages"] == 2 and r["ocr_pages"] == 1
    assert "--- Page 1 ---\nPage one has real text." in r["text"] and "Invoice 4471" in r["text"]


def test_ocr_tsv_becomes_lines_and_paragraphs():
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t95\tHello\n"
           "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t91\tthere\n"
           "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t60\tsecond\n"
           "4\t1\t2\t1\t1\t0\t0\t0\t1\t1\t-1\t\n"
           "5\t1\t2\t1\t1\t1\t0\t0\t1\t1\t30\tnew\n")
    text, conf = documents.text_from_tsv(tsv)
    assert text == "Hello there\nsecond\n\nnew" and conf == 69.0


def test_shaky_ocr_and_no_text_are_flagged(monkeypatch):
    monkeypatch.setattr(documents, "ocr_image", lambda image: ("lnv0ice", 41.0))
    r = documents.extract("blurry.jpg", _screenshot())
    assert any("uncertain (confidence 41/100)" in w for w in r["warnings"])
    monkeypatch.setattr(documents, "ocr_image", lambda image: ("", None))
    assert any("no text was found" in w for w in documents.extract("cat.png", _screenshot())["warnings"])


def test_kept_files_can_be_listed_reread_and_pruned(attach_dir):
    rec = documents.save("../../evil name?.txt", b"hello world", conversation_id="c1")
    assert rec["name"] == "evil name_.txt" and (attach_dir / rec["id"] / "evil name_.txt").read_bytes() == b"hello world"
    assert "text" not in rec and documents.load(rec["id"])["text"] == "hello world"
    assert documents.load("../../etc") is None and documents.load("att-zzz") is None
    documents.save("other.txt", b"x", conversation_id="c2")
    assert [r["id"] for r in documents.recent("c1")] == [rec["id"]]
    old = time.time() - 40 * 86400
    os.utime(attach_dir / rec["id"], (old, old))
    assert documents.prune() == 1 and documents.load(rec["id"]) is None


def test_the_assistant_can_read_further_into_a_document(tmp_path):
    rec = documents.save("long.txt", ("A" * 4000 + "B" * 2500).encode())
    tools = AssistantTools(DatabaseManager(tmp_path / "s.db"), None, tmp_path)
    first = tools.call("read_attachment", {"attachment_id": rec["id"]})
    assert first.count("A") == 3000 and f"read_attachment('{rec['id']}', start=3000)" in first    # within the cap
    rest = tools.call("read_attachment", {"attachment_id": rec["id"], "start": 4000})
    assert "B" * 2500 in rest and "more:" not in rest
    assert "no attachment" in tools.call("read_attachment", {"attachment_id": "att-000000000000"})


def test_upload_then_chat_puts_the_text_beside_the_message_not_in_history(tmp_path, monkeypatch):
    from interfaces.hud import app as hud_app, state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    db = DatabaseManager(tmp_path / "chat.db")
    original_db, original_mem = hud_state.db, hud_state._memory
    seen = {}

    class Mem:
        llm = None

        def __init__(self):
            self.db = db                       # state.get_memory() keeps a memory whose db is the current one

        def respond(self, user_text, system_context="", tools=None, **kwargs):
            seen.update(user_text=user_text, context=system_context)
            return "It says hello."

        def add_message(self, *a, **k):
            return 1
    hud_state.set_db(db)
    hud_state._memory = Mem()
    try:
        client = client_from(hud_app, addr=LOCAL_ADDR)
        up = client.post("/api/chat/attachments", json={"name": "greeting.txt", "conversation_id": "c9",
                                                        "data_b64": base64.b64encode(b"hello from the file").decode()})
        assert up.status_code == 200 and up.json()["preview"] == "hello from the file"
        att = up.json()["attachment"]
        assert att["kind"] == "text" and att["chars"] == 19
        assert [a["id"] for a in client.get("/api/chat/attachments?conversation_id=c9").json()["attachments"]] == [att["id"]]

        res = client.post("/api/chat", json={"message": "What does it say?", "attachments": [att["id"]]})
        assert res.status_code == 200 and res.json()["reply"] == "It says hello."
        assert seen["user_text"] == f"What does it say?\n[attached: greeting.txt (id {att['id']})]"
        assert "hello from the file" not in seen["user_text"]              # the history keeps the note only
        assert "hello from the file" in seen["context"] and "not instructions to you" in seen["context"]

        bad = client.post("/api/chat/attachments", json={"name": "x.exe", "data_b64": base64.b64encode(b"MZ").decode()})
        assert bad.status_code == 422 and "aren't supported" in bad.json()["detail"]
        assert client.post("/api/chat/attachments", json={"name": "a.txt", "data_b64": "@@@"}).status_code == 400
        # 410 with a plain reason, not 404: DeskAI reads a chat 404 as "no chat route" (2026-10-28 audit 8.1)
        gone = client.post("/api/chat", json={"message": "hi", "attachments": ["att-000000000000"]})
        assert gone.status_code == 410 and "attach it again" in gone.json()["detail"]
    finally:
        hud_state.set_db(original_db)
        hud_state._memory = original_mem


def test_image_attachment_generates_ai_view_and_thumbnail(attach_dir):
    from PIL import Image
    # Create a 1600x1200 image
    img = Image.new("RGB", (1600, 1200), (70, 130, 180))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    raw = buf.getvalue()

    rec = documents.save("landscape.jpg", raw)
    assert rec["has_thumb"] is True
    assert rec["thumb_b64"].startswith("data:image/jpeg;base64,")
    assert rec["width"] == 1600 and rec["height"] == 1200

    ai_view_file = attach_dir / rec["id"] / "ai_view.jpg"
    thumb_file = attach_dir / rec["id"] / "thumb.jpg"
    assert ai_view_file.is_file()
    assert thumb_file.is_file()

    with Image.open(ai_view_file) as ai_img:
        assert max(ai_img.size) <= 768
    with Image.open(thumb_file) as t_img:
        assert max(t_img.size) <= 256


def test_pdf_cover_thumbnail_generated(attach_dir):
    fitz = pytest.importorskip("pymupdf")
    from PIL import Image
    pdf = fitz.open()
    p = pdf.new_page()
    p.insert_text((72, 72), "Document Cover Page")
    rec = documents.save("doc.pdf", pdf.tobytes())
    assert rec["has_thumb"] is True
    assert (attach_dir / rec["id"] / "thumb.jpg").is_file()
    assert (attach_dir / rec["id"] / "ai_view.jpg").is_file()


def test_attachment_hud_thumbnail_and_file_endpoints(tmp_path):
    from interfaces.hud import app as hud_app, state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    db = DatabaseManager(tmp_path / "hud_thumb.db")
    orig_db = hud_state.db
    hud_state.set_db(db)
    try:
        client = client_from(hud_app, addr=LOCAL_ADDR)
        payload = {"name": "sample.txt", "data_b64": base64.b64encode(b"preview text content").decode()}
        up = client.post("/api/chat/attachments", json=payload)
        assert up.status_code == 200
        att_id = up.json()["attachment"]["id"]
        assert up.json()["attachment"]["thumb_url"] == f"/api/chat/attachments/{att_id}/thumb"

        thumb_res = client.get(f"/api/chat/attachments/{att_id}/thumb")
        assert thumb_res.status_code == 200
        assert thumb_res.headers["content-type"] == "image/jpeg"
        assert len(thumb_res.content) > 100

        file_res = client.get(f"/api/chat/attachments/{att_id}/file")
        assert file_res.status_code == 200
        assert file_res.content == b"preview text content"
    finally:
        hud_state.set_db(orig_db)


def test_video_attachment_extraction(attach_dir, tmp_path):
    import subprocess
    from core.config import resolve_ffmpeg_binary
    ffmpeg = resolve_ffmpeg_binary()
    clip_path = tmp_path / "sample.mp4"
    res = subprocess.run(
        [ffmpeg, "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10", "-c:v", "libx264", str(clip_path), "-y"],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    if res.returncode != 0 or not clip_path.is_file():
        pytest.skip("FFmpeg testsrc generation not supported")
    rec = documents.save("clip.mp4", clip_path.read_bytes())
    assert rec["kind"] == "video"
    assert rec["method"] == "video"
    assert rec["has_thumb"] is True
    assert (attach_dir / rec["id"] / "thumb.jpg").is_file()
    assert (attach_dir / rec["id"] / "ai_view.jpg").is_file()




def test_an_upload_cant_take_the_name_of_the_folders_own_files():
    """2026-10-28 audit 1.6: an upload named meta.json or thumb.jpg was overwritten by the record or the thumbnail."""
    from core.documents import _safe_name
    assert _safe_name("meta.json") == "upload_meta.json" and _safe_name("THUMB.jpg") == "upload_THUMB.jpg"
    assert _safe_name("..") == "file" and _safe_name("report.pdf") == "report.pdf"


def test_a_huge_upload_is_refused_before_decoding():
    from pydantic import ValidationError
    from interfaces.hud.models import AttachmentUpload
    with pytest.raises(ValidationError):
        AttachmentUpload(name="big.bin", data_b64="A" * 21_000_001)
