"""Why chat missed its best model, and daily-quota warnings (core/llm_usage.py, docs/IDEAS.md #12)."""

import io
import json
import sys
import urllib.error
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import llm as llm_mod
from core import llm_usage
from core.database import DatabaseManager
from core.llm import CloudLLM
from core.llm_usage import UsageLog, usage_purpose

BEST = "gemini:gemini-3.8-flash"
PER_DAY = b"429 Quota exceeded, quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"


class Resp(io.BytesIO):
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _llm(tmp_path):
    llm = CloudLLM()
    llm.usage_log = UsageLog(DatabaseManager(tmp_path / "state.db"))
    llm.keys = {"gemini": "k", "groq": "k"}
    llm.urls = {"gemini": "https://gemini.test", "groq": "https://groq.test"}
    llm._cooldown_until = {}
    llm.OVERLOAD_RETRY_WAIT = 0
    return llm


def _kinds(log):
    return [(r["model"], r["kind"], r["purpose"]) for r in log.db.get_connection().execute(
        "SELECT model, kind, purpose FROM llm_failures ORDER BY id")]


def test_each_failed_call_is_recorded_with_why(tmp_path, monkeypatch):
    llm = _llm(tmp_path)
    routes = [BEST, "gemini:gemini-3.5-flash-lite", "groq:qwen/qwen3.8-27b", "groq:openai/gpt-oss-120b"]

    def fake_urlopen(req, timeout):
        model = json.loads(req.data)["model"]
        if model == "gemini-3.8-flash":
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"high demand"))
        if model == "gemini-3.5-flash-lite":
            raise TimeoutError("timed out")
        if model == "qwen/qwen3.8-27b":
            return Resp(json.dumps({"choices": [{"message": {"content": "!" * 300}}]}).encode())
        return Resp(json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with usage_purpose("chat"):
        assert llm.chat_with_tools([{"role": "user", "content": "hey"}], [], lambda n, a: "", models=routes)[0] == "hi"
    assert _kinds(llm.usage_log) == [("gemini-3.8-flash", "overloaded", "chat"),     # once (Gemini overloads aren't retried)
                                     ("gemini-3.5-flash-lite", "timeout", "chat"),
                                     ("qwen/qwen3.8-27b", "garbage", "chat")]


def test_rate_limits_and_daily_quota_are_told_apart(tmp_path, monkeypatch):
    llm = _llm(tmp_path)

    def fake_urlopen(req, timeout):
        model = json.loads(req.data)["model"]
        body = PER_DAY if model == "gemini-3.8-flash" else b"Requests per minute exceeded"
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(body))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    assert llm.chat([{"role": "user", "content": "x"}], models=[BEST, "gemini:gemini-3.5-flash-lite"]) is None
    assert [k[1] for k in _kinds(llm.usage_log)] == ["quota_day", "rate_limit"]


def test_the_daily_limit_is_learned_and_warned_about_once(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(llm_usage, "NOTIFY", sent.append)
    log = UsageLog(DatabaseManager(tmp_path / "state.db"))
    for _ in range(10):
        log.record("gemini-3.8-flash", {}, provider="gemini")
    assert log.daily_limit("gemini", "gemini-3.8-flash") == 0 and sent == []     # nothing known: no guessing
    log.record_failure("gemini", "gemini-3.8-flash", "quota_day")
    log.record_failure("gemini", "gemini-3.8-flash", "quota_day")
    assert log.daily_limit("gemini", "gemini-3.8-flash") == 10
    assert len(sent) == 1 and "used up today's quota after 10 calls" in sent[0]

    # A later day: warned once at 80% of the learned limit
    log.db.get_connection().execute("DELETE FROM llm_usage")
    log.db.get_connection().commit()
    log.db.set_state("quota_warn:gemini:gemini-3.8-flash", "")
    sent.clear()
    for _ in range(9):
        log.record("gemini-3.8-flash", {}, provider="gemini")
    assert len(sent) == 1 and "answered 8 calls today" in sent[0] and "about 10" in sent[0]
    # A lower count on a day other programs shared the key doesn't lower the learned limit
    log.db.set_state("quota_out:gemini:gemini-3.8-flash", "")
    log.record_failure("gemini", "gemini-3.8-flash", "quota_day")
    assert log.daily_limit("gemini", "gemini-3.8-flash") == 10


def test_no_warnings_outside_the_live_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_usage, "NOTIFY", None)
    log = UsageLog(DatabaseManager(tmp_path / "state.db"))
    log.record_failure("gemini", "gemini-3.8-flash", "quota_day")       # must not raise or send anything


def test_health_note_only_when_chat_mostly_missed_its_best_model(tmp_path):
    log = UsageLog(DatabaseManager(tmp_path / "state.db"))
    with usage_purpose("chat"):
        for model, provider in [("gemini-3.8-flash", "gemini")] * 2 + [("gemini-3.5-flash-lite", "gemini")] * 4:
            log.record(model, {}, provider=provider, duration_ms=900)
        for _ in range(3):
            log.record_failure("gemini", "gemini-3.8-flash", "overloaded")
    h = log.model_health(24, BEST)
    assert h["chat_replies"] == 6 and h["best_share"] == 0.33
    note = log.health_note(BEST)
    assert "2 of 6" in note and "gemini-3.8-flash" in note and "overloaded" in note and "3x" in note
    with usage_purpose("chat"):
        for _ in range(10):
            log.record("gemini-3.8-flash", {}, provider="gemini")
    assert log.health_note(BEST) is None                             # 12 of 16 on the best model: nothing to say
