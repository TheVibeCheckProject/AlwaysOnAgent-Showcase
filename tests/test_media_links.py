"""
Media without the token in the URL (2026-10-07)
================================================
Older DeskAI builds put the HUD token on every image/video URL (?token=...), so it landed in logs, the chat
history and shared links. The app now sends it in a header; ?token= keeps working for old builds; a link that
must leave the app is a short-lived signed one (POST /api/media/link); and logs hide token/sig values.
"""

import logging
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import LogRedactionFilter, redact_url_tokens  # noqa: E402
from interfaces.hud import auth, state  # noqa: E402
from interfaces.hud.server import app  # noqa: E402
from tests._hud_client import LAN_ADDR, client_from  # noqa: E402

TOKEN = "test-hud-token-" + "x" * 24


class _Token:
    """Sets HUD_AUTH_TOKEN for one test and puts a small 'video' in the served folder."""

    def __init__(self, token=TOKEN):
        self.token = token

    def __enter__(self):
        self.saved = state.HUD_AUTH_TOKEN
        state.HUD_AUTH_TOKEN = self.token
        self.name = f"link_test_{uuid.uuid4().hex[:8]}.mp4"
        self.path = state.VIDEOS_DIR / self.name
        self.path.write_bytes(b"\x00" * 64)
        return self

    def __exit__(self, *exc):
        state.HUD_AUTH_TOKEN = self.saved
        try:
            self.path.unlink()
        except OSError:
            pass


def _lan():
    return client_from(app, LAN_ADDR)


def test_media_loads_with_the_token_in_a_header_and_old_query_token_still_works():
    with _Token() as t:
        c = _lan()
        url = f"/videos/{t.name}"
        assert c.get(url).status_code == 401
        assert c.get(url, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        assert c.get(url, headers={"X-HUD-Token": TOKEN}).status_code == 200
        assert c.get(f"{url}?token={TOKEN}").status_code == 200          # older DeskAI builds keep working
        assert c.get(f"{url}?token=wrong").status_code == 401
    print("  - media loads with the header; ?token= still accepted for older app builds")


def test_signed_media_link_works_without_the_token_and_only_for_that_file():
    with _Token() as t:
        c = _lan()
        r = c.post("/api/media/link", json={"url": f"http://192.168.1.2:8080/videos/{t.name}?token={TOKEN}",
                                             "ttl_seconds": 3600}, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["signed"] is True and TOKEN not in body["url"] and "token=" not in body["url"]
        link = body["url"]
        assert link.startswith(f"/videos/{t.name}?exp=") and "&sig=" in link
        assert c.get(link).status_code == 200                              # no token needed
        assert c.head(link).status_code in (200, 405)                      # HEAD allowed by auth (static may 405)
        # Tampered, moved to another file, or used for a write: refused
        assert c.get(link[:-1] + ("0" if link[-1] != "0" else "1")).status_code == 401
        other = link.replace(t.name, "someone_else.mp4")
        assert c.get(other).status_code == 401
        assert c.post(link).status_code == 401
        # The same signature can't open an API route
        q = link.split("?", 1)[1]
        assert c.get(f"/api/memory?{q}").status_code == 401
        # Needs the token itself
        assert c.post("/api/media/link", json={"url": f"/videos/{t.name}"}).status_code == 401
    print("  - a signed link opens that one file without the token, and nothing else")


def test_signed_link_expires_and_dies_when_the_token_changes():
    with _Token() as t:
        c = _lan()
        past = time.time() - 2 * 3600
        url, _ = auth.sign_media_link(f"/videos/{t.name}", ttl_seconds=3600, now=past)
        assert c.get(url).status_code == 401                               # expired an hour ago
        fresh, _ = auth.sign_media_link(f"/videos/{t.name}", ttl_seconds=3600)
        assert c.get(fresh).status_code == 200
        state.HUD_AUTH_TOKEN = TOKEN + "-rotated"
        assert c.get(fresh).status_code == 401                             # a new token voids old links
    print("  - links expire, and changing the token voids them")


def test_only_media_paths_can_be_signed():
    with _Token():
        c = _lan()
        h = {"Authorization": f"Bearer {TOKEN}"}
        for bad in ("/api/memory", "/videos/../.env", "/videos/a/b.mp4", "/images/%2e%2e/x", "https://x.com/"):
            assert c.post("/api/media/link", json={"url": bad}, headers=h).status_code == 400, bad
        for good in ("/images/gen_1.jpg", "/api/videos/a.mp4/thumbnail", "/api/tasks/abc123/thumb"):
            r = c.post("/api/media/link", json={"url": good}, headers=h)
            assert r.status_code == 200 and r.json()["signed"] is True, good
    print("  - only single media files can be signed")


def test_without_a_token_configured_the_link_is_the_plain_path():
    with _Token(token="") as t:
        r = client_from(app).post("/api/media/link", json={"url": f"/videos/{t.name}"})    # loopback
        assert r.status_code == 200 and r.json() == {"url": f"/videos/{t.name}", "expires_at": None, "signed": False}
        assert auth.media_link_valid is not None


def test_logs_hide_tokens_and_signatures_in_urls():
    line = f'1.2.3.4 - "GET /videos/a.mp4?token={TOKEN}&x=1 HTTP/1.1" 200; /images/b.jpg?exp=9&sig=abcdef'
    clean = redact_url_tokens(line)
    assert TOKEN not in clean and "abcdef" not in clean
    assert "token=[REDACTED]&x=1" in clean and "sig=[REDACTED]" in clean and "exp=9" in clean

    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4", "GET", f"/images/b.jpg?token={TOKEN}", "1.1", 200), None)
    LogRedactionFilter().filter(record)
    assert TOKEN not in record.getMessage() and "token=[REDACTED]" in record.getMessage()

    # The server installs the filter on uvicorn's loggers (the access log prints full URLs)
    for name in ("uvicorn.access", "uvicorn.error"):
        assert any(isinstance(f, LogRedactionFilter) for f in logging.getLogger(name).filters), name
    print("  - token=/sig= values never reach a log line")
