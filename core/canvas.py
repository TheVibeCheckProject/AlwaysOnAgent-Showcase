"""
Canvas: drafts the owner and the agent work on together
=======================================================
A canvas is a draft with every version kept: a **document** (a guide, script, caption set, plan, email) or a
**page** (a website page or change, previewed with example-site.com's own styles). Content is always an
HTML fragment, the format the site uses, so previews and publishing need no conversion.

Token budget: the chat model never carries a whole draft. It sends a short instruction; a dedicated writing call
here gets only the draft and that instruction (no conversation, no memory) and the chat gets a one-line summary
back. Previews are rendered here and shown by DeskAI from GET /api/canvas/{id}/preview, with scripts blocked.
Design: docs/CANVAS.md.
"""

import html as html_lib
import logging
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional

try:
    from core.database import get_iso_now
    from core.llm_usage import usage_purpose
except ImportError:
    from database import get_iso_now
    from llm_usage import usage_purpose

logger = logging.getLogger("Canvas")

KINDS = ("document", "page")
MAX_CHARS = 60000
SITE = "https://example-site.com"
STYLE_REFERENCE_URL = f"{SITE}/blog/daily-affirmations-for-anxiety.html"   # a real guide, for page drafts
_style_reference = {"html": "", "at": 0.0}

# Shown on the preview response: nothing but the page itself may load, and no script may run
PREVIEW_CSP = ("default-src 'none'; script-src 'none'; style-src 'unsafe-inline' " + SITE +
               " https://fonts.googleapis.com; font-src https://fonts.gstatic.com data:; img-src https: data:; "
               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox")

WRITER_RULES = (
    "Write clean semantic HTML only: h2/h3, p, ul/ol/li, blockquote, strong/em, a. No <html>, <head>, <body>, "
    "<style>, <script>, inline event handlers or markdown. Plain, warm English. Return ONLY the HTML fragment.")
PAGE_RULES = (
    "This is a page for example-site.com (anonymous affirmation cards). Write the content of its "
    "<main class=\"blog-post\"> the way the site's guides are written: an h1 title, a short intro, h2 sections, "
    "and the site's own classes where they fit (e.g. inline-cta-block for a call to send a card). Here is part "
    "of a real guide as a style reference:\n")


class CanvasError(Exception):
    """Why a canvas action can't be done; written for the owner."""


def _clean(fragment: str) -> str:
    """The writer's HTML minus anything executable (the preview also blocks scripts: belt and braces)."""
    s = re.sub(r"^```[a-z]*\s*|\s*```$", "", (fragment or "").strip(), flags=re.I)
    s = re.sub(r"(?is)<(script|style|iframe|object|embed)\b.*?</\1\s*>", "", s)
    s = re.sub(r"(?is)<(script|style|iframe|object|embed|link|meta)\b[^>]*>", "", s)
    s = re.sub(r"(?i)\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", s)
    s = re.sub(r"(?i)(href|src)\s*=\s*([\"'])\s*javascript:[^\"']*\2", r'\1="#"', s)
    return s.strip()[:MAX_CHARS]


def outline(fragment: str) -> str:
    """Words and section titles: what the chat model hears about a draft instead of the draft itself."""
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    words = len(re.findall(r"\w+", html_lib.unescape(text)))
    heads = [html_lib.unescape(re.sub(r"<[^>]+>", "", h)).strip()
             for h in re.findall(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", fragment or "")]
    heads = [h for h in heads if h][:8]
    return f"{words} words" + (f"; sections: {', '.join(heads)}" if heads else "")


def _site_reference() -> str:
    """Part of a live guide's <main> (cached for a day), so page drafts follow the site's structure."""
    if _style_reference["html"] and time.time() - _style_reference["at"] < 86400:
        return _style_reference["html"]
    try:
        req = urllib.request.Request(STYLE_REFERENCE_URL, headers={"User-Agent": "AlwaysOnAgent canvas"})
        with urllib.request.urlopen(req, timeout=10) as r:
            page = r.read().decode("utf-8", "replace")
        m = re.search(r'(?is)<main class="blog-post">(.*?)</main>', page)
        ref = re.sub(r"(?is)<script\b.*?</script>", "", m.group(1) if m else "")
        _style_reference.update(html=re.sub(r"\s+", " ", ref)[:3500], at=time.time())
    except Exception as e:
        logger.debug(f"style reference unavailable: {e}")
    return _style_reference["html"]


class Canvas:
    def __init__(self, db, llm=None):
        self.db, self.llm = db, llm
        with db.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS canvas_docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, kind TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS canvas_versions (
                    doc_id INTEGER NOT NULL, version INTEGER NOT NULL, html TEXT NOT NULL, note TEXT,
                    created_at TEXT NOT NULL, PRIMARY KEY (doc_id, version));
            """)

    # ------------------------------------------------------------------------------------------ reading
    def get(self, doc_id: int, version: Optional[int] = None) -> Dict[str, Any]:
        conn = self.db.get_connection()
        doc = conn.execute("SELECT * FROM canvas_docs WHERE id = ?", (int(doc_id),)).fetchone()
        if not doc:
            raise CanvasError(f"there's no canvas #{doc_id}")
        v = int(version or doc["version"])
        row = conn.execute("SELECT * FROM canvas_versions WHERE doc_id = ? AND version = ?", (doc["id"], v)).fetchone()
        if not row:
            raise CanvasError(f"canvas #{doc_id} has no version {v}")
        versions = [{"version": r["version"], "note": r["note"], "created_at": r["created_at"]} for r in conn.execute(
            "SELECT version, note, created_at FROM canvas_versions WHERE doc_id = ? ORDER BY version", (doc["id"],))]
        return {"id": doc["id"], "title": doc["title"], "kind": doc["kind"], "version": v, "latest": doc["version"],
                "html": row["html"], "versions": versions, "created_at": doc["created_at"],
                "updated_at": doc["updated_at"]}

    def list(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.db.get_connection().execute(
            "SELECT id, title, kind, version, updated_at FROM canvas_docs ORDER BY updated_at DESC LIMIT ?",
            (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def summary(self, doc: Dict[str, Any]) -> str:
        return (f"Canvas #{doc['id']} \"{doc['title']}\" ({doc['kind']}), version {doc['version']} of "
                f"{doc['latest']}: {outline(doc['html'])}")

    # ------------------------------------------------------------------------------------------ writing
    def _write(self, kind: str, title: str, instruction: str, current: str = "") -> str:
        if self.llm is None:
            raise CanvasError("no writing model is available right now")
        system = ("You write drafts for the owner of example-site.com, in a canvas they refine with you. "
                  + WRITER_RULES)
        if kind == "page":
            ref = _site_reference()
            system += "\n\n" + PAGE_RULES + (ref or "(the reference is unavailable: use h1, p and h2 sections)")
        else:
            system += " The title is shown above the draft: don't repeat it as a heading."
        user = (f"Title: {title}\n\nCurrent draft (HTML):\n{current}\n\nChange it like this: {instruction}"
                if current else f"Title: {title}\n\nWrite it: {instruction}")
        with usage_purpose("canvas"):
            out = self.llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                max_tokens=3500, temperature=0.6, timeout=90)
        fragment = _clean(out or "")
        if len(re.sub(r"<[^>]+>", "", fragment).strip()) < 20:
            raise CanvasError("the writing model returned nothing usable; try again")
        return fragment

    def _save_version(self, doc_id: int, fragment: str, note: str) -> int:
        conn = self.db.get_connection()
        with conn:
            v = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM canvas_versions WHERE doc_id = ?",
                             (doc_id,)).fetchone()[0]
            now = get_iso_now()
            conn.execute("INSERT INTO canvas_versions (doc_id, version, html, note, created_at) VALUES (?, ?, ?, ?, ?)",
                         (doc_id, v, fragment, note[:200], now))
            conn.execute("UPDATE canvas_docs SET version = ?, updated_at = ? WHERE id = ?", (v, now, doc_id))
        return v

    def create(self, title: str, kind: str, instruction: str) -> Dict[str, Any]:
        title, kind = (title or "").strip()[:120], (kind or "document").strip().lower()
        if kind not in KINDS:
            raise CanvasError(f"kind must be one of {', '.join(KINDS)}")
        if not title or len((instruction or "").strip()) < 5:
            raise CanvasError("a canvas needs a title and a description of what to write")
        fragment = self._write(kind, title, instruction)
        now = get_iso_now()
        with self.db.get_connection() as conn:
            doc_id = conn.execute("INSERT INTO canvas_docs (title, kind, version, created_at, updated_at) "
                                  "VALUES (?, ?, 0, ?, ?)", (title, kind, now, now)).lastrowid
        self._save_version(doc_id, fragment, f"first draft: {instruction.strip()}")
        return self.get(doc_id)

    def create_from(self, title: str, kind: str, fragment: str, note: str) -> Dict[str, Any]:
        """A document written elsewhere (the weekly review): saved as is, cleaned like the writer's output, and
        refined in chat like any other draft."""
        title, kind = (title or "").strip()[:120], (kind or "document").strip().lower()
        fragment = _clean(fragment)
        if kind not in KINDS or not title or len(re.sub(r"<[^>]+>", "", fragment).strip()) < 20:
            raise CanvasError("a canvas needs a kind, a title and some content")
        now = get_iso_now()
        with self.db.get_connection() as conn:
            doc_id = conn.execute("INSERT INTO canvas_docs (title, kind, version, created_at, updated_at) "
                                  "VALUES (?, ?, 0, ?, ?)", (title, kind, now, now)).lastrowid
        self._save_version(doc_id, fragment, note)
        return self.get(doc_id)

    def edit(self, doc_id: int, instruction: str) -> Dict[str, Any]:
        doc = self.get(doc_id)
        if len((instruction or "").strip()) < 3:
            raise CanvasError("say what to change")
        self._save_version(doc["id"], self._write(doc["kind"], doc["title"], instruction, doc["html"]),
                           instruction.strip())
        return self.get(doc["id"])

    def revert(self, doc_id: int, version: int) -> Dict[str, Any]:
        """Makes an earlier version the newest again (history only grows, so nothing is lost)."""
        old = self.get(doc_id, version)
        self._save_version(old["id"], old["html"], f"back to version {old['version']}")
        return self.get(old["id"])

    # ------------------------------------------------------------------------------------------ rendering
    def render(self, doc_id: int, version: Optional[int] = None) -> str:
        doc = self.get(doc_id, version)
        title = html_lib.escape(doc["title"])
        if doc["kind"] == "page":
            return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                    f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{title}</title>"
                    f"<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&"
                    f"family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=Fredoka:wght@400;600&display=swap\">"
                    f"<link rel=\"stylesheet\" href=\"{SITE}/css/styles.css\">"
                    f"<link rel=\"stylesheet\" href=\"{SITE}/css/article.css\"></head><body>"
                    f"<nav id=\"nav\"><div class=\"nav-container\"><div class=\"nav-logo\">Example Site</div>"
                    f"</div></nav><main class=\"blog-post\">{doc['html']}</main>"
                    f"<footer class=\"footer\"><div class=\"footer-container\"><div class=\"footer-bottom\">"
                    f"Preview of a draft · not published</div></div></footer></body></html>")
        return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{title}</title>"
                "<style>:root{color-scheme:light dark;--bg:#fbfaf7;--fg:#1f2328;--muted:#6b7280;--accent:#7c3aed}"
                "@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e7ea;--muted:#9aa0a6;--accent:#a78bfa}}"
                "body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.65 Georgia,'Times New Roman',serif}"
                "main{max-width:680px;margin:0 auto;padding:28px 18px 64px}h1,h2,h3{font-family:system-ui,sans-serif;"
                "line-height:1.25}h1{font-size:1.7em}h2{font-size:1.3em;margin-top:1.8em}a{color:var(--accent)}"
                "blockquote{margin:1em 0;padding:.2em 1em;border-left:3px solid var(--accent);color:var(--muted)}"
                ".meta{font:13px system-ui,sans-serif;color:var(--muted)}</style></head><body><main>"
                f"<p class=\"meta\">Draft · version {doc['version']} of {doc['latest']}</p><h1>{title}</h1>"
                f"{doc['html']}</main></body></html>")
