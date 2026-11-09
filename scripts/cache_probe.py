"""
Groq prompt-cache probe
=======================
Finds out how Groq's prompt cache treats the assistant's chat prompts, by sending a few tiny
requests (real persona and tool definitions, "Say OK." as the question) and reading
usage.prompt_tokens_details.cached_tokens.

Answers:
  1. How much of an identical prompt is served from cache on the second send?
  2. Are the tool definitions placed before or after the system message in the cached prefix?
     (If after, anything that changes in the system message also un-caches all the tools.)
  3. Does the current layout (time + memory inside the first system message) get cache hits?
  4. Does the planned layout (static system message, history, then a late system message with
     the time and memory) keep the persona, tools and history cached?

Usage:  python scripts/cache_probe.py [--model openai/gpt-oss-120b] [--tries 3]
Uses the GROQ_API_KEY from .env and roughly 40k tokens of the model's free-tier budget (less
whatever is cached), paced to stay under the per-minute limit, so it takes several minutes and
chat on the same model may wait briefly while it runs.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.config  # noqa: F401  (loads .env)
from core.assistant_tools import AssistantTools
from core.memory import ASSISTANT_PERSONA

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
QUESTION = {"role": "user", "content": "Say OK."}
HISTORY = [
    {"role": "user", "content": "Can you check whether the render for batch 12 finished?"},
    {"role": "assistant", "content": "Batch 12 finished at 3:14 PM; the video is in output_videos."},
    {"role": "user", "content": "Great. Remind me what resolution we settled on for TikTok?"},
    {"role": "assistant", "content": "1080x1920 at 30 fps, exported as H.264."},
    {"role": "user", "content": "And the wax seal should peel off with the flap, right?"},
    {"role": "assistant", "content": "Right: it peels off with the flap instead of splitting."},
]


def _seconds(reset: str) -> float:
    m = re.fullmatch(r"(?:(\d+)m)?(?:([\d.]+)s)?(?:([\d.]+)ms)?", (reset or "").strip())
    if not m or not any(m.groups()):
        return 10.0
    mins, secs, ms = m.groups()
    return int(mins or 0) * 60 + float(secs or 0) + float(ms or 0) / 1000.0


def call(api_key: str, model: str, messages, tools=None):
    payload = {"model": model, "messages": messages, "max_tokens": 16, "temperature": 0}
    if model.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "low"
    if tools:
        payload["tools"] = tools
    for _ in range(4):
        req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), method="POST", headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "AlwaysOnAgent/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                usage = json.loads(resp.read())["usage"]
                remaining = int(resp.headers.get("x-ratelimit-remaining-tokens") or 99999)
                if remaining < 4000:        # stay under the per-minute budget
                    wait = _seconds(resp.headers.get("x-ratelimit-reset-tokens", "")) + 1
                    print(f"    (pacing: waiting {wait:.0f}s for the rate limit to refill)")
                    time.sleep(wait)
                time.sleep(2)               # let the cache entry settle before the next request
                return usage["prompt_tokens"], (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise SystemExit(f"Groq returned HTTP {e.code}: {e.read()[:300]!r}")
            wait = float(e.headers.get("retry-after") or _seconds(e.headers.get("x-ratelimit-reset-tokens", "")))
            print(f"    (rate limited: waiting {wait:.0f}s)")
            time.sleep(wait + 1)
    raise SystemExit("Still rate limited after several waits; try again in a minute.")


def _run_experiment(api_key, model, label, make, tools_, tries):
    """Sends make() once to warm the cache, then `tries` more times; reports each try's cached tokens."""
    call(api_key, model, make(), tools_)
    got = [call(api_key, model, make(), tools_) for _ in range(tries)]
    prompt, cached = got[-1][0], [c for _, c in got]
    hits = sum(1 for c in cached if c)
    print(f"  {label:<46} prompt {prompt:>5}  cached per try {cached}  hits {hits}/{tries}")
    return prompt, max(cached), hits


def _experiment_identical(api_key, model, persona, tools, tries, p_no_tools, block):
    print("1. Identical prompt resent: how reliable are cache hits?")
    p_tools, c_same, h_same = _run_experiment(api_key, model, "persona + tools", persona, tools, tries)
    print(f"   (tools add ~{p_tools - p_no_tools} tokens; a full hit would be {block(p_tools)})\n")
    return p_tools, c_same, h_same


def _experiment_timestamp(api_key, model, persona, tools, tries, stamp, p_no_tools, p_tools, block):
    print("2. Only the END of the system message changes (a timestamp)")
    _, c_tail, _ = _run_experiment(api_key, model, "persona + new stamp each time",
                                   lambda: [{"role": "system", "content": ASSISTANT_PERSONA + "\n\n" + stamp()},
                                            QUESTION], tools, tries)
    print(f"   (tools after the system message -> at most {block(p_no_tools)}; tools before it -> about {block(p_tools)})\n")
    return c_tail


def _experiment_tool_change(api_key, model, persona, tools, tries, p_no_tools, block):
    print("3. Only the FIRST tool changes")
    changed = json.loads(json.dumps(tools))
    changed[0]["function"]["description"] += f" (probe variant {time.time_ns()})"
    _, c_tool, _ = _run_experiment(api_key, model, "persona + tools with tool 1 edited", persona, changed, tries)
    print(f"   (tools after the system message -> about {block(p_no_tools)}; tools before it -> about 0)\n")
    return c_tool


def _experiment_layouts(api_key, model, tools, tries, stamp, memory):
    print("4. Current layout: time + memory inside the first system message, then history")
    p_cur, c_cur, h_cur = _run_experiment(api_key, model, "current layout, new time each time", lambda: [
        {"role": "system", "content": f"{ASSISTANT_PERSONA}\n\n{stamp()}\n\n{memory}"}, *HISTORY, QUESTION],
        tools, tries)
    print("\n5. Planned layout: static system, history, then a late system message with time + memory")
    p_plan, c_plan, h_plan = _run_experiment(api_key, model, "planned layout, new time each time", lambda: [
        {"role": "system", "content": ASSISTANT_PERSONA}, *HISTORY,
        {"role": "system", "content": f"{stamp()}\n\n{memory}"}, QUESTION], tools, tries)
    return p_cur, c_cur, h_cur, p_plan, c_plan, h_plan


def _print_summary(args, p_tools, c_same, h_same, c_tail, c_tool, p_cur, c_cur, h_cur, p_plan, c_plan, h_plan):
    print("\nSummary (max cached over the tries; hits are not guaranteed, see experiment 1)")
    print(f"- Identical prompt: up to {c_same}/{p_tools} cached, {h_same}/{args.tries} tries hit.")
    print(f"- Timestamp at the end of the system message: up to {c_tail} cached.")
    print(f"- First tool edited: up to {c_tool} cached.")
    print(f"- Current layout:  up to {c_cur}/{p_cur} cached ({h_cur}/{args.tries} hits).")
    print(f"- Planned layout:  up to {c_plan}/{p_plan} cached ({h_plan}/{args.tries} hits).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    ap.add_argument("--tries", type=int, default=3, help="measured sends per experiment, after one warm-up")
    args = ap.parse_args()
    import os
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise SystemExit("GROQ_API_KEY is not set (.env).")
    tools = AssistantTools.specs()
    stamp = lambda: f"Current date and time: {time.strftime('%A, %B %d, %Y %H:%M:%S')}. Probe {time.time_ns()}."
    memory = "## Memories relevant to this message\n- The user's dog is named Pepper.\n- The user lives in Springfield."
    block = lambda n: n // 512 * 512      # Groq caches whole 512-token blocks (hits seen: 1024, 1536)

    print(f"Model: {args.model}, {args.tries} measured tries per experiment (after one warm-up)\n")
    persona = lambda: [{"role": "system", "content": ASSISTANT_PERSONA}, QUESTION]
    p_no_tools = call(api_key, args.model, persona(), None)[0]
    print(f"Persona alone is {p_no_tools} prompt tokens.\n")

    p_tools, c_same, h_same = _experiment_identical(api_key, args.model, persona, tools, args.tries,
                                                    p_no_tools, block)
    c_tail = _experiment_timestamp(api_key, args.model, persona, tools, args.tries, stamp, p_no_tools,
                                   p_tools, block)
    c_tool = _experiment_tool_change(api_key, args.model, persona, tools, args.tries, p_no_tools, block)
    p_cur, c_cur, h_cur, p_plan, c_plan, h_plan = _experiment_layouts(api_key, args.model, tools, args.tries,
                                                                      stamp, memory)
    _print_summary(args, p_tools, c_same, h_same, c_tail, c_tool, p_cur, c_cur, h_cur, p_plan, c_plan, h_plan)


if __name__ == "__main__":
    main()
