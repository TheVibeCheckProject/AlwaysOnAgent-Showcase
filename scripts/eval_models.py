"""
Model benchmark: which free Gemini / Gemma models can do the agent's work (2026-10-06)
====================================================================================
  python scripts/eval_models.py --models gemma-4-31b-it,gemini-3.1-flash-lite            memory chores + tool choice
  python scripts/eval_models.py --models gemini-3.5-flash-lite --only tools               just tool choice

Two parts, every model on the same cases:
- memory: the extract and verify cases from data/evals/memory_cases.json (the owner's own messages, never in git),
  through the production prompts and scored exactly as scripts/eval_memory.py scores them;
- tools: TOOL_CASES below. Each message gets the real persona and the tool list the agent would offer for it
  (AssistantTools.tool_specs: core + the routed groups + more_tools); the model's first tool call is scored. A
  more_tools call that loads the right group is followed for one more round, as in production.

Calls go straight to Google's generateContent API (Gemma only works there; the agent's OpenAI-style route gives
Gemma a server error), with the model's thinking dropped, paced under each model's free per-minute limits, and
retried on Google's "overloaded" errors. Each answer's time is kept: the agent's chores give up after 30-40 s.
Results: data/evals/models-<time>.json, and a table at the end. Run it on the Mini (the cases and the database
are there).
"""
import argparse
import collections
import datetime
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import eval_memory

from core.assistant_tools import TOOL_GROUPS, AssistantTools
from core.database import DatabaseManager
from core.memory import ASSISTANT_PERSONA, FINAL_REMINDER, MemoryManager

EVAL_DIR = ROOT / "data" / "evals"
API = "https://generativelanguage.googleapis.com/v1beta/models"
# The free limits from Google's rate-limit page (2026-10-06): requests and tokens per minute, kept a bit under
LIMITS = {"gemma": (28, 14000), "flash-lite": (13, 240000), "flash": (4, 240000)}

# message -> the tools that count as the right first call (several when either is right)
TOOL_CASES = [
    ("How many unread emails do I have?", ["gmail_unread"]),
    ("Any important emails from yesterday?", ["gmail_important"]),
    ("Find the email from Jackson about the claim form", ["gmail_search"]),
    ("What's the weather going to be like this weekend?", ["get_weather"]),
    ("What's on my task queue?", ["list_tasks"]),
    ("Remind me at 9pm to take out the trash", ["create_schedule"]),
    ("Make me a affirmation video", ["render_video"]),
    ("Post 2 pins to Pinterest", ["post_pins"]),
    ("What did my numbers look like this week?", ["weekly_report"]),
    ("Forget that I play golf", ["forget_memory"]),
    ("What do you remember about my fishing cabin?", ["search_memory"]),
    ("Use the computer to find the office hours of the Travis County Clerk in Austin", ["computer_task"]),
    ("Search the web for news about the latest Gemini models", ["web_search", "get_news"]),
    ("Cancel the task that's running", ["cancel_task", "list_tasks"]),
    ("Show me my last rendered videos", ["list_videos"]),
    ("Check your logs for errors from this morning", ["read_logs"]),
    ("Write me a caption draft for tomorrow's TikTok", ["canvas_write"]),
    ("Pause my TikTok schedule", ["list_schedules", "change_schedule"]),
    ("Fix the typo on the website's about page", ["start_project_job"]),
    ("What's the status of the agent right now?", ["get_system_status"]),
    ("Thanks, that's great!", []),                      # nothing to look up: no tool
    ("Tell me a joke", []),
]


class Pacer:
    """Keeps a model under its per-minute request and token limits."""

    def __init__(self, rpm: int, tpm: int):
        self.rpm, self.tpm = rpm, tpm
        self.calls: collections.deque = collections.deque()      # (time, tokens)

    def wait(self, tokens: int) -> None:
        while True:
            now = time.time()
            while self.calls and now - self.calls[0][0] > 60:
                self.calls.popleft()
            if len(self.calls) < self.rpm and sum(t for _, t in self.calls) + tokens <= self.tpm:
                return
            time.sleep(1.0)

    def used(self, tokens: int) -> None:
        self.calls.append((time.time(), tokens))


def limits_for(model: str):
    kind = "gemma" if "gemma" in model else "flash-lite" if "lite" in model else "flash"
    return LIMITS[kind]


class Native:
    """One model through Google's generateContent API. chat() matches what the memory code calls."""

    def __init__(self, key: str, model: str):
        self.key, self.model = key, model
        self.pacer = Pacer(*limits_for(model))
        self.times: list[float] = []
        self.errors: list[str] = []

    @staticmethod
    def _contents(messages: list[dict[str, Any]]):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                    for m in messages if m["role"] in ("user", "assistant")]
        return system, contents

    @staticmethod
    def _tools(specs: list[dict[str, Any]]):
        decls = []
        for s in specs:
            f = s["function"]
            d = {"name": f["name"], "description": f.get("description", "")}
            params = f.get("parameters") or {}
            if params.get("properties"):
                d["parameters"] = params
            decls.append(d)
        return [{"functionDeclarations": decls}]

    def call(self, system: str, contents: list, tools=None, max_tokens: int = 700, temperature: float = 0.1,
             timeout: float = 150) -> dict[str, Any]:
        body: dict[str, Any] = {"contents": contents,
                                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = tools
        est = len(json.dumps(body)) // 4 + max_tokens
        for attempt in range(2):                     # one retry: an overloaded model counts as a miss, not a stall
            print(f"      . call {self.model} (try {attempt + 1}, ~{est} tokens)", flush=True)
            self.pacer.wait(est)
            req = urllib.request.Request(f"{API}/{self.model}:generateContent?key={self.key}",
                                         data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            started = time.time()
            try:
                r = json.loads(urllib.request.urlopen(req, timeout=min(timeout, 90)).read())
                secs = time.time() - started
                usage = r.get("usageMetadata") or {}
                self.pacer.used(int(usage.get("totalTokenCount") or est))
                self.times.append(secs)
                print(f"      . answered in {secs:.1f}s", flush=True)
                parts = ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                answer = [p for p in parts if not p.get("thought")]
                return {"text": "".join(p.get("text", "") for p in answer).strip(),
                        "calls": [p["functionCall"] for p in answer if "functionCall" in p],
                        "parts": parts, "seconds": secs}
            except urllib.error.HTTPError as e:
                self.pacer.used(est)
                msg = f"HTTP {e.code}"
                if e.code in (429, 500, 503) and attempt < 1:
                    time.sleep(8)
                    continue
                self.errors.append(msg)
                return {"text": "", "calls": [], "parts": [], "seconds": time.time() - started, "error": msg}
            except Exception as e:
                self.pacer.used(est)
                if attempt < 1:
                    time.sleep(8)
                    continue
                self.errors.append(str(e)[:80])
                return {"text": "", "calls": [], "parts": [], "seconds": time.time() - started, "error": str(e)[:80]}
        return {"text": "", "calls": [], "parts": [], "seconds": 0, "error": "gave up"}

    def chat(self, messages, max_tokens: int = 700, temperature: float = 0.1, json_mode: bool = False,
             timeout: float = 40, models=None, **kw) -> str | None:
        # Gemma thinks out loud first and needs room for it (with 400 tokens it was cut off before answering)
        system, contents = self._contents(messages)
        out = self.call(system, contents, max_tokens=max(max_tokens, 4096) if "gemma" in self.model else max_tokens,
                        temperature=temperature, timeout=timeout if "gemma" not in self.model else 120)
        return out["text"] or None


def run_memory(native: Native, cases: dict, db: DatabaseManager) -> dict[str, Any]:
    mem = MemoryManager(db, llm=native, embedder=None)
    rows: dict[str, list[dict[str, Any]]] = {"extract": [], "verify": []}
    for case in cases.get("extract", []):
        user, reply = eval_memory.exchange(db, case["msg"])
        if user is None:
            continue
        saved = eval_memory.extracted(mem, user, reply, None)
        problem = eval_memory.judge_extract(case, saved)
        rows["extract"].append({"msg": case["msg"], "ok": not problem, "problem": problem, "saved": saved})
        print(f"    {'ok ' if not problem else 'BAD'} extract M{case['msg']}: {problem or 'fine'}", flush=True)
    for case in cases.get("verify", []):
        agree, _reason = mem.second_opinion(mem.verify_question(case["kind"], case["fact"], case["context"]))
        ok = agree is case["agree"]
        rows["verify"].append({"kind": case["kind"], "ok": ok, "answer": agree, "want": case["agree"]})
        print(f"    {'ok ' if ok else 'BAD'} verify {case['kind']}: {agree} (want {case['agree']})", flush=True)
    return rows


def run_tools(native: Native, db: DatabaseManager) -> list[dict[str, Any]]:
    rows = []
    system = ASSISTANT_PERSONA + "\n\n" + FINAL_REMINDER + \
        f"\n\nCurrent date and time: {datetime.datetime.now():%A, %B %d, %Y %I:%M %p}."
    for text, want in TOOL_CASES:
        tools = AssistantTools(db, None, ROOT, user_text=text)
        specs = tools.tool_specs()
        contents = [{"role": "user", "parts": [{"text": text}]}]
        out = native.call(system, contents, tools=native._tools(specs), max_tokens=1024, temperature=0.4)
        calls = [c.get("name") for c in out["calls"]]
        via = ""
        if calls[:1] == ["more_tools"]:
            group = (out["calls"][0].get("args") or {}).get("group", "")
            via = f" via more_tools({group})"
            if group in TOOL_GROUPS:
                tools.more_tools(group)
                contents += [{"role": "model", "parts": [p for p in out["parts"] if "functionCall" in p]},
                             {"role": "user", "parts": [{"functionResponse": {"name": "more_tools",
                                                                              "response": {"result": "loaded"}}}]}]
                # _specs stays None when CHAT_TOOL_ROUTING=0 (tool_specs returns early); then reuse specs
                loaded = tools._specs if tools._specs is not None else specs
                out = native.call(system, contents, tools=native._tools(loaded), max_tokens=1024,
                                  temperature=0.4)
                calls = [c.get("name") for c in out["calls"]]
        first = calls[0] if calls else None
        ok = (first in want) if want else first is None
        rows.append({"message": text, "want": want, "got": calls, "ok": ok, "error": out.get("error"),
                     "seconds": round(out["seconds"], 1)})
        print(f"    {'ok ' if ok else 'BAD'} {text[:55]!r}: {first or out.get('error') or 'no tool'}{via} "
              f"(want {want or 'none'}) {out['seconds']:.1f}s", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="comma-separated Google model names, e.g. gemma-4-31b-it")
    ap.add_argument("--only", choices=("memory", "tools"))
    args = ap.parse_args()
    from apply_update import _env_value
    key = _env_value("GEMINI_API_KEY")
    cases = json.loads((EVAL_DIR / "memory_cases.json").read_text(encoding="utf-8"))
    db = DatabaseManager(eval_memory.scratch_copy())
    report: dict[str, Any] = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "models": {}}
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"== {model}", flush=True)
        native = Native(key, model)
        result: dict[str, Any] = {}
        if args.only in (None, "memory"):
            result.update(run_memory(native, cases, db))
        if args.only in (None, "tools"):
            result["tools"] = run_tools(native, db)
        times = sorted(native.times)
        result["seconds"] = {"median": round(times[len(times) // 2], 1) if times else None,
                             "slowest": round(times[-1], 1) if times else None,
                             "over_40s": sum(t > 40 for t in times)}
        result["errors"] = native.errors
        report["models"][model] = result
        out = EVAL_DIR / f"models-{datetime.datetime.now():%Y%m%d-%H%M%S}.json"
        out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    print("\nmodel | extract | verify | tools | median s | slowest s | >40 s | errors")
    for model, r in report["models"].items():
        score = lambda k, r=r: f"{sum(x['ok'] for x in r.get(k, []))}/{len(r.get(k, []))}" if r.get(k) else "-"
        s = r["seconds"]
        print(f"{model} | {score('extract')} | {score('verify')} | {score('tools')} | {s['median']} | "
              f"{s['slowest']} | {s['over_40s']} | {len(r['errors'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
