"""Judges the local model's shadow answers (data/shadow/memory.jsonl, IDEAS #10): is the Mini's own model as good as
the cloud at the background memory chores?

    python scripts/eval_shadow.py [--purpose extract] [--limit 60]

For each logged call where both answered: identical content (JSON compared as data, not text) counts as a tie. The
rest go to a judge model, blind (the two answers in random order, unnamed), which says which is better and whether
either one does harm (invents a fact, or deletes/forgets a true one). The judge is Gemma 4 26B (free, 14,400 a day),
which produced almost none of the answers it judges. Prints a summary and writes data/evals/shadow-judged.jsonl.
Run on the Mini (the log and the keys are there).
"""
import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.llm import CloudLLM  # noqa: E402

LOG = ROOT / "data" / "shadow" / "memory.jsonl"
OUT = ROOT / "data" / "evals" / "shadow-judged.jsonl"
JUDGE = "gemini:gemma-4-26b-a4b-it"
TASKS = {
    "extract": "From the latest exchange, decide what to change in a personal assistant's long-term memory about its "
               "owner: facts to add or update (durable, about the owner), ones to delete or forget. Requests the "
               "assistant carried out are not facts. Nothing changes when nothing new was learned.",
    "summarize": "Summarize a stretch of conversation for the assistant's long-term memory: accurate, complete on what "
                 "matters, nothing invented.",
    "rollup": "Combine older summaries into one: accurate, keeps what matters, nothing invented.",
    "profile": "Write a short profile of the owner from the facts given: accurate, nothing invented.",
}


def same(a: str, b: str) -> bool:
    try:
        return json.loads(a) == json.loads(b)
    except (ValueError, TypeError):
        return " ".join((a or "").split()) == " ".join((b or "").split())


def judge(llm: CloudLLM, purpose: str, inp: str, first: str, second: str):
    msgs = [{"role": "system", "content": "You compare two answers to the same task, strictly and fairly. "
             f"THE TASK: {TASKS.get(purpose, purpose)} Judge correctness against the input only. "
             "Reply with JSON only: {\"better\": \"A\"|\"B\"|\"same\", \"harm_A\": true|false, \"harm_B\": true|false, "
             "\"why\": \"one sentence\"}. harm = it invents something not in the input, or deletes/forgets something "
             "true, or saves a carried-out request as a fact. 'same' when the difference doesn't matter."},
            {"role": "user", "content": f"INPUT:\n{inp[-3500:]}\n\nANSWER A:\n{first[:2500]}\n\nANSWER B:\n{second[:2500]}"}]
    text = llm.chat(msgs, max_tokens=4096, temperature=0.0, timeout=90, models=[JUDGE]) or ""
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]) if start >= 0 and end > start else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--purpose", default=None)
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()
    rows = [json.loads(l) for l in LOG.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("cloud") and r.get("local") and (not args.purpose or r["purpose"] == args.purpose)]
    llm = CloudLLM()
    random.seed(7)
    tally = Counter()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows[-args.limit:]:
            p = r["purpose"]
            if same(r["cloud"], r["local"]):
                tally[(p, "same")] += 1
                continue
            local_first = random.random() < 0.5
            a, b = (r["local"], r["cloud"]) if local_first else (r["cloud"], r["local"])
            try:
                v = judge(llm, p, r.get("input", ""), a, b)
            except Exception as e:
                v = None
                print("judge failed:", e, flush=True)
            if not v:
                tally[(p, "unjudged")] += 1
                continue
            better = v.get("better")
            winner = "same" if better == "same" else ("local" if (better == "A") == local_first else "cloud")
            tally[(p, winner)] += 1
            if v.get("harm_A" if local_first else "harm_B"):
                tally[(p, "local_harm")] += 1
            if v.get("harm_B" if local_first else "harm_A"):
                tally[(p, "cloud_harm")] += 1
            f.write(json.dumps({"at": r["at"], "purpose": p, "winner": winner, "why": v.get("why"),
                                "local_harm": v.get("harm_A" if local_first else "harm_B"),
                                "cloud_harm": v.get("harm_B" if local_first else "harm_A")}, ensure_ascii=False) + "\n")
            print(f"{p}: {winner} | {str(v.get('why'))[:120]}", flush=True)
    print("\nSUMMARY")
    for p in sorted({k[0] for k in tally}):
        print(p, {k[1]: n for k, n in tally.items() if k[0] == p})
    return 0


if __name__ == "__main__":
    sys.exit(main())
