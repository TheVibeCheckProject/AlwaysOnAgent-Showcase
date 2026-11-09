"""
Image notes: a scene note per attached image from the local vision model, the chat's honesty about images, and
the facts distilled from every 5 notes. The vision model and the memory's LLM are stand-ins.
"""

import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real tools or models
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))
os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"          # nothing listens there

from core import documents, vision
from core.assistant_tools import AssistantTools
from core.database import DatabaseManager


@pytest.fixture(autouse=True)
def attach_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "ATTACH_DIR", tmp_path / "attachments")
    monkeypatch.setattr(documents, "ocr_image", lambda image: ("08:16:47PM 11°F", 72.0))
    return tmp_path / "attachments"


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "gray").save(buf, "PNG")
    return buf.getvalue()


class Memory:
    """Records events and facts; its LLM returns what the test sets."""

    def __init__(self, db=None, answer="{}"):
        self.db, self.llm, self.answer = db, object(), answer
        self.events, self.facts, self.prompts = [], [], []

    def record_event(self, text):
        self.events.append(text)

    def _bg_chat(self, messages, purpose, **kw):
        self.prompts.append((purpose, messages[-1]["content"]))
        return self.answer

    def _grounded(self, evidence, text):          # the real check, from MemoryManager
        from core.memory import MemoryManager
        return MemoryManager._grounded(self, evidence, text)

    def _norm(self, text):
        from core.memory import MemoryManager
        return MemoryManager._norm(text)

    def add_fact(self, content, category="fact", importance=3, source="chat"):
        self.facts.append((content, category, importance, source))
        return len(self.facts), True


def test_an_unreachable_model_is_recorded_not_raised():
    rec = documents.save("cam.png", _png())
    out = vision.note_image(rec["id"])
    assert "isn't reachable" in out["scene_error"] and not out.get("scene")


def test_each_image_gets_a_scene_note_with_the_ocr_text_as_a_hint(monkeypatch):
    sent = {}

    def fake_generate(prompt, image_b64):
        sent.update(prompt=prompt, image=image_b64)
        return "A night trail-camera photo of a large fox. The banner reads 08:16:47 PM 09/28/2026 71°F."
    monkeypatch.setattr(vision, "_generate", fake_generate)
    mem = Memory()
    rec = documents.save("cam.png", _png())
    out = vision.note_image(rec["id"], mem)
    assert out["scene"].startswith("A night trail-camera photo") and out["scene_model"] == vision.model_name()
    assert "08:16:47PM 11°F" in sent["prompt"] and "trust what you see" in sent["prompt"] and sent["image"]
    assert mem.events and mem.events[0].startswith("The user shared an image, cam.png")
    # a document isn't an image: nothing to note
    txt = documents.save("a.txt", b"hello")
    assert "scene" not in vision.note_image(txt["id"], mem)


def test_the_chat_is_honest_about_images_it_cant_see(monkeypatch):
    rec = documents.load(documents.save("cam.png", _png())["id"])
    pending = documents.chat_context([rec])
    assert "You can't see this image" in pending and "never guess" in pending and "Text read by OCR" in pending
    documents.update(rec["id"], scene_error="the local vision model isn't reachable")
    assert "say so plainly instead of guessing" in documents.chat_context([documents.load(rec["id"])])
    documents.update(rec["id"], scene="A fox at night.", scene_model="gemma3:4b")
    ready = documents.chat_context([documents.load(rec["id"])])
    assert "A fox at night." in ready and "can be wrong about small details" in ready
    tools = AssistantTools(DatabaseManager(Path(tempfile.mkdtemp()) / "s.db"), None, Path("."))
    assert "A fox at night." in tools.call("read_attachment", {"attachment_id": rec["id"]})


def test_a_turn_waits_for_a_scene_note_that_is_on_its_way():
    rec = documents.save("cam.png", _png())
    threading.Timer(0.5, lambda: documents.update(rec["id"], scene="A fox.")).start()
    t = time.time()
    assert vision.wait_for_scene(rec["id"], timeout=5)["scene"] == "A fox." and time.time() - t < 3
    other = documents.save("b.png", _png())
    assert not vision.wait_for_scene(other["id"], timeout=0.2).get("scene")          # gives up after the timeout


def test_notes_wait_while_a_video_renders(monkeypatch):
    class Db:
        calls = 0

        def list_tasks(self, limit=3, phase=None):
            Db.calls += 1
            return [{"engine": "media_pipeline"}] if Db.calls < 3 else []
    naps = []
    monkeypatch.setattr(vision.time, "sleep", naps.append)
    monkeypatch.setattr(vision, "_generate", lambda p, i: "A fox.")
    rec = documents.save("cam.png", _png())
    assert vision.note_image(rec["id"], Memory(db=Db()))["scene"] == "A fox." and naps == [30, 30]


def test_every_five_notes_the_grounded_facts_go_into_memory(monkeypatch):
    monkeypatch.setattr(vision, "_generate", lambda p, i: "A night trail-camera photo from a trail cam: a fox "
                                                         "walks past a bird feeder hanging on a tree.")
    answer = json.dumps({"facts": [
        {"content": "The user has a trail camera watching a bird feeder", "category": "fact",
         "importance": 3, "evidence": "trail cam: a fox walks past a bird feeder"},
        {"content": "The user skis in Colorado", "category": "fact", "importance": 4,
         "evidence": "skiing in the Rocky Mountains"}]})              # not in the notes: thrown out
    mem = Memory(answer=answer)
    ids = [documents.save(f"cam{i}.png", _png())["id"] for i in range(5)]
    for attachment_id in ids[:4]:
        vision.note_image(attachment_id, mem)
    assert mem.prompts == []                                             # not due before the 5th
    vision.note_image(ids[4], mem)
    assert [p for p, _ in mem.prompts] == ["image_memory"] and all(i in mem.prompts[0][1] for i in ids)
    assert mem.facts == [("The user has a trail camera watching a bird feeder", "fact", 3, "images")]
    assert all(documents.load(i).get("distilled") for i in ids)
    assert vision.pending_for_distill() == [] and vision.distill_if_due(mem) is None     # the batch is used once


def test_the_lead_in_is_dropped():
    assert vision._tidy("Here's a description of the image:\n\nA fox at night.") == "A fox at night."
    assert vision._tidy("A fox at night.\nHere's more:") == "A fox at night.\nHere's more:"
    assert vision._tidy("Okay, here’s a description of the image, as if I were telling a friend what I see:\n\n"
                        "A forest floor.") == "A forest floor."


def test_text_the_ocr_didnt_find_is_checked_and_dropped_when_there_is_none(monkeypatch, tmp_path):
    asked = []

    def fake_generate(prompt, image_b64):
        asked.append(prompt)
        if prompt == vision.VERIFY_TEXT:
            return "No."
        return ("A wooded area with tall trees. The ground is covered in leaves. There are markings on the trunks, "
                "but they are too faint to read. I can see a small sign that says \"No Trespassing.\"")
    monkeypatch.setattr(vision, "_generate", fake_generate)
    img = tmp_path / "forest.png"
    img.write_bytes(_png())
    note = vision.describe(img, "")
    assert len(asked) == 2 and "Trespassing" not in note
    assert note == ("A wooded area with tall trees. The ground is covered in leaves. There are markings on the "
                    "trunks, but they are too faint to read.")


def test_real_text_is_kept(monkeypatch, tmp_path):
    img = tmp_path / "sign.png"
    img.write_bytes(_png())
    # the model confirms it: kept
    monkeypatch.setattr(vision, "_generate", lambda p, i: "Yes: a sign at the top says OPEN." if p == vision.VERIFY_TEXT
                        else "A shop door. The sign says \"OPEN\".")
    assert vision.describe(img, "") == "A shop door. The sign says \"OPEN\"."
    # the OCR found text too: no second question
    asked = []
    monkeypatch.setattr(vision, "_generate", lambda p, i: asked.append(p) or "A shop door. The sign says \"OPEN\".")
    assert "OPEN" in vision.describe(img, "OPEN") and len(asked) == 1


def test_ocr_text_isnt_evidence_and_a_failed_batch_is_kept(monkeypatch):
    """2026-10-28 audit 7.5: a screenshot's OCR text (other people's words) could ground a fact about the owner, and
    a batch was marked distilled even when the AI gave no answer, so those images were never read together."""
    monkeypatch.setattr(vision, "_generate", lambda p, i: "A screenshot of a web page with text.")
    monkeypatch.setattr(documents, "ocr_image", lambda image: ("Jane Smith lives in Dallas and owns a boat", 90.0))
    mem = Memory(answer=None)                                             # the AI didn't answer
    mem.subject = lambda: "Alex"
    ids = [documents.save(f"shot{i}.png", _png())["id"] for i in range(5)]
    for attachment_id in ids:
        vision.note_image(attachment_id, mem)
    assert len(vision.pending_for_distill()) == 5                         # kept for the next try
    mem.answer = json.dumps({"facts": [{"content": "Alex owns a boat", "category": "fact", "importance": 3,
                                        "evidence": "owns a boat"}]})        # quoted from the OCR text only
    assert vision.distill_if_due(mem) == {"added": 0, "rejected": 1}
    assert mem.facts == [] and vision.pending_for_distill() == []
    assert "about Alex, by name" in str(vision.DISTILL_PROMPT.format(n=3, subject="Alex"))
