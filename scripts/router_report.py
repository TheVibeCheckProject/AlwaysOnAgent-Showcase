"""Is the chat's tool router earning its keep? Read-only numbers from the live data. Runs ON the Mini.

From the laptop:

    python scripts/mini.py py < scripts/router_report.py

or on any machine with a copy of the data:  python scripts/router_report.py [--days 7] [--data path/to/data]

What it reads (nothing is changed, no AI is called):
  - data/tool_routing.jsonl: one line per chat reply (memory.py _log_tool_routing). A reply whose groups grew
    (`added_groups`) had to call more_tools: the router (or the keywords) missed a group it needed. `picker` says
    who chose the groups ("keywords" = the router failed or is off); lines before 2026-10-14 don't have it.
  - data/state.db llm_usage / llm_failures: the router's own calls (purpose `route_tools`), how long they took and
    how they failed; 3.5 Flash-Lite calls per day (all purposes; ~500 a day free, failed calls count too); chat
    calls that timed out (chat_with_tools waits 25 s per call since 0007ff7).

The verdict at the end uses rough thresholds (below); read the numbers too.
"""
import argparse
import datetime
import json
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

MINI_REPO = Path(r"C:\Projects\AlwaysOnAgent")
FLASH_LITE = "gemini-3.5-flash-lite"
FLASH_LITE_DAY = 500           # free calls a day (docs/GEMINI_FREE_TIER.md)
MISS_RATE_HIGH = 0.10          # more than 1 reply in 10 needed more_tools: the router's prompt needs work
FALLBACK_RATE_HIGH = 0.20      # more than 1 in 5 fell back to keywords: the router is unreliable
ROUTER_P90_SLOW_MS = 3000      # the router runs before every reply; slower than this is felt
FLASH_LITE_BUSY = 0.80         # a day above this share of FLASH_LITE_DAY is too close to the limit


def parse_time(text):
    try:
        t = datetime.datetime.fromisoformat(str(text))
        return t if t.tzinfo else t.astimezone()
    except ValueError:
        return None


def pct(part, whole):
    return f"{part / whole:.0%}" if whole else "n/a"


def ms_summary(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return "no timings"
    p90 = values[min(len(values) - 1, int(len(values) * 0.9))]
    return f"median {statistics.median(values) / 1000:.1f} s, p90 {p90 / 1000:.1f} s, max {values[-1] / 1000:.1f} s"


def read_routing(path, since):
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            at = parse_time(row.get("time"))
            if at and at >= since:
                rows.append(row)
    return rows


def query(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []                      # an older database without the table: report nothing rather than fail


def _resolve_data_dir(args) -> Path:
    if args.data:
        return Path(args.data)
    # piped through scripts/mini.py, __file__ is "<stdin>"; on the Mini the repo is at MINI_REPO
    here = Path(globals().get("__file__") or "<stdin>")
    if MINI_REPO.exists():
        repo = MINI_REPO
    else:
        repo = here.resolve().parent.parent if here.is_file() else Path.cwd()
    return repo / "data"


def _report_replies(rows, verdict) -> None:
    print("1. Chat replies and misses (tool_routing.jsonl)")
    if not rows:
        print("   No chat replies logged in this window.\n")
        return
    missed = [r for r in rows if r.get("added_groups")]
    print(f"   Replies: {len(rows)}; needed more_tools (a missed group): {len(missed)} ({pct(len(missed), len(rows))})")
    added = Counter(g for r in missed for g in r["added_groups"])
    if added:
        print("   Groups added late: " + ", ".join(f"{g} x{n}" for g, n in added.most_common()))
    no_tool = sum(1 for r in rows if not r.get("tools_called"))
    print(f"   Replies that called no tool: {no_tool} ({pct(no_tool, len(rows))})")
    tokens = [r.get("tool_tokens", 0) for r in rows if r.get("tool_tokens")]
    if tokens:
        print(f"   Tool definitions sent: median ~{int(statistics.median(tokens))} tokens a reply")
    pickers = Counter(r.get("picker") or "unknown" for r in rows)
    print("   Groups chosen by: " + ", ".join(f"{p} {n}" for p, n in pickers.most_common())
          + ("  (unknown = logged before the picker field existed)" if pickers.get("unknown") else ""))
    for p in ("router", "keywords"):
        these = [r for r in rows if r.get("picker") == p]
        if these:
            m = sum(1 for r in these if r.get("added_groups"))
            print(f"     {p}: {m} of {len(these)} missed ({pct(m, len(these))})")
    known = pickers.get("router", 0) + pickers.get("keywords", 0)
    if len(missed) / len(rows) > MISS_RATE_HIGH:
        verdict.append(f"Misses are high ({pct(len(missed), len(rows))}): look at the late groups above and "
                       "add them to ROUTER_SYSTEM_PROMPT's descriptions.")
    if known and pickers.get("keywords", 0) / known > FALLBACK_RATE_HIGH:
        verdict.append(f"The router fell back to keywords on {pct(pickers['keywords'], known)} of replies: "
                       "it isn't reliable enough to be worth its call.")
    print()


def _report_router_calls(conn, cutoff, verdict) -> None:
    print("2. The router's calls (llm_usage / llm_failures, purpose route_tools)")
    ok = query(conn, "SELECT provider, model, duration_ms FROM llm_usage "
                     "WHERE purpose = 'route_tools' AND at >= ?", (cutoff,))
    bad = query(conn, "SELECT provider, model, kind, duration_ms FROM llm_failures "
                      "WHERE purpose = 'route_tools' AND at >= ?", (cutoff,))
    print(f"   Answered: {len(ok)}; failed attempts: {len(bad)}")
    for (prov, model), n in Counter((p, m) for p, m, _ in ok).most_common():
        print(f"     {prov}:{model} answered {n}: "
              + ms_summary([d for p, m, d in ok if (p, m) == (prov, model)]))
    if bad:
        print("   Failures: " + ", ".join(f"{p}:{m} {k} x{n}" for (p, m, k), n
                                         in Counter((p, m, k) for p, m, k, _ in bad).most_common()))
    if ok:
        durations = sorted(d for _, _, d in ok if d is not None)
        if durations and durations[min(len(durations) - 1, int(len(durations) * 0.9))] > ROUTER_P90_SLOW_MS:
            verdict.append("The router is slow (p90 over 3 s before every reply): consider CHAT_TOOL_ROUTER=0.")
    print()


def _report_flash_lite(conn, cutoff, verdict) -> None:
    print(f"3. 3.5 Flash-Lite calls a day (free limit ~{FLASH_LITE_DAY}; failed calls count too)")
    used = query(conn, "SELECT substr(at, 1, 10) AS day, SUM(purpose = 'route_tools'), COUNT(*) FROM llm_usage "
                       "WHERE provider = 'gemini' AND model = ? AND at >= ? GROUP BY day ORDER BY day",
                 (FLASH_LITE, cutoff))
    failed = dict(query(conn, "SELECT substr(at, 1, 10) AS day, COUNT(*) FROM llm_failures "
                              "WHERE provider = 'gemini' AND model = ? AND at >= ? GROUP BY day",
                        (FLASH_LITE, cutoff)))
    if not used:
        print("   No Flash-Lite calls in this window.")
    busiest = 0
    for day, router_n, total in used:
        all_n = total + failed.get(day, 0)
        busiest = max(busiest, all_n)
        print(f"   {day}: {all_n} calls ({failed.get(day, 0)} failed), router {router_n} ({pct(router_n, total)})")
    if busiest > FLASH_LITE_DAY * FLASH_LITE_BUSY:
        verdict.append(f"Flash-Lite reached {busiest} calls on its busiest day (limit ~{FLASH_LITE_DAY}): "
                       "the router's share above is what turning it off would save.")
    print()


def _report_chat_timeouts(conn, cutoff, days, verdict) -> None:
    print("4. Chat model calls that timed out (chat_with_tools waits 25 s per call)")
    timeouts = query(conn, "SELECT provider, model, duration_ms FROM llm_failures "
                           "WHERE purpose = 'chat' AND kind = 'timeout' AND at >= ?", (cutoff,))
    chats = query(conn, "SELECT duration_ms FROM llm_usage WHERE purpose = 'chat' AND at >= ?", (cutoff,))
    print(f"   Chat calls answered: {len(chats)} ({ms_summary([d for (d,) in chats])})")
    print(f"   Timed out: {len(timeouts)}"
          + (": " + ", ".join(f"{p}:{m} x{n}" for (p, m), n in Counter((p, m) for p, m, _ in timeouts).most_common())
             if timeouts else ""))
    if timeouts:
        print(f"   Each timeout cost about {ms_summary([d for _, _, d in timeouts])} before the next model")
    if len(timeouts) >= max(3, days):
        verdict.append(f"{len(timeouts)} chat timeouts: each made you wait ~25 s; consider the old 15 s.")
    print()


def _print_verdict(verdict) -> None:
    print("Verdict:")
    for line in verdict or ["Nothing stands out: leave the router and the timeout as they are."]:
        print(f" - {line}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=float, default=7, help="how far back to look (default 7)")
    ap.add_argument("--data", help="the data folder (default: the repo's data/)")
    args = ap.parse_args()

    data = _resolve_data_dir(args)
    since = datetime.datetime.now().astimezone() - datetime.timedelta(days=args.days)
    print(f"Router report: the last {args.days:g} days (since {since:%Y-%m-%d %H:%M}), data in {data}\n")
    verdict: list[str] = []

    # ---------------------------------------------------------------- 1. replies and misses
    rows = read_routing(data / "tool_routing.jsonl", since)
    _report_replies(rows, verdict)

    conn = None
    db = data / "state.db"
    if db.exists():
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)   # file:///C:/... on Windows
    else:
        print(f"No database at {db}; skipping the model sections.")

    if conn is not None:
        cutoff = since.isoformat()
        # ---------------------------------------------------------------- 2. the router's own calls
        _report_router_calls(conn, cutoff, verdict)
        # ---------------------------------------------------------------- 3. Flash-Lite budget
        _report_flash_lite(conn, cutoff, verdict)
        # ---------------------------------------------------------------- 4. chat timeouts (the 25 s change)
        _report_chat_timeouts(conn, cutoff, args.days, verdict)
        conn.close()

    _print_verdict(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
