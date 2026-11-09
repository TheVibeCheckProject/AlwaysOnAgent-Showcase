"""Model routing: how long a rate-limited model is skipped."""

import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos (it would render and post real videos): point the agent at a
# folder where it isn't installed, so an accidental call fails harmlessly
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

from core.llm import CloudLLM


def _skipped_for(llm, route, text, seconds=None):
    llm._cooldown_until.clear()
    llm._cool_down(route, seconds, text)
    return llm._cooldown_until[route] - time.time()


def test_only_a_daily_gemini_quota_benches_the_model_until_the_reset():
    llm = CloudLLM.__new__(CloudLLM)
    llm._cooldown_until = {}
    import logging
    llm.logger = logging.getLogger("test")

    per_minute = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generate_content_free_tier_requests, "
                  "quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier")
    assert _skipped_for(llm, "gemini:gemini-3.8-flash", per_minute, seconds=34) <= 35       # the retry delay
    assert _skipped_for(llm, "gemini:gemini-3.8-flash", per_minute) <= CloudLLM.DEFAULT_COOLDOWN + 1

    per_day = "429 Quota exceeded, quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    left = _skipped_for(llm, "gemini:gemini-3.8-flash", per_day)
    assert 60 < left <= 24 * 3600                                    # until midnight Pacific

    # Other providers' limits keep their own handling
    assert _skipped_for(llm, "groq:openai/gpt-oss-120b", "rate limit reached", seconds=12) <= 13
    assert _skipped_for(llm, "cloudflare:@cf/openai/gpt-oss-120b", "you have used up your daily free allocation of 10,000 neurons") > 60


def test_recent_images_keep_ten_for_a_day(tmp_path, monkeypatch):
    from core import recent_images
    from core.database import DatabaseManager
    db = DatabaseManager(tmp_path / "state.db")
    for i in range(12):
        recent_images.remember(db, f"prompt {i}", model="m", filename=f"gen_{i}.jpg")
    items = recent_images.recent(db, 50)
    assert len(items) == recent_images.KEEP and items[0]["prompt"] == "prompt 11"
    assert recent_images.recent(db) == items[:3]

    # Older than a day: forgotten
    import json
    old = json.loads(db.get_state(recent_images.STATE_KEY))
    for item in old:
        item["at"] = "2020-01-01T00:00:00+00:00"
    db.set_state(recent_images.STATE_KEY, json.dumps(old))
    assert recent_images.recent(db) == []
    recent_images.remember(db, "new one", ok=False)
    assert [i["prompt"] for i in recent_images.recent(db, 10)] == ["new one"]


def test_an_overloaded_or_hung_model_is_skipped_on_the_next_messages(monkeypatch):
    import io
    import json
    import urllib.error

    from core import llm as llm_mod

    llm = CloudLLM()
    llm.usage_log = None
    llm.keys = {"gemini": "k", "groq": "k"}
    llm.urls = {"gemini": "https://gemini.test", "groq": "https://groq.test"}
    llm._cooldown_until = {}
    llm.OVERLOAD_RETRY_WAIT = 0
    routes = ["gemini:gemini-3.8-flash", "gemini:gemini-3.5-flash-lite", "groq:openai/gpt-oss-120b"]
    tried = []

    class Resp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        model = json.loads(req.data)["model"]
        tried.append(model)
        if model == "gemini-3.8-flash":
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"high demand"))
        if model == "gemini-3.5-flash-lite":
            raise TimeoutError("timed out")
        return Resp(json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    assert llm.chat_with_tools([{"role": "user", "content": "hey"}], [], lambda n, a: "", models=routes)[0] == "hi"
    # An overloaded Gemini model isn't retried (failed calls seem to count against its 20 a day): next route at once
    assert tried == ["gemini-3.8-flash", "gemini-3.5-flash-lite", "openai/gpt-oss-120b"]

    # Both failures are benched for a few minutes: the next message goes straight to the model that works
    tried.clear()
    assert llm.chat_with_tools([{"role": "user", "content": "again"}], [], lambda n, a: "", models=routes)[0] == "hi"
    assert tried == ["openai/gpt-oss-120b"]
    assert llm._cooldown_until["gemini:gemini-3.8-flash"] - time.time() > CloudLLM.OVERLOAD_COOLDOWN - 5
    # The reply is credited to the model that actually answered, not the first one on the list
    assert llm.last_route == "groq:openai/gpt-oss-120b"


def test_a_short_deadline_does_not_bench_the_chat_model(monkeypatch):
    """2026-10-14: the tool router's 5 s timeouts on 3.5 Flash-Lite benched it for 180 s, so the chat replies right
    after skipped their first model while every other Gemini model answered 503. Only a normal wait benches."""
    import json

    from core import llm as llm_mod

    llm = CloudLLM()
    llm.usage_log = None
    llm.keys = {"gemini": "k", "groq": "k"}
    llm.urls = {"gemini": "https://gemini.test", "groq": "https://groq.test"}
    llm._cooldown_until = {}

    def fake_urlopen(req, timeout):
        assert json.loads(req.data)["model"] == "gemini-3.5-flash-lite"
        raise TimeoutError("timed out")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    route = "gemini:gemini-3.5-flash-lite"
    assert llm.complete([{"role": "user", "content": "route me"}], timeout=5, models=[route])[0] is None
    assert route not in llm._cooldown_until                         # the router's deadline: not benched
    assert llm.complete([{"role": "user", "content": "chat"}], timeout=25, models=[route])[0] is None
    assert llm._cooldown_until[route] - time.time() > CloudLLM.TIMEOUT_COOLDOWN - 5


def test_after_two_failures_a_reply_skips_the_other_flash_models(monkeypatch):
    """2026-10-06: Google overloaded; each Flash failure cost 8-15 s, replies took over a minute and were given
    up on. After two failures in one reply, the rest of it goes past the other Flash models to Flash-Lite."""
    import io
    import json
    import urllib.error

    from core import llm as llm_mod

    llm = CloudLLM()
    llm.usage_log = None
    llm.keys = {"gemini": "k"}
    llm.urls = {"gemini": "https://gemini.test"}
    llm._cooldown_until = {}
    llm.OVERLOAD_RETRY_WAIT = 0
    routes = ["gemini:gemini-3.8-flash", "gemini:gemini-3.6-flash", "gemini:gemini-3-flash-preview",
              "gemini:gemini-3.5-flash-lite"]
    tried = []

    class Resp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        model = json.loads(req.data)["model"]
        tried.append(model)
        if model != "gemini-3.5-flash-lite":
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"high demand"))
        return Resp(json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    assert llm.chat_with_tools([{"role": "user", "content": "hey"}], [], lambda n, a: "", models=routes)[0] == "hi"
    assert tried == ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]


def test_an_overload_blip_is_retried_except_on_gemini(monkeypatch):
    """One 503: a Gemini route goes straight to the next Flash model (no retry: its free tier allows 20 requests a
    day and failed calls seem to count); other providers get one quick retry and nothing is benched."""
    import io
    import json
    import urllib.error

    from core import llm as llm_mod

    llm = CloudLLM()
    llm.usage_log = None
    llm.keys = {"gemini": "k", "groq": "k"}
    llm.urls = {"gemini": "https://gemini.test", "groq": "https://groq.test"}
    llm._cooldown_until = {}
    llm.OVERLOAD_RETRY_WAIT = 0
    calls = []

    class Resp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        calls.append(json.loads(req.data)["model"])
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"high demand"))
        return Resp(json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    routes = ["gemini:gemini-3.8-flash", "gemini:gemini-3.6-flash"]
    assert llm.chat_with_tools([{"role": "user", "content": "hey"}], [], lambda n, a: "", models=routes)[0] == "hi"
    assert calls == ["gemini-3.8-flash", "gemini-3.6-flash"] and llm.last_route == "gemini:gemini-3.6-flash"

    calls.clear()
    llm._cooldown_until = {}
    routes = ["groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b"]
    assert llm.chat_with_tools([{"role": "user", "content": "hey"}], [], lambda n, a: "", models=routes)[0] == "hi"
    assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-120b"]
    assert llm.last_route == "groq:openai/gpt-oss-120b" and not llm._cooldown_until


def test_garbage_replies_are_dropped_but_real_ones_kept():
    from core.llm import degenerate
    assert degenerate("!" * 300) and degenerate("ok " + "?" * 60) and degenerate("aaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    table = ("| Course | Distance |\n|" + "-" * 60 + "|" + "-" * 50 + "|\n| Springfield CC | 5 mi |\n\n" + "=" * 80)
    for fine in ("Sunny, highs near 80.", "OK", table, "Wow!!! That's great!", "1. a\n2. b\n3. c",
                 "Here's the plan:\n\n---\n\n**Step 1** ... **Step 2** ..."):
        assert not degenerate(fine), fine


def test_gemma_goes_through_googles_own_api_and_its_thinking_is_dropped(monkeypatch):
    """The OpenAI-style route answers Gemma with HTTP 500 (2026-10-06); Google's generateContent works. Gemma
    thinks out loud first: it gets room for that, and only the answer comes back."""
    import io
    import json

    from core import llm as llm_mod

    llm = CloudLLM()
    llm.usage_log = None
    llm.keys = {"gemini": "k"}
    llm._cooldown_until = {}
    sent = {}

    class Resp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        sent["url"], sent["body"] = req.full_url, json.loads(req.data)
        return Resp(json.dumps({"candidates": [{"content": {"parts": [
            {"text": "Let me think about the facts...", "thought": True},
            {"text": '{"facts": ["moved to Chicago"]}'}]}}],
            "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 300}}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    out = llm.complete([{"role": "system", "content": "Extract facts."}, {"role": "user", "content": "I moved."}],
                       max_tokens=400, models=["gemini:gemma-4-26b-a4b-it"])
    assert out[0] == '{"facts": ["moved to Chicago"]}'
    assert sent["url"].endswith("/gemma-4-26b-a4b-it:generateContent")
    assert sent["body"]["systemInstruction"]["parts"][0]["text"] == "Extract facts."
    assert sent["body"]["generationConfig"]["maxOutputTokens"] >= 4096


def test_gemma_can_use_tools_in_chat(monkeypatch):
    """Chat with Gemma (2026-10-06): the tool list goes as functionDeclarations, Gemma's functionCall comes back as
    an OpenAI-style tool call, and the next round sends the call back unchanged (with Google's signature) and the
    result as a functionResponse."""
    import io
    import json

    from core import llm as llm_mod

    llm = CloudLLM()
    llm.usage_log = None
    llm.keys = {"gemini": "k"}
    llm._cooldown_until = {}
    bodies, timeouts = [], []
    call_part = {"functionCall": {"name": "get_weather", "args": {"place": "Springfield"}}, "thoughtSignature": "sig-1"}
    answers = [
        [{"text": "The user wants weather...", "thought": True}, call_part],
        [{"text": "It's sunny in Springfield, high of 80."}],
    ]

    class Resp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        bodies.append(json.loads(req.data))
        timeouts.append(timeout)
        return Resp(json.dumps({"candidates": [{"content": {"parts": answers[len(bodies) - 1]}}]}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    tools = [{"type": "function", "function": {"name": "get_weather", "description": "Weather for a place",
                                               "parameters": {"type": "object", "properties": {
                                                   "place": {"type": "string"}}}}},
             {"type": "function", "function": {"name": "list_tasks", "description": "The task queue",
                                               "parameters": {"type": "object", "properties": {}}}}]
    seen = []
    reply, trace = llm.chat_with_tools(
        [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "weather?"}], tools,
        lambda name, args: seen.append((name, json.loads(args))) or "Sunny, high 80",
        models=["gemini:gemma-4-26b-a4b-it"])
    assert reply == "It's sunny in Springfield, high of 80."
    assert seen == [("get_weather", {"place": "Springfield"})] and trace[0]["result"] == "Sunny, high 80"
    decls = bodies[0]["tools"][0]["functionDeclarations"]
    assert [d["name"] for d in decls] == ["get_weather", "list_tasks"] and "parameters" not in decls[1]
    second = bodies[1]["contents"]
    assert [c["role"] for c in second] == ["user", "model", "user"]
    assert second[1]["parts"] == [call_part]                               # resent unchanged, signature included
    assert second[2]["parts"] == [{"functionResponse": {"name": "get_weather",
                                                        "response": {"content": "Sunny, high 80"}}}]
    assert min(timeouts) >= CloudLLM.GEMMA_MIN_TIMEOUT                     # room to think before answering


def test_a_model_is_paced_under_its_per_minute_limit(monkeypatch):
    """Flash-Lite peaked at 18 calls a minute against 15 (2026-10-06): the 15th call in a minute waits."""
    from core import llm as llm_mod
    llm = CloudLLM.__new__(CloudLLM)
    import logging
    llm.logger = logging.getLogger("test")
    clock = {"t": 1000.0}
    monkeypatch.setattr(llm_mod.time, "time", lambda: clock["t"])
    waited = []
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: (waited.append(s), clock.__setitem__("t", clock["t"] + s)))
    for _ in range(14):
        llm._pace("gemini:gemini-3.5-flash-lite", {"messages": [], "max_tokens": 10})
    assert not waited
    llm._pace("gemini:gemini-3.5-flash-lite", {"messages": [], "max_tokens": 10})
    assert waited and sum(waited) <= CloudLLM.PACE_MAX_WAIT + 1           # waited, but never past the cap
    llm._pace("groq:openai/gpt-oss-120b", {"messages": []})                 # other providers: no pacing


def test_waiting_for_one_model_never_holds_up_another(monkeypatch):
    """2026-10-31 review, defect 6: _pace slept while holding the class-wide lock, so a background chore waiting for
    room on one model stalled a chat call to another for up to PACE_MAX_WAIT."""
    from core import llm as llm_mod
    llm = CloudLLM.__new__(CloudLLM)
    import logging
    llm.logger = logging.getLogger("test")
    clock = {"t": 1000.0}
    monkeypatch.setattr(llm_mod.time, "time", lambda: clock["t"])
    held_while_sleeping = []

    def sleep(s):
        held_while_sleeping.append(CloudLLM._PACE_LOCK.locked())
        clock["t"] += s
    monkeypatch.setattr(llm_mod.time, "sleep", sleep)
    for _ in range(15):                                                    # the 15th has to wait for room
        llm._pace("gemini:gemini-3.5-flash-lite", {"messages": [], "max_tokens": 10})
    assert held_while_sleeping and not any(held_while_sleeping)


def test_no_retired_or_redirected_model_is_listed():
    """Google retires and redirects models (2026-10-10: 3.7 Flash -> 3.8, 3.5 Flash -> 3.6; 2.5 Flash-Lite answers 404
    though still listed). A listed one costs a failed call, or a duplicate try, on every reply that reaches it."""
    retired = {"gemini-3.7-flash", "gemini-3.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash",
               "gemini-1.5-flash", "gemini-3.1-flash-lite-preview"}
    for name in ("CHAT_MODELS", "BACKGROUND_MODELS", "TASK_MODELS"):
        listed = {r.split(":", 1)[1] for r in getattr(CloudLLM, name) if r.startswith("gemini:")}
        assert not listed & retired, f"{name} lists retired models: {sorted(listed & retired)}"


def test_a_request_too_big_for_groq_is_skipped_not_sent(monkeypatch):
    """Groq's free tier caps a request at ~8K tokens; a full chat request (~10K) was answered 413 every time
    (2026-10-06). It's skipped before sending: no call, no failure logged, the next route answers."""
    import io
    import json

    from core import llm as llm_mod

    llm = CloudLLM()
    llm.usage_log = None
    llm.keys = {"groq": "k", "cloudflare": "k"}
    llm.urls = {"groq": "https://groq.test", "cloudflare": "https://cf.test"}
    llm._cooldown_until = {}
    sent, failed = [], []
    monkeypatch.setattr(llm, "_failed", lambda route, kind, started=None: failed.append(route))

    class Resp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        sent.append(req.full_url)
        return Resp(json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    big = [{"role": "system", "content": "x" * 40000}, {"role": "user", "content": "hey"}]
    routes = ["groq:openai/gpt-oss-120b", "cloudflare:@cf/openai/gpt-oss-120b"]
    assert llm.chat_with_tools(big, [], lambda n, a: "", models=routes)[0] == "hi"
    assert llm.complete(big, models=routes)[0] == "hi"
    assert all("groq" not in u for u in sent) and failed == []
    small = [{"role": "user", "content": "hey"}]
    llm.complete(small, models=routes)
    assert sent[-1].startswith("https://groq.test")              # a small one still goes to Groq


def _fake_gemini(monkeypatch, answer):
    """A fake Gemini endpoint: answer(body) -> the message dict to return. Returns the request bodies sent."""
    import io
    import json

    from core import llm as llm_mod

    sent = []

    class Resp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        body = json.loads(req.data)
        sent.append(body)
        return Resp(json.dumps({"choices": [{"message": answer(body)}]}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    llm = CloudLLM()
    llm.usage_log = None
    llm.keys = {"gemini": "k"}
    llm.urls = {"gemini": "https://gemini.test"}
    llm._cooldown_until = {}
    return llm, sent


def test_gemini_flash_thinks_low_and_flash_lite_keeps_its_default(monkeypatch):
    """Unset, 3.8 Flash thinks at 'medium' and timed out at 20 s on every chat attempt (week to 2026-10-27)."""
    llm, sent = _fake_gemini(monkeypatch, lambda body: {"content": "hi"})
    for model in ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"):
        assert llm.chat([{"role": "user", "content": "hey"}], models=[f"gemini:{model}"]) == "hi"
    assert [b.get("reasoning_effort") for b in sent] == ["low", "low", None]


def test_an_empty_answer_after_a_tool_round_is_asked_again_on_the_same_model(monkeypatch):
    """2026-10-26: Flash-Lite twice ended a tool round with no text; the reply then waited 20 s on 3.8 Flash."""
    def answer(body):
        roles = [m["role"] for m in body["messages"]]
        if "tool" not in roles:
            return {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                   "function": {"name": "list_tasks", "arguments": "{}"}}]}
        return {"content": "Two tasks ran." if roles[-1] == "user" else ""}

    llm, sent = _fake_gemini(monkeypatch, answer)
    reply, trace = llm.chat_with_tools([{"role": "user", "content": "what ran?"}], [{"type": "function", "function": {
        "name": "list_tasks", "parameters": {"type": "object", "properties": {}}}}], lambda n, a: "2 tasks",
        models=["gemini:gemini-3.5-flash-lite", "gemini:gemini-3.8-flash"])
    assert reply == "Two tasks ran." and [t["name"] for t in trace] == ["list_tasks"]
    assert [b["model"] for b in sent] == ["gemini-3.5-flash-lite"] * 3          # never moved to 3.8 Flash
