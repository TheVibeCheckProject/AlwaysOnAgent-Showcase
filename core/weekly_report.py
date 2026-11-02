"""
The weekly numbers report
=========================
Once a week (a "report" schedule, Sundays by default) the agent asks three tools for their numbers and puts
them into one report, the result of a task, so the owner gets the usual "task finished" push and reads the
numbers in DeskAI (a push never carries them through Google's servers):
- VibeStats (VIBESTATS_DIR): site visits by channel and source (Clarity), cards sent, daily-email subscribers.
- TiktokVideos: followers and views, read from TikTok Studio.
- SocialPostEngine: Pinterest impressions, clicks and saves.
A source that fails is named in the report; the rest still go out. The week's row also goes into the tracker
table of VibeStats' GROWTH_PLAN.md, which VibeStats commits and pushes (`track --push`).

Clarity only exports the last 1-3 days, so VibeStats also needs a nightly `collect`: the ScheduleRunner starts it
once a night at NIGHTLY_AT (collect_nightly). The last reports are kept in the state table for later questions.
"""

import datetime
import json
import logging
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional

try:
    from core import social_posts, tiktok_videos
    from core.config import VIBESTATS_DIR, get_safe_subprocess_env
except ImportError:
    import social_posts
    import tiktok_videos
    from config import VIBESTATS_DIR, get_safe_subprocess_env

logger = logging.getLogger("WeeklyReport")
NIGHTLY_AT = datetime.time(23, 50)
KEEP_REPORTS = 12
STATE_REPORTS = "weekly_reports"
STATE_COLLECTED = "stats_collected_on"
NOT_SOURCES = ("(none)", "example-site.com", "www.example-site.com", "localhost", "127.0.0.1")


class VibeStatsError(Exception):
    pass


def vibestats(args: List[str], timeout: float = 180) -> Dict[str, Any]:
    """Runs `vibestats.py <args>` and returns its "done" event; raises VibeStatsError on an "error" event."""
    script = VIBESTATS_DIR / "vibestats.py"
    if not script.is_file():
        raise VibeStatsError(f"VibeStats isn't installed at {VIBESTATS_DIR}")
    env = get_safe_subprocess_env()                  # its own .env holds the Clarity and MailerLite keys
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run([sys.executable, str(script), *args], cwd=str(VIBESTATS_DIR), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise VibeStatsError(f"VibeStats didn't finish within {timeout:.0f}s")
    final: Dict[str, Any] = {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") in ("done", "error"):
            final = event
    if not final:
        raise VibeStatsError(f"VibeStats stopped without a result (exit code {proc.returncode})")
    if final.pop("event") == "error":
        raise VibeStatsError(final.get("message") or "VibeStats failed")
    return final


def collect_nightly(db, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    """The nightly collection, once per date from NIGHTLY_AT on. None when it isn't due."""
    now = now or datetime.datetime.now()
    today = now.date().isoformat()
    if now.time() < NIGHTLY_AT or db.get_state(STATE_COLLECTED, "") == today:
        return None
    db.set_state(STATE_COLLECTED, today)             # first, so a slow or failing run isn't started twice
    try:
        result = vibestats(["collect"])
        problems = result.get("errors") or []
        db.append_log("WARNING" if problems else "INFO",
                      f"Nightly stats: {result.get('visits')} visits, {result.get('cards_total')} cards, "
                      f"{result.get('subscribers')} subscribers" + (f"; problems: {'; '.join(problems)}" if problems else ""))
        return result
    except VibeStatsError as e:
        db.append_log("WARNING", f"Nightly stats collection failed: {e}")
        return {"error": str(e)}


# ------------------------------------------------------------------ the report
def _plus(n: Optional[int]) -> str:
    return "" if n is None else (f"+{n}" if n >= 0 else str(n))


def _since(part: Dict[str, Any]) -> str:
    return "" if part.get("full_period", True) or not part.get("since") else f" since {part['since']}"


def _day(iso: str) -> str:
    d = datetime.date.fromisoformat(iso)
    return f"{d:%b} {d.day}"


def resonance_lines(resonance: Any) -> List[str]:
    """What resonates (TiktokVideos' `performance`, 2026-10-10): the leading theme and the best and weakest quotes.
    The quote writer already leans on this by itself; the report shows what it's learning."""
    if not isinstance(resonance, dict) or not resonance.get("posts_judged"):
        return []
    out = []
    ranked = [t for t in resonance.get("themes", []) if t.get("posts")]
    if ranked:
        top = max(ranked, key=lambda t: t.get("avg_views") or 0)
        out.append(f"• What resonates: {top['theme']} posts average {top['avg_views']:g} views "
                   f"({top['posts']} posts; {resonance['posts_judged']} posts judged in all)")
    if resonance.get("best"):
        b = resonance["best"][0]
        out.append(f"• Most viewed quote: \"{b['quote']}\" ({b['views']} views)")
    if resonance.get("weakest"):
        w = resonance["weakest"][0]
        out.append(f"• Least viewed: \"{w['quote']}\" ({w['views']} views)")
    return out


def format_report(site: Any, tiktok: Any, pins: Any, days: int = 7, resonance: Any = None,
                  proactive: Optional[str] = None) -> str:
    """The report's text. Each part is the tool's result, or an Exception when that source failed."""
    lines: List[str] = []
    if isinstance(site, dict):
        lines.append(f"📊 Week of {_day(site['from'])} – {_day(site['to'])}")
    else:
        lines.append("📊 Weekly report")
    lines += ["", "Website"]
    if isinstance(site, Exception):
        lines.append(f"• Couldn't read the site's numbers: {site}")
    else:
        channels = ", ".join(f"{c['name']} {c['visits']}" for c in site.get("by_channel", [])[:4])
        lines.append(f"• Visits: {site['visits']}" + (f" ({channels})" if channels else ""))
        covered = site.get("hours_covered", 0)
        if covered < days * 24:
            lines.append(f"  (covers {covered // 24} of {days} days: visits are collected nightly)")
        # Not sources of new visitors: no referrer, the site itself, or the owner testing on their own PC
        sources = [s for s in site.get("top_sources", []) if s["name"] not in NOT_SOURCES]
        if sources:
            lines.append("• Top sources: " + ", ".join(f"{s['name']} {s['visits']}" for s in sources[:5]))
        cards = site.get("cards_sent")
        if cards:
            lines.append(f"• Cards sent: {_plus(cards['change'])}{_since(cards)} ({cards['total']} in total)")
        subs = site.get("subscribers")
        if subs:
            lines.append(f"• Daily-email subscribers: {subs['total']} ({_plus(subs['change'])}{_since(subs)})")
    lines += ["", "TikTok"]
    if isinstance(tiktok, Exception):
        lines.append(f"• Couldn't read TikTok: {tiktok}")
    else:
        if tiktok.get("views_change") is None:
            lines.append(f"• Views: {tiktok['views_total']} in total (the change shows from next week)")
        else:
            lines.append(f"• Views: {_plus(tiktok['views_change'])}{_since(tiktok)} ({tiktok['views_total']} in total)")
        lines.append(f"• New posts: {tiktok.get('posts_this_week', 0)}, with {tiktok.get('views_on_this_weeks_posts', 0)} views")
        follow = tiktok.get("followers")
        if follow is not None:
            lines.append(f"• Followers: {follow}" + (f" ({_plus(tiktok['followers_change'])})"
                                                      if tiktok.get("followers_change") is not None else ""))
        best = tiktok.get("best_post")
        if best:
            lines.append(f"• Best post so far: {' '.join(best['posted'].split())}, {best['views']} views")
        lines += resonance_lines(resonance)
    lines += ["", "Pinterest"]
    if isinstance(pins, Exception):
        lines.append(f"• Couldn't read Pinterest: {pins}")
    else:
        lines.append(f"• {pins['impressions']} impressions · {pins['outbound_clicks']} clicks to the site · "
                     f"{pins['saves']} saves")
    if proactive:
        lines += ["", "Proactive", f"• {proactive}" if not str(proactive).startswith("•") else str(proactive)]
    return "\n".join(lines)


def tracker_cells(site: Any, tiktok: Any, pins: Any, days: int = 7) -> Dict[str, str]:
    """The tracker row: a change since the week before, the total in brackets; "—" where a source failed."""
    ok = lambda part: isinstance(part, dict)
    week = _day(site["to"]) if ok(site) else _day(datetime.date.today().isoformat())
    cells = {"week": week, "cards": "", "visits": "", "tiktok": "", "pins": "", "subscribers": ""}
    if ok(site):
        covered = site.get("hours_covered", 0) // 24
        cells["visits"] = str(site["visits"]) + (f" ({covered} of {days} days)" if covered < days else "")
        if site.get("cards_sent"):
            cells["cards"] = f"{_plus(site['cards_sent']['change'])} ({site['cards_sent']['total']})"
        if site.get("subscribers"):
            cells["subscribers"] = f"{site['subscribers']['total']} ({_plus(site['subscribers']['change'])})"
    if ok(tiktok):
        cells["tiktok"] = (f"{tiktok['views_total']} in total" if tiktok.get("views_change") is None
                           else f"{_plus(tiktok['views_change'])} ({tiktok['views_total']})")
    if ok(pins):
        cells["pins"] = str(pins["outbound_clicks"])
    return cells


def build(days: int = 7, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
          db: Any = None) -> Dict[str, Any]:
    """Asks the three tools and returns {"text", "site", "tiktok", "pins", "failed", "proactive"}."""
    def step(n: int, label: str) -> None:
        if on_progress:
            on_progress({"stage": "report", "label": label, "percent": n * 25, "detail": ""})

    step(0, "Site numbers")
    try:
        vibestats(["collect"])                        # tonight's snapshot early (visits skip if collected today)
        site: Any = vibestats(["week", "--days", str(days)])
    except VibeStatsError as e:
        site = e
    step(1, "TikTok numbers")
    try:
        tiktok: Any = tiktok_videos.run(["stats", "--days", str(days)], timeout=300)
    except tiktok_videos.TiktokVideosError as e:
        tiktok = e
    resonance: Any = None
    if not isinstance(tiktok, Exception):
        try:
            resonance = tiktok_videos.run(["performance"], timeout=60)
        except tiktok_videos.TiktokVideosError:
            resonance = None                          # the report goes out without it
    step(2, "Pinterest numbers")
    try:
        pins: Any = social_posts.run(["stats", "--days", str(days)], timeout=120)
    except social_posts.SocialPostError as e:
        pins = e
    step(3, "Writing the report")
    failed = [name for name, part in (("site", site), ("tiktok", tiktok), ("pinterest", pins))
              if isinstance(part, Exception)]
    pro_line = None
    if db is not None:
        try:
            from core.growth_review import proactive_summary
            pro_line = proactive_summary(db, days=days)
        except Exception:
            pass
    text = format_report(site, tiktok, pins, days, resonance=resonance, proactive=pro_line)
    if len(failed) < 3:                               # the tracker in GROWTH_PLAN.md, pushed to GitHub
        cells = tracker_cells(site, tiktok, pins, days)
        try:
            # --name=value: a value like "-1 (5)" would otherwise be taken for an option
            tracked = vibestats(["track", *[f"--{k}={v}" for k, v in cells.items()], "--push"])
            if tracked.get("problem"):
                text += f"\n\n(The tracker was updated on the Mini but not pushed: {tracked['problem']})"
        except VibeStatsError as e:
            text += f"\n\n(The tracker in GROWTH_PLAN.md couldn't be updated: {e})"
    return {"text": text, "failed": failed, "proactive": pro_line,
            **{k: (None if isinstance(v, Exception) else v) for k, v in (("site", site), ("tiktok", tiktok),
                                                                         ("pins", pins))}}


def remember(db, report: Dict[str, Any]) -> None:
    """Keeps the last KEEP_REPORTS reports (text and numbers) in the state table."""
    try:
        kept = json.loads(db.get_state(STATE_REPORTS, "[]") or "[]")
    except ValueError:
        kept = []
    kept.append({"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), **report})
    db.set_state(STATE_REPORTS, json.dumps(kept[-KEEP_REPORTS:], ensure_ascii=False))
