"""
Canvas: drafts written and refined together (core/canvas.py, docs/CANVAS.md)
===========================================================================
A fake writer stands in for the AI; the live style reference is replaced, so no test touches the network.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.environ.setdefault("CONNECTORS_DIR", str(Path(tempfile.gettempdir()) / "no_connectors_in_tests"))

from core import canvas  # noqa: E402
from core.assistant_tools import AssistantTools  # noqa: E402
from core.database import DatabaseManager  # noqa: E402


class Writer:
    """Returns a draft that depends on the request, and records what it was sent."""

    def __init__(self):
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        user = messages[-1]["content"]
        if "punchier" in user:
            return "<h2>Start here</h2><p>A punchy new opening that grabs you right away.</p>"
        return "```html\n<h2>Why it matters</h2><p>Grief is heavy, and a small card can help someone feel less alone.</p>" \
               "<script>alert(1)</script><p onclick=\"steal()\">Write from the heart.</p>\n```"


class Mem:
    def __init__(self, llm):
        self.llm = llm


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas, "_site_reference", lambda: '<h1>Example guide</h1><div class="inline-cta-block">x</div>')
    return DatabaseManager(tmp_path / "state.db")


def test_drafts_keep_every_version_and_revert_adds_one(db):
    c = canvas.Canvas(db, llm=Writer())
    doc = c.create("Grief cards guide", "document", "a 600-word guide on what to write in a grief card")
    assert doc["version"] == 1 and "Grief is heavy" in doc["html"]
    doc = c.edit(doc["id"], "make the intro punchier")
    assert doc["version"] == 2 and "punchy" in doc["html"]
    doc = c.revert(doc["id"], 1)
    assert doc["version"] == 3 and "Grief is heavy" in doc["html"]
    assert [v["version"] for v in doc["versions"]] == [1, 2, 3] and doc["versions"][2]["note"] == "back to version 1"


def test_the_writer_gets_only_the_draft_and_the_change(db):
    w = Writer()
    c = canvas.Canvas(db, llm=w)
    doc = c.create("Grief cards guide", "document", "a short guide")
    c.edit(doc["id"], "make it punchier")
    system, user = w.calls[-1][0]["content"], w.calls[-1][1]["content"]
    assert len(w.calls[-1]) == 2                                   # no conversation, no memory
    assert "Current draft (HTML)" in user and "make it punchier" in user and "Grief is heavy" in user


def test_nothing_executable_survives(db):
    doc = canvas.Canvas(db, llm=Writer()).create("Guide", "document", "a short guide")
    assert "<script" not in doc["html"] and "onclick" not in doc["html"] and "```" not in doc["html"]
    assert canvas._clean('<a href="javascript:evil()">x</a><iframe src="x"></iframe>') == '<a href="#">x</a>'


def test_the_chat_model_hears_an_outline_not_the_draft(db, tmp_path):
    tools = AssistantTools(db, Mem(Writer()), tmp_path, user_text="draft a guide about grief cards")
    out = tools.call("canvas_write", {"title": "Grief cards guide", "kind": "document",
                                      "instruction": "a guide on what to write in a grief card"})
    assert out.startswith('Canvas #1 "Grief cards guide" (document), version 1 of 1:') and "sections: Why it matters" in out
    assert "Grief is heavy" not in out and "don't paste the draft" in out
    assert tools.canvas_shown == {"id": 1, "title": "Grief cards guide", "kind": "document", "version": 1}


def test_page_drafts_follow_the_site_and_preview_with_its_styles(db):
    w = Writer()
    c = canvas.Canvas(db, llm=w)
    doc = c.create("New guide page", "page", "a guide page about thinking-of-you messages")
    assert "inline-cta-block" in w.calls[0][0]["content"]          # the site's own structure as a reference
    page = c.render(doc["id"])
    assert f'{canvas.SITE}/css/styles.css' in page and '<main class="blog-post">' in page and "<script" not in page
    assert "not published" in page


def test_the_preview_blocks_scripts_and_is_sandboxed(db):
    from interfaces.hud import app as hud_app
    from interfaces.hud import state as hud_state
    from tests._hud_client import LOCAL_ADDR, client_from
    doc = canvas.Canvas(db, llm=Writer()).create("Guide", "document", "a short guide")
    original = hud_state.db
    hud_state.set_db(db)
    try:
        client = client_from(hud_app, addr=LOCAL_ADDR)
        r = client.get(f"/api/canvas/{doc['id']}/preview")
        assert r.status_code == 200 and "Grief is heavy" in r.text
        csp = r.headers["content-security-policy"]
        assert "script-src 'none'" in csp and "sandbox" in csp and "default-src 'none'" in csp
        meta = client.get(f"/api/canvas/{doc['id']}").json()
        assert meta["latest"] == 1 and "html" not in meta
        assert client.get("/api/canvas").json()["canvases"][0]["title"] == "Guide"
        assert client.get("/api/canvas/99/preview").status_code == 404
    finally:
        hud_state.set_db(original)


def test_publishing_needs_the_users_words_and_goes_to_agentwork(db, tmp_path, monkeypatch):
    w = Writer()
    doc = canvas.Canvas(db, llm=w).create("Grief cards guide", "document", "a short guide")
    tools = AssistantTools(db, Mem(w), tmp_path, user_text="looks great")
    monkeypatch.setattr(AssistantTools, "agentwork_projects", lambda self: [{"name": "website"}])
    assert "needs the user's own words" in tools.call("canvas_publish", {"canvas_id": doc["id"], "project": "website"})
    tools = AssistantTools(db, Mem(w), tmp_path, user_text="publish it to the site")
    monkeypatch.setattr(AssistantTools, "agentwork_projects", lambda self: [{"name": "website"}])
    out = tools.call("canvas_publish", {"canvas_id": doc["id"], "project": "website", "notes": "a new guide in blog/"})
    assert out.startswith("Started task-")
    task = db.get_task(tools.started[0])
    assert task["engine"] == "agentwork" and "Grief is heavy" in task["prompt"] and "a new guide in blog/" in task["prompt"]


def test_a_drafting_request_routes_to_the_canvas_tools(db, tmp_path):
    tools = AssistantTools(db, None, tmp_path, user_text="draft this week's guide about grief cards")
    tools.tool_specs()
    assert "canvas" in tools.groups
