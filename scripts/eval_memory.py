"""
Memory eval: how well fact extraction and memory search work on the owner's real conversations (docs/IDEAS.md #9)
===============================================================================================================
  python scripts/eval_memory.py                         every case, background models (as in production)
  python scripts/eval_memory.py --models gemini:gemini-3.8-flash
  python scripts/eval_memory.py --only search           just the search cases (no model calls)

Cases live in data/evals/memory_cases.json (personal, never in git):
  {"extract": [{"msg": 41, "expect": [["Springfield"]], "note": "..."},          a user message id (the reply after it
               {"msg": 405, "expect": [], "forbid": ["Experian"]}],            is included); expect = groups of
   "search":  [{"query": "weather at the cabin", "expect": "Pine Lake"}],       alternatives, each group must appear
   "verify":  [{"kind": "delete", "fact": "...", "context": "the owner's message", "agree": false}]}
                 the second opinion before a delete; kind delete / forget (context: what to forget) / merge
                 (context: the fact kept); agree: what a careful checker should answer
An extract case with expect [] passes only if nothing is saved. Extraction runs from scratch (no existing
memories shown) through the production prompt and the same evidence check, and nothing is applied; search runs
on a scratch copy of the live database. It costs one model
call per extract case, so it isn't part of pytest. Results go to data/evals/results-<time>.json.
"""
import argparse
import datetime
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.database import DB_FILE, DatabaseManager  # noqa: E402
from core.embedder import get_memory_embedder  # noqa: E402
from core.llm import CloudLLM  # noqa: E402
from core.memory import MemoryManager, _parse_json_object  # noqa: E402

EVAL_DIR = ROOT / "data" / "evals"


def scratch_copy() -> Path:
    target = Path(tempfile.mkdtemp(prefix="memeval-")) / "state.db"
    src, dst = sqlite3.connect(str(DB_FILE)), sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def exchange(db: DatabaseManager, msg_id: int):
    conn = db.get_connection()
    user = conn.execute("SELECT content FROM mem_messages WHERE id = ? AND role = 'user'", (msg_id,)).fetchone()
    reply = conn.execute("SELECT content FROM mem_messages WHERE id > ? AND role = 'assistant' ORDER BY id LIMIT 1",
                         (msg_id,)).fetchone()
    return (user[0] if user else None), (reply[0] if reply else "")


def extracted(mem: MemoryManager, user: str, reply: str, models) -> list:
    """What production would save from this exchange: the adds and updates that pass the evidence check."""
    if mem._nothing_to_learn(user):
        return []
    # From scratch: with the live facts shown, a fact that's already saved is (rightly) not saved again, which
    # would read as a miss
    existing = "(none)"
    out = mem.llm.chat([
        {"role": "system", "content": mem.EXTRACT_PROMPT.format(subject=mem.subject())},
        {"role": "user", "content": f"Today is {datetime.date.today().isoformat()}.\n\nExisting memories:\n{existing}\n\n"
                                    f"Latest exchange:\nUSER: {user[:3000]}\n\nASSISTANT: {reply[:1500]}"}
    ], max_tokens=700, temperature=0.1, json_mode=True, timeout=40, models=models)
    ops = _parse_json_object(out) or {}
    return [str(o.get("content", "")) for key in ("add", "update") for o in ops.get(key) or []
            if isinstance(o, dict) and mem._grounded(o.get("evidence"), user)]


def judge_extract(case: dict, saved: list) -> str:
    text = " | ".join(saved).lower()
    if any(f.lower() in text for f in case.get("forbid", [])):
        return "saved something it shouldn't"
    groups = case.get("expect", [])
    if not groups:
        return "saved something from a message with nothing to keep" if saved else ""
    missing = [g for g in groups if not any(alt.lower() in text for alt in g)]
    return f"missed {missing}" if missing else ""


_SENTENCE_START = re.compile(r"(?:^|[.!?:]\s+|\n\s*)([A-Z][\w'’-]*)")


def check_profile(profile: str, facts: List[str], name: str = "") -> List[str]:
    """What's wrong with a profile (IDEAS #18): it goes with every message, so a wrong line persists. It must be
    prose about the owner by name, short, and say nothing the saved facts don't: no number and no name of a
    place, person or thing that isn't in them (a cheap check for invented details)."""
    problems = []
    text = (profile or "").strip()
    if not text:
        return ["the profile is empty"]
    if name and re.search(r"\bthe user\b", text, re.I):
        problems.append("says 'the user' instead of the owner's name")
    if name and name not in text:
        problems.append(f"doesn't use the name {name}")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if any(re.match(r"^([-*•#]|\d+[.)]\s|[A-Z][\w ]{0,25}:$)", l) for l in lines):
        problems.append("has headings or bullets, not prose")
    words = len(re.findall(r"\b\w+\b", text))
    if words > 220:
        problems.append(f"{words} words (aim: at most 200)")
    source = " ".join(facts)
    extra_numbers = sorted(set(re.findall(r"\d+", text)) - set(re.findall(r"\d+", source)))
    if extra_numbers:
        problems.append(f"numbers not in the facts: {extra_numbers}")
    starts = {m.group(1) for m in _SENTENCE_START.finditer(text)}
    names = {w for w in re.findall(r"\b[A-Z][\w'’-]+\b", text) if w not in starts and w != "I"}
    unknown = sorted(w for w in names if w.lower().strip("’'s") not in source.lower())
    if unknown:
        problems.append(f"names not in the facts: {unknown}")
    return problems


def run_procedures(mem: MemoryManager, llm: CloudLLM, cases: dict, models, out: list) -> None:
    """Procedural memory (IDEAS #25), on the scratch copy:
    teach   the owner's message -> the extraction call should save a recipe with these words (or none: expect [])
    recall  recipes are loaded, then a message -> these recipes (by index) must be sent, those must not
    learn   a chat tool trace -> a recipe should be saved (true) or not (false: a one-off), second opinion included
    """
    procs = mem.procedures
    conn = mem.db.get_connection()
    for case in cases.get("teach", []):
        out_json = mem.llm.chat([
            {"role": "system", "content": mem.EXTRACT_PROMPT.format(subject=mem.subject())},
            {"role": "user", "content": f"Today is {datetime.date.today().isoformat()}.\n\nExisting memories:\n(none)\n\n"
                                        f"Latest exchange:\nUSER: {case['text']}\n\nASSISTANT: {case.get('reply', 'Got it.')}"}
        ], max_tokens=700, temperature=0.1, json_mode=True, timeout=40, models=models)
        saved = [f"{p.get('when')}: {p.get('do')}" for p in (_parse_json_object(out_json) or {}).get("procedure") or []
                 if isinstance(p, dict) and mem._grounded(p.get("evidence"), case["text"])]
        text = " | ".join(saved).lower()
        want = case.get("expect", [])
        ok = (not saved) if not want else all(any(a.lower() in text for a in g) for g in want)
        out.append({"part": "teach", "text": case["text"], "ok": ok, "saved": saved})
        print(f"  {'ok ' if ok else 'BAD'} teach {case['text'][:60]!r}: {saved or 'nothing saved'}")

    if cases.get("recipes"):
        with conn:
            conn.execute("DELETE FROM mem_procedures")
        ids = [procs.add(r["area"], r["when"], r["do"], r.get("source", "seed"))[0] for r in cases["recipes"]]
        for case in cases.get("recall", []):
            got = [r["id"] for r in procs.relevant(case["query"], areas=case.get("areas", []))]
            want = {ids[i] for i in case.get("expect", [])}
            bad = {ids[i] for i in case.get("forbid", [])} & set(got)
            ok = want <= set(got) and not bad
            out.append({"part": "recall", "query": case["query"], "ok": ok, "got": got})
            print(f"  {'ok ' if ok else 'BAD'} recall {case['query'][:60]!r}: sent "
                  f"{[ids.index(g) for g in got if g in ids]} (want {sorted(case.get('expect', []))}, "
                  f"never {sorted(case.get('forbid', []))})")

    for case in cases.get("learn", []):
        with conn:
            conn.execute("DELETE FROM mem_procedures")
        n = procs.learn_from_trace(case["user"], case["trace"])
        saved = [procs.text_of(p) for p in procs.list()]
        ok = bool(n) is case["expect"]
        out.append({"part": "learn", "user": case["user"], "ok": ok, "saved": saved})
        print(f"  {'ok ' if ok else 'BAD'} learn {case['user'][:50]!r}: {saved or 'nothing'} (want {case['expect']})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", help="comma-separated routes, e.g. gemini:gemini-3.8-flash (default: background)")
    ap.add_argument("--only", choices=("extract", "search", "profile", "verify", "procedures"))
    args = ap.parse_args()
    run = {args.only} if args.only else {"extract", "search", "profile", "verify", "procedures"}
    cases = json.loads((EVAL_DIR / "memory_cases.json").read_text(encoding="utf-8"))
    db = DatabaseManager(scratch_copy())
    llm = CloudLLM()
    embedder = get_memory_embedder()
    # Loaded first: search embeds the query without waiting, so a cold model meant keyword-only search, and the
    # first baseline (3/7) measured that instead of what the running agent does (6/7)
    if not embedder.ensure_loaded():
        print("  ! the embedding model didn't load: search below is keyword-only")
    mem = MemoryManager(db, llm=llm, embedder=embedder)
    models = args.models.split(",") if args.models else list(llm.BACKGROUND_MODELS)
    report = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "models": models, "extract": [],
              "search": [], "profile": [], "verify": [], "procedures": []}

    if "extract" in run:
        for case in cases.get("extract", []):
            user, reply = exchange(db, case["msg"])
            if user is None:
                print(f"  ? M{case['msg']}: no such user message")
                continue
            saved = extracted(mem, user, reply, models)
            problem = judge_extract(case, saved)
            report["extract"].append({"msg": case["msg"], "ok": not problem, "problem": problem, "saved": saved})
            print(f"  {'ok ' if not problem else 'BAD'} M{case['msg']}: {problem or 'fine'}" + (f"  saved: {saved}" if saved else ""))

    if "search" in run:
        for case in cases.get("search", []):
            hits = [f["content"] for f in mem.search_facts(case["query"], k=5)]
            ok = any(case["expect"].lower() in h.lower() for h in hits)
            report["search"].append({"query": case["query"], "ok": ok, "top": hits})
            print(f"  {'ok ' if ok else 'BAD'} search {case['query']!r}" + ("" if ok else f"  top: {hits[:3]}"))

    if "profile" in run:
        # The live profile as it is, and a fresh rebuild from the same facts (on the scratch copy)
        facts = [f["content"] for f in mem._fact_rows()]
        name = mem.owner_name()
        for label in ("live", "rebuilt"):
            if label == "rebuilt" and not mem.rebuild_profile():
                print("  ? the profile couldn't be rebuilt (no model answered)")
                continue
            problems = check_profile(mem.get_profile(), facts, name)
            report["profile"].append({"which": label, "ok": not problems, "problems": problems,
                                      "profile": mem.get_profile()})
            print(f"  {'ok ' if not problems else 'BAD'} profile ({label}): {'; '.join(problems) or 'fine'}")

    if "verify" in run:
        # The second opinion before a delete (IDEAS #11), on its own models unless --models is given
        if args.models:
            mem.VERIFY_MODELS = tuple(models)
        for case in cases.get("verify", []):
            agree, reason = mem.second_opinion(mem.verify_question(case["kind"], case["fact"], case["context"]))
            ok = agree is case["agree"]
            sim = ""
            if case["kind"] == "merge":           # would the merge even be proposed? (DUPLICATE_SIM)
                vecs = mem._embed([case["context"], case["fact"]], wait=True)
                sim = f" sim={float(vecs[0] @ vecs[1]):.3f}" if vecs is not None else ""
            report["verify"].append({**case, "ok": ok, "answer": agree, "reason": reason, "route": llm.last_route})
            print(f"  {'ok ' if ok else 'BAD'} {case['kind']}: {case['fact'][:60]!r} -> {agree} "
                  f"(want {case['agree']}){sim}  [{llm.last_route}] {reason[:90]}")

    if "procedures" in run:
        run_procedures(mem, llm, cases.get("procedures") or {}, models, report["procedures"])

    for kind in ("extract", "search", "profile", "verify", "procedures"):
        rows = report[kind]
        if rows:
            print(f"{kind}: {sum(r['ok'] for r in rows)}/{len(rows)} passed")
    out = EVAL_DIR / f"results-{datetime.datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"saved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
