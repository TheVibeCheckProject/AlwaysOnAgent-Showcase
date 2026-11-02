"""Shared test setup. pytest loads this before any test file, so it holds for every test, in any order.

core.config copies .env into os.environ when first imported, OVERRIDING what's already there; get_config() then
prefers its own copy of .env; and it computes the companion tools' folders once, at import. So the test values are
set before it's imported (the folders are computed from them), then removed from its copy of .env and set again
(so .env can't win for any of them, whichever test imports what first).

Why (2026-10-04): the values used to be set with setdefault before that import, so a key also in .env lost; and
the API module, which opens its database at import, was bound to the LIVE data/state.db whenever a test file
imported it before setting STATE_DB_PATH. Every test run on the Mini wiped memory's vectors and wrote the test
search model's name into the live database (the agent re-embedded all memory on each start), and /image tests
wrote fake prompts and log lines there. A first fix that imported core.config before setting the folders let
three tests reach the real ImageGen, SocialPostEngine and VibeStats on the laptop.
"""
import gc
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="aoa_tests_"))
LIVE_DB = Path(__file__).resolve().parent.parent / "data" / "state.db"

TEST_ENV = {
    # Never the live database: the API module and anything else that opens "the" database get a scratch one
    "STATE_DB_PATH": str(_TMP / "state.db"),
    # Memory search defaults to Qwen through the Mini's Ollama; tests use the in-process bge-small so they never
    # depend on (or start) Ollama
    "MEMORY_EMBED_MODEL": "bge",
    "MEMORY_LOCAL_MODE": "off",              # tests never call the local model (IDEAS #10)
}
# The companion tools point at folders where they aren't installed, so no test can render or post a video, make
# an image, post pins or push the growth tracker
for _key, _name in (("TIKTOK_VIDEOS_DIR", "tiktokvideos"), ("IMAGEGEN_DIR", "imagegen"),
                    ("SOCIALPOSTENGINE_DIR", "socialpostengine"), ("VIBESTATS_DIR", "vibestats"),
                    ("CONNECTORS_DIR", "connectors"), ("AGENT_GALLERY_DIR", "agentgallery"),
                    ("COMPUTER_USE_DIR", "agentcomputeruse")):          # it moves the real mouse
    TEST_ENV[_key] = str(Path(tempfile.gettempdir()) / f"no_{_name}_in_tests")

os.environ.update(TEST_ENV)                  # before core.config computes the folders from them
import core.config  # noqa: E402
for _key, _value in TEST_ENV.items():        # .env may name some of them: it mustn't win
    core.config._ENV_VALUES.pop(_key, None)
    os.environ[_key] = _value
for _const, _key in (("TIKTOK_VIDEOS_DIR", "TIKTOK_VIDEOS_DIR"), ("IMAGEGEN_DIR", "IMAGEGEN_DIR"),
                     ("SOCIALPOSTENGINE_DIR", "SOCIALPOSTENGINE_DIR"), ("VIBESTATS_DIR", "VIBESTATS_DIR"),
                     ("CONNECTORS_DIR", "CONNECTORS_DIR"), ("AGENT_GALLERY_DIR", "AGENT_GALLERY_DIR"),
                     ("COMPUTER_USE_DIR", "COMPUTER_USE_DIR")):
    assert Path(getattr(core.config, _const)) == Path(TEST_ENV[_key]), f"{_const} still points at the real tool"
# No test can reach a paid or rate-limited service with the real keys in .env: AI models, web search, the health
# ping. 2026-10-28 audit 5.1: two chat tests called real Gemini through the google-genai SDK (httpx, which the
# urllib guard below didn't see), at every deploy on the Mini. HUD_AUTH_TOKEN stays: the API tests sign in with it.
_REAL_SERVICE_KEY = re.compile(r"KEY|TOKEN|SECRET|PASS|WEBHOOK|CREDENTIAL|^HEALTHCHECK_URL$|^LAPTOP_TAKEOVER_URL$|"
                               r"^CLOUDFLARE_ACCOUNT_ID$", re.I)
for _key in {*core.config._ENV_VALUES, *os.environ}:
    if _key != "HUD_AUTH_TOKEN" and _key not in TEST_ENV and _REAL_SERVICE_KEY.search(_key):
        core.config._ENV_VALUES.pop(_key, None)
        os.environ.pop(_key, None)
core.config.VIDEOS_DIR = core.config.TIKTOK_VIDEOS_DIR / "videos"
core.config.IMAGES_DIR = core.config.AGENT_GALLERY_DIR / "images"
# Task thumbnails (core/task_thumbs.py) default to the live data/task_thumbs; tests use a scratch folder
core.config.TASK_THUMBS_DIR = _TMP / "task_thumbs"


def _live_fingerprint():
    """What a test run must never change in the live database."""
    if not LIVE_DB.exists():
        return None
    c = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    try:
        return tuple(c.execute(q).fetchone() for q in (
            "SELECT MAX(updated_at), COUNT(*) FROM system_state",
            "SELECT COUNT(*), MAX(id) FROM system_logs",
            "SELECT COUNT(*), MAX(id) FROM mem_messages",
            "SELECT COUNT(*), SUM(embedding IS NULL) FROM mem_facts",
            "SELECT COUNT(*) FROM tasks"))
    except sqlite3.Error:
        return None
    finally:
        c.close()


@pytest.fixture(scope="session", autouse=True)
def live_database_untouched():
    """Fails the run if any test wrote to the live database. Skipped while the agent itself is running on this
    machine (it writes there all the time; on the Mini the deploy runs the tests while it's up)."""
    before = _live_fingerprint()
    yield
    after = _live_fingerprint()
    if before is not None and after != before and not _agent_running():
        pytest.fail(f"a test wrote to the live database {LIVE_DB}: {before} -> {after}", pytrace=False)


# The scratch folders test files make with tempfile.mkdtemp(prefix=...) at import, and conftest's own
SCRATCH_PREFIXES = ("aoa_tests_", "worker_test_", "tools_test_", "backup_test_", "memory_test_", "memory_recall_",
                    "robustness_test_", "media_delivery_test_", "agentwork_link_test_")
SCRATCH_STALE_SECONDS = 3600     # older than any run: safe to remove even while another run is going


def _remove_tree(folder: Path) -> None:
    def writable_then_retry(func, path, _exc):
        try:
            os.chmod(path, 0o700)                # git's object files are read-only on Windows
            func(path)
        except OSError:
            pass                                 # still open in another thread: the next run's sweep gets it
    shutil.rmtree(folder, onexc=writable_then_retry)


@pytest.fixture(scope="session", autouse=True)
def remove_scratch_folders():
    """Deletes the scratch folders test files make at import (TEST_DIR) and conftest's own. Their cleanup used to sit
    in the files' `if __name__ == "__main__":` blocks, which pytest never runs: 1,724 folders, 324 MB, had piled up
    on the Mini by 2026-10-28 (audit 5.2). Each parallel worker removes its own at the end; a database still open in
    another thread can't be deleted on Windows, so each run also sweeps stale ones left by earlier runs."""
    import time
    temp = Path(tempfile.gettempdir())
    for folder in temp.iterdir():
        try:
            if folder.name.startswith(SCRATCH_PREFIXES) and folder.is_dir() \
                    and time.time() - folder.stat().st_mtime > SCRATCH_STALE_SECONDS:
                _remove_tree(folder)
        except OSError:
            pass
    yield
    gc.collect()                                 # closes database handles nothing refers to any more
    folders = [_TMP] + [m.TEST_DIR for m in list(sys.modules.values())
                        if isinstance(getattr(m, "TEST_DIR", None), Path)]
    for folder in folders:
        if folder.name.startswith(SCRATCH_PREFIXES) and folder.parent.resolve() == temp.resolve():
            _remove_tree(folder)


def _agent_running() -> bool:
    try:
        import psutil
        return any("supervisor.py" in " ".join(p.info["cmdline"] or []) for p in psutil.process_iter(["cmdline"]))
    except Exception:
        return True


MODEL_HOSTS = ("generativelanguage.googleapis.com", "api.groq.com", "api.cloudflare.com", "api.mistral.ai")


@pytest.fixture(autouse=True)
def no_real_model_calls(monkeypatch):
    """Tests never reach a real AI model: on the Mini (which has the keys) a weekly-report test called real Gemini
    models and spent the day's free requests (2026-10-06). A test that fakes urlopen itself overrides this."""
    import urllib.request
    real = urllib.request.urlopen

    def guarded(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if any(h in url for h in MODEL_HOSTS):
            raise RuntimeError(f"a test tried to call a real AI model: {url.split('?')[0]}")
        return real(req, *a, **k)
    monkeypatch.setattr(urllib.request, "urlopen", guarded)

    # The same for the libraries the SDKs use (google-genai is httpx); FastAPI's TestClient (httpx) is let through
    import httpx

    def checked(real_send):
        def send(self, request, *a, **k):
            if any(h in str(request.url) for h in MODEL_HOSTS):
                raise RuntimeError(f"a test tried to call a real AI model: {request.url.host}{request.url.path}")
            return real_send(self, request, *a, **k)
        return send
    monkeypatch.setattr(httpx.Client, "send", checked(httpx.Client.send))
    monkeypatch.setattr(httpx.AsyncClient, "send", checked(httpx.AsyncClient.send))
    try:
        import requests
        monkeypatch.setattr(requests.Session, "send", checked(requests.Session.send))
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def youtube_feed_state_reset(monkeypatch):
    """core.feeds remembers, per process, that YouTube's feed failed: one test's 404 mustn't skip the next one's feed.
    And no test reads the real Data API with the .env's YOUTUBE_API_KEY (a test that wants it sets its own)."""
    try:
        from core import feeds
    except ImportError:
        return
    monkeypatch.setattr(feeds, "_down_since", {})
    monkeypatch.setattr(feeds, "_api_key", lambda: "")


@pytest.fixture(autouse=True)
def no_real_page_date_lookups(monkeypatch):
    """The away shift reads a finding's publication date from its page when the search result has none: tests' fake
    findings (https://parks.example.gov/trails...) must never be fetched for real. A test that wants a date passes page_date_fn."""
    try:
        from core import web_tools
    except ImportError:
        return
    monkeypatch.setattr(web_tools, "page_published", lambda url: "")


@pytest.fixture(autouse=True)
def no_real_discover_fetches(monkeypatch):
    """Today's daily cards (core/discover.py: Wikipedia, NASA, Open Trivia DB) fetch the real web: off in every test
    unless the test turns them on (monkeypatch.setattr(discover, "ENABLED", True))."""
    try:
        from core import discover
    except ImportError:
        return
    monkeypatch.setattr(discover, "ENABLED", False)
