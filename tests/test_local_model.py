"""The local model for background memory work: routing, shadow mode and the cloud-only rules (IDEAS #10)."""

import io
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import llm as llm_mod
from core.database import DatabaseManager
from core.llm import CloudLLM
from core.memory import MemoryManager
from tests.test_memory import FakeEmbedder

LOCAL = "ollama:qwen3:4b-instruct"


class Resp(io.BytesIO):
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _llm(monkeypatch, answers, calls):
    """A CloudLLM whose HTTP answers per model are scripted; calls records (model, url, body)."""
    llm = CloudLLM(keys={"gemini": "k", "ollama": "local"})
    llm.usage_log = None
    llm._cooldown_until = {}
    monkeypatch.setattr(llm, "_cpu_busy", lambda: False)

    def fake_urlopen(req, timeout):
        body = json.loads(req.data)
        calls.append((body["model"], req.full_url, body, timeout))
        out = answers[body["model"]]
        if isinstance(out, Exception):
            raise out
        if "ollama" in req.full_url or req.full_url.endswith("/api/chat"):
            return Resp(json.dumps({"message": {"content": out}, "prompt_eval_count": 10, "eval_count": 5}).encode())
        return Resp(json.dumps({"choices": [{"message": {"content": out}}]}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    return llm


def test_local_route_uses_ollamas_own_api_with_cpu_limits(monkeypatch):
    calls = []
    llm = _llm(monkeypatch, {"qwen3:4b-instruct": '{"ok": true}'}, calls)
    out = llm.chat([{"role": "user", "content": "hi"}], json_mode=True, timeout=40, models=[LOCAL])
    model, url, body, timeout = calls[0]
    assert out == '{"ok": true}' and url.endswith("/api/chat") and model == "qwen3:4b-instruct"
    assert body["think"] is False and body["format"] == "json" and body["stream"] is False
    assert body["options"]["num_thread"] == CloudLLM.LOCAL_THREADS and body["options"]["num_ctx"] == CloudLLM.LOCAL_CTX
    assert timeout >= CloudLLM.LOCAL_TIMEOUT                       # a CPU model gets longer than a cloud call


def test_ollama_down_or_cpu_busy_falls_back_to_the_cloud(monkeypatch):
    import urllib.error
    calls = []
    llm = _llm(monkeypatch, {"qwen3:4b-instruct": urllib.error.URLError(ConnectionRefusedError()),
                             "gemini-3.5-flash-lite": "cloud answer"}, calls)
    routes = [LOCAL, "gemini:gemini-3.5-flash-lite"]
    assert llm.chat([{"role": "user", "content": "x"}], models=routes) == "cloud answer"
    assert llm._cooldown_until[LOCAL] > 0                           # not tried again for a while
    calls.clear()
    assert llm.chat([{"role": "user", "content": "x"}], models=routes) == "cloud answer"
    assert [c[0] for c in calls] == ["gemini-3.5-flash-lite"]

    llm._cooldown_until.clear()
    monkeypatch.setattr(llm, "_cpu_busy", lambda: True)              # a render is running
    calls.clear()
    assert llm.chat([{"role": "user", "content": "x"}], models=routes) == "cloud answer"
    assert [c[0] for c in calls] == ["gemini-3.5-flash-lite"]
    assert not llm.is_available() or llm.keys.get("gemini")          # the local model alone isn't "a provider"


def _memory(tmp_path, llm, mode):
    mem = MemoryManager(DatabaseManager(tmp_path / "state.db"), llm=llm, embedder=FakeEmbedder())
    mem.LOCAL_MODEL, mem.LOCAL_MODE = LOCAL, mode
    return mem


def test_shadow_mode_logs_the_local_answer_but_applies_the_clouds(monkeypatch, tmp_path):
    calls = []
    cloud = {"add": [{"content": "Alex likes golf.", "category": "preference", "importance": 3, "evidence": "I like golf"}],
             "update": [], "delete": []}
    local = {"add": [{"content": "Alex likes tennis.", "category": "preference", "importance": 3, "evidence": "I like golf"}],
             "update": [], "delete": []}
    llm = _llm(monkeypatch, {"qwen3:4b-instruct": json.dumps(local), "gemini-3.5-flash-lite": json.dumps(cloud)}, calls)
    llm.BACKGROUND_MODELS = ("gemini:gemini-3.5-flash-lite",)
    mem = _memory(tmp_path, llm, "shadow")
    mem.learn_from_exchange("I like golf", "Nice!")
    facts = [f["content"] for f in mem.list_facts()]
    assert facts == ["Alex likes golf."] or facts == ["The user likes golf."] or "golf" in facts[0], facts
    assert not any("tennis" in f for f in facts)                   # the local answer is never applied
    log = [json.loads(l) for l in (tmp_path / "shadow" / "memory.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[0]["purpose"] == "extract" and "tennis" in log[0]["local"] and "golf" in log[0]["cloud"]
    assert log[0]["local_route"] == LOCAL and log[0]["cloud_route"] == "gemini:gemini-3.5-flash-lite"

    mem.add_message("user", "I like golf and tennis")
    calls.clear()                                                    # erasing never goes to the local model
    mem.scrub("golf")
    erase_calls = [c for c in calls if c[2]["messages"][0]["content"].startswith("The user asked their assistant to forget")]
    assert erase_calls and all(c[0] != "qwen3:4b-instruct" for c in erase_calls)


def test_primary_mode_a_local_delete_is_redone_on_the_cloud(monkeypatch, tmp_path):
    calls = []
    llm = _llm(monkeypatch, {}, calls)
    llm.BACKGROUND_MODELS = ("gemini:gemini-3.5-flash-lite",)
    mem = _memory(tmp_path, llm, "primary")
    keep, _ = mem.add_fact("Alex lives in Springfield.", "profile", 4)
    local = {"add": [], "update": [], "delete": [{"id": keep, "evidence": "moved"}]}
    cloud = {"add": [], "update": [], "delete": []}
    calls.clear()
    llm_answers = {"qwen3:4b-instruct": json.dumps(local), "gemini-3.5-flash-lite": json.dumps(cloud)}
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", lambda req, timeout: (
        calls.append(json.loads(req.data)["model"]) or
        (Resp(json.dumps({"message": {"content": llm_answers["qwen3:4b-instruct"]}}).encode())
         if req.full_url.endswith("/api/chat") else
         Resp(json.dumps({"choices": [{"message": {"content": llm_answers["gemini-3.5-flash-lite"]}}]}).encode()))))
    stats = mem.learn_from_exchange("I moved, the old place is gone", "Got it.")
    assert calls[:2] == ["qwen3:4b-instruct", "gemini-3.5-flash-lite"]   # local first, then the cloud redoes it
    assert stats["deleted"] == 0 and mem.get_fact(keep)                  # only the cloud's answer counted
