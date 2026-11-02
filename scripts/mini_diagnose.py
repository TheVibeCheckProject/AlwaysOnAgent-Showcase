"""Why aren't calls going through? One read-only snapshot of the agent's health. Runs ON the Mini.

From the laptop:

    python scripts/mini.py py < scripts/mini_diagnose.py > diagnose.txt

then share diagnose.txt. Nothing is changed, restarted or sent anywhere; no AI is called. Secrets aren't read
(.env is never opened; the logs already hide token= and sig= values).

What it prints, for the last HOURS hours:
  1. Is the agent up (GET /api/status, no token needed), and how long since it restarted.
  2. AI calls that failed (llm_failures), grouped by what they were for, which model and why; then the latest ones.
  3. AI calls that worked (llm_usage), per purpose and model, with timings, and calls per Gemini model today.
  4. Warnings, errors and tracebacks from logs/worker.log (and its rotated copies), repeats counted, newest last.
"""
import datetime
import json
import os
import re
import sqlite3
import urllib.request
from collections import Counter
from pathlib import Path

HOURS = 24                     # how far back to look
LATEST_FAILURES = 40           # failed calls listed one by one
LOG_LINES = 150                # distinct warning/error lines shown

REPO = Path(r"C:\Projects\AlwaysOnAgent")
if not REPO.exists():          # a copy of the data elsewhere: run from the repo root
    REPO = Path.cwd()
DATA, LOGS = REPO / "data", REPO / "logs"
since = datetime.datetime.now().astimezone() - datetime.timedelta(hours=HOURS)
cutoff = since.isoformat()


def section(title):
    print(f"\n=== {title} ===")


print(f"Agent diagnosis, last {HOURS} h (since {since:%Y-%m-%d %H:%M}), repo {REPO}")

# ---------------------------------------------------------------- 1. up?
section("1. Status")
try:
    port = os.environ.get("HUD_PORT", "8080")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=10) as r:
        status = json.loads(r.read().decode("utf-8", "replace"))
    for key in ("status", "uptime", "uptime_seconds", "version", "api_version", "active_task", "queue", "model"):
        if key in status:
            print(f"{key}: {status[key]}")
    if not any(k in status for k in ("status", "uptime", "uptime_seconds")):
        print(json.dumps(status)[:600])
except Exception as e:
    print(f"/api/status did not answer: {e!r}  <- the agent may be down or restarting")

# ---------------------------------------------------------------- 2-3. the database
db = DATA / "state.db"
if not db.exists():
    print(f"\nNo database at {db}")
else:
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)   # file:///C:/... on Windows

    def rows(sql, args=()):
        try:
            return conn.execute(sql, args).fetchall()
        except sqlite3.Error as e:
            print(f"(query failed: {e})")
            return []

    section("2. AI calls that FAILED")
    failed = rows("SELECT at, purpose, provider, model, kind, duration_ms FROM llm_failures WHERE at >= ? ORDER BY at",
                  (cutoff,))
    ok_total = rows("SELECT COUNT(*) FROM llm_usage WHERE at >= ?", (cutoff,))
    ok_n = ok_total[0][0] if ok_total else 0
    print(f"Failed attempts: {len(failed)}; answered calls: {ok_n}")
    print("(one reply can fail on several models and still be answered by the next; a reply only fails if all do)")
    for (purpose, route, kind), n in Counter((p, f"{pr}:{m}", k) for _, p, pr, m, k, _ in failed).most_common(30):
        print(f"  {n:4d}  {purpose:14s} {route:45s} {kind}")
    print(f"\nLatest {LATEST_FAILURES}:")
    for at, purpose, prov, model, kind, ms in failed[-LATEST_FAILURES:]:
        print(f"  {at[:19]}  {purpose:14s} {prov}:{model:38s} {kind:11s} {(ms or 0) / 1000:5.1f} s")

    section("3. AI calls that WORKED")
    for purpose, prov, model, n, avg, worst in rows(
            "SELECT purpose, provider, model, COUNT(*), CAST(AVG(duration_ms) AS INTEGER), MAX(duration_ms) "
            "FROM llm_usage WHERE at >= ? GROUP BY purpose, provider, model ORDER BY purpose, COUNT(*) DESC",
            (cutoff,)):
        print(f"  {n:4d}  {purpose:14s} {prov}:{model:38s} avg {(avg or 0) / 1000:4.1f} s, worst {(worst or 0) / 1000:4.1f} s")
    today = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
    print(f"\nGemini calls today ({today}), answered + failed (free limits: ~20 a Flash model, ~500 Flash-Lite):")
    used = dict(((m, n) for m, n in rows("SELECT model, COUNT(*) FROM llm_usage WHERE provider = 'gemini' "
                                         "AND at LIKE ? GROUP BY model", (today + "%",))))
    lost = dict(((m, n) for m, n in rows("SELECT model, COUNT(*) FROM llm_failures WHERE provider = 'gemini' "
                                         "AND at LIKE ? GROUP BY model", (today + "%",))))
    for model in sorted(set(used) | set(lost)):
        print(f"  {model:38s} {used.get(model, 0)} answered, {lost.get(model, 0)} failed")
    last = rows("SELECT at, purpose, provider, model FROM llm_usage ORDER BY id DESC LIMIT 1")
    if last:
        print(f"\nLast answered call: {last[0][0][:19]} ({last[0][1]}, {last[0][2]}:{last[0][3]})")
    conn.close()

# ---------------------------------------------------------------- 4. the log
section("4. logs/worker.log: warnings, errors, tracebacks")
STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(\w+)\]")
NUMBERS = re.compile(r"\d+")
files = sorted(LOGS.glob("worker.log*"), key=lambda p: p.stat().st_mtime) if LOGS.exists() else []
start = since.strftime("%Y-%m-%d %H:%M:%S")
seen, order = Counter(), []
first_at, last_at = {}, {}
in_traceback = False
current_at = ""
for path in files:
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            m = STAMP.match(line)
            if m:
                in_traceback = False
                at, level = m.groups()
                current_at = at                         # a traceback below belongs to this line's time
                if at < start or level not in ("WARNING", "ERROR", "CRITICAL"):
                    continue
                text = line[m.end():].strip()
                text = re.sub(r"^\[PID:\d+\]\s*", "", text)
            elif line.startswith("Traceback") or (in_traceback and line.startswith((" ", "\t"))) or \
                    (in_traceback and re.match(r"^\w+(\.\w+)*(Error|Exception)\b", line)):
                in_traceback = True
                at, text = current_at, line
                if at < start:
                    continue
            else:
                continue
            key = NUMBERS.sub("#", text[:200])          # "task-196 took 25 s" and "task-197 took 31 s" are one line
            if key not in seen:
                order.append(key)
                first_at[key] = (at, text[:400])
            seen[key] += 1
            last_at[key] = at
if not files:
    print(f"No log files in {LOGS}")
elif not order:
    print("No warnings or errors in this window.")
else:
    print(f"{sum(seen.values())} lines, {len(order)} distinct; showing the last {LOG_LINES} distinct (count, last seen):")
    for key in sorted(order, key=lambda k: last_at[k])[-LOG_LINES:]:
        at, text = first_at[key]
        print(f"  x{seen[key]:<4d} {last_at[key]}  {text}")
print("\nDone.")
