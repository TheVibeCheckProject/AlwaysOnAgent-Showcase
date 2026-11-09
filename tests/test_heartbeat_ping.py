"""The external heartbeat (core/heartbeat_ping.py): off without a URL, pings every 5 minutes, /fail when the API
doesn't answer, and never raises."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import heartbeat_ping as hp


class FakeResponse:
    status = 200

    def read(self):
        return b"OK"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _setup(monkeypatch, url, api_up=True, ping_ok=True):
    pinged = []
    monkeypatch.setattr(hp, "get_config", lambda key, default=None: url if key == "HEALTHCHECK_URL" else default)

    def fake_urlopen(req, timeout=None):
        target = req if isinstance(req, str) else req.full_url
        if "/api/status" in target:
            if not api_up:
                raise OSError("connection refused")
            return FakeResponse()
        if not ping_ok:
            raise OSError("no network")
        pinged.append(target)
        return FakeResponse()

    monkeypatch.setattr(hp.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(hp, "_last_attempt", 0.0)
    return pinged


def test_off_without_a_url(monkeypatch):
    pinged = _setup(monkeypatch, "")
    assert hp.maybe_ping() is None and hp.ping() is None and not pinged


def test_pings_then_waits_five_minutes(monkeypatch):
    pinged = _setup(monkeypatch, "https://hc-ping.com/abc/")
    t = hp.maybe_ping()
    t.join(5)
    assert pinged == ["https://hc-ping.com/abc"]
    assert hp.maybe_ping() is None                                  # not due again yet
    monkeypatch.setattr(hp, "_last_attempt", hp.time.monotonic() - hp.PING_EVERY_SECONDS - 1)
    hp.maybe_ping().join(5)
    assert len(pinged) == 2


def test_reports_fail_when_the_api_is_down(monkeypatch):
    pinged = _setup(monkeypatch, "https://hc-ping.com/abc", api_up=False)
    assert hp.ping() == "https://hc-ping.com/abc/fail" and pinged == ["https://hc-ping.com/abc/fail"]


def test_a_failed_ping_never_raises(monkeypatch):
    _setup(monkeypatch, "https://hc-ping.com/abc", ping_ok=False)
    assert hp.ping() is None


def test_the_url_is_redacted_like_a_secret():
    from core import config
    assert "HEALTHCHECK_URL" in config.SENSITIVE_ENV_KEYS


def test_a_stopped_scheduler_is_reported_as_a_failure_naming_it(monkeypatch):
    from core import schedules
    _setup(monkeypatch, "https://hc-ping.com/abc")
    monkeypatch.setattr(hp, "degraded_problems", list)
    bodies = []
    real = hp.urllib.request.urlopen

    def capture(req, timeout=None):
        if not isinstance(req, str) and req.data:
            bodies.append(req.data.decode())
        return real(req, timeout=timeout)

    monkeypatch.setattr(hp.urllib.request, "urlopen", capture)
    monkeypatch.setattr(schedules, "LAST_PASS", hp.time.time() - 600)
    assert hp.ping().endswith("/fail") and "schedule runner has stopped" in bodies[-1]
    monkeypatch.setattr(schedules, "LAST_PASS", hp.time.time())
    assert hp.ping() == "https://hc-ping.com/abc" and bodies[-1] == "ok"


def test_ollama_down_is_a_push_once_a_day_not_a_failure(monkeypatch, tmp_path):
    from core import config
    from core.database import DatabaseManager
    db = DatabaseManager(tmp_path / "hb.db")
    _setup(monkeypatch, "https://hc-ping.com/abc")
    monkeypatch.setattr(hp, "degraded_problems", lambda: ["memory search's model isn't answering"])
    pushed = []
    monkeypatch.setattr(config, "notify_owner", lambda text: pushed.append(text) or True)
    assert hp.ping(db=db) == "https://hc-ping.com/abc"               # not /fail: the agent still works
    hp.ping(db=db)
    assert len(pushed) == 1 and "isn't answering" in pushed[0]
