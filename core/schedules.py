"""
Schedules: things the agent does on its own at set times
========================================================
Five kinds, each firing on a cron schedule in the PC's local time:
  video     render a fresh affirmation video (TiktokVideos), and post it to TikTok if the schedule says so
  pins      post the next pins to Pinterest (SocialPostEngine), `count` (1-5) per run
  report    the weekly numbers report (core/weekly_report.py): the owner reads it in DeskAI
  reminder  a push notification to the owner's phone
  task      any background instruction (the default engine, or an AgentWork job on a project)

The owner creates them in chat (the assistant's schedule tools) or in DeskAI's Schedules screen.
ScheduleRunner (a supervisor thread) checks every 20 s, so a reminder isn't held up by a long render.

Safety:
- Minimum time between runs per kind (a typo can't render a video every minute).
- A run missed while the agent was off is made up only within MISSED_GRACE; otherwise it's skipped.
- In standby (the owner paused the agent), video, pin and task runs are skipped; reminders still go out.

Cron: "minute hour day-of-month month day-of-week", with *, lists (1,3), ranges (1-5), steps (*/15, 8-18/2),
names (mon-fri, jan) and @hourly / @daily / @weekly / @monthly. When both day fields are set, either matching
is enough (as in standard cron). Sunday is 0 or 7.
"""

import datetime
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

try:
    from core import actions, maintenance, push
    from core.database import DatabaseManager
except ImportError:
    import actions
    import maintenance
    import push
    from database import DatabaseManager

logger = logging.getLogger("Schedules")

KINDS = ("video", "pins", "report", "reminder", "task")
MIN_GAP_MINUTES = {"video": 60, "pins": 60, "report": 720, "task": 15, "reminder": 5}
MAX_PINS_PER_RUN = 5
MAX_SCHEDULES = 50
MISSED_GRACE = datetime.timedelta(hours=2)

MACROS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
          "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


class CronError(ValueError):
    """The cron expression is invalid (the message says why, for the owner or the assistant)."""


# ---------------------------------------------------------------------------- cron
@dataclass
class Cron:
    expr: str
    minutes: Set[int]
    hours: Set[int]
    days: Set[int]
    months: Set[int]
    weekdays: Set[int]          # 0 = Sunday
    days_any: bool              # the day-of-month field was *
    weekdays_any: bool          # the day-of-week field was *

    def matches_day(self, d: datetime.date) -> bool:
        if d.month not in self.months:
            return False
        dom = d.day in self.days
        dow = (d.isoweekday() % 7) in self.weekdays
        if self.days_any and self.weekdays_any:
            return True
        if self.days_any:
            return dow
        if self.weekdays_any:
            return dom
        return dom or dow

    def next_after(self, after: datetime.datetime) -> datetime.datetime:
        """The first run strictly after `after` (naive local time, to the minute)."""
        start = after.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
        hours, minutes = sorted(self.hours), sorted(self.minutes)
        day = start.date()
        for _ in range(366 * 5):
            if self.matches_day(day):
                for h in hours:
                    if day == start.date() and h < start.hour:
                        continue
                    for m in minutes:
                        candidate = datetime.datetime.combine(day, datetime.time(h, m))
                        if candidate >= start:
                            return candidate
            day += datetime.timedelta(days=1)
        raise CronError(f"'{self.expr}' never runs")

    def next_runs(self, after: datetime.datetime, count: int = 3) -> List[datetime.datetime]:
        runs, t = [], after
        for _ in range(count):
            t = self.next_after(t)
            runs.append(t)
        return runs


def _field(text: str, lo: int, hi: int, what: str, names: Optional[List[str]] = None, name_base: int = 0) -> Set[int]:
    """One cron field's values. names: three-letter names allowed instead of numbers (names[0] = name_base)."""
    values: Set[int] = set()
    for part in text.lower().split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"bad step '/{step_text}' in the {what} field")
            step = int(step_text)

        def num(tok: str) -> int:
            if tok.isdigit():
                return int(tok)
            if names and tok[:3] in names:
                return names.index(tok[:3]) + name_base
            raise CronError(f"'{tok}' isn't a valid {what}")

        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a_text, b_text = part.split("-", 1)
            a, b = num(a_text), num(b_text)
        else:
            a = num(part)
            b = hi if step > 1 else a            # "5/15" means from 5 to the end, every 15
        if not lo <= a <= b <= hi:
            raise CronError(f"'{part}' is out of range for the {what} field ({lo}-{hi})")
        values.update(range(a, b + 1, step))
    return values


def parse(expr: str) -> Cron:
    raw = " ".join((expr or "").split())
    text = MACROS.get(raw.lower(), raw)
    parts = text.split(" ")
    if len(parts) != 5:
        raise CronError("a schedule needs 5 fields: minute hour day-of-month month day-of-week "
                        "(e.g. '0 9 * * *' is every day at 09:00)")
    m, h, dom, mon, dow = parts
    return Cron(expr=raw, minutes=_field(m, 0, 59, "minute"), hours=_field(h, 0, 23, "hour"),
                days=_field(dom, 1, 31, "day of month"), months=_field(mon, 1, 12, "month", MONTHS, 1),
                weekdays={d % 7 for d in _field(dow, 0, 7, "day of week", DAYS)},      # 7 is Sunday too
                days_any=dom == "*", weekdays_any=dow == "*")


def _day_list(days: Set[int]) -> str:
    order = [1, 2, 3, 4, 5, 6, 0]
    return ", ".join(DAY_NAMES[d] for d in order if d in days)


def describe(expr: str) -> str:
    """Plain English for the common shapes ("weekdays at 09:00"); the cron text otherwise."""
    try:
        c = parse(expr)
    except CronError:
        return expr
    text = MACROS.get(c.expr.lower(), c.expr)
    m, h, dom, mon, dow = text.split(" ")
    everyday = dom == "*" and mon == "*"
    if everyday and dow == "*" and h == "*" and m.startswith("*/"):
        return f"every {m[2:]} minutes"
    if everyday and dow == "*" and h == "*" and m.isdigit():
        return "every hour" + (f" at :{int(m):02d}" if int(m) else "")
    if m.isdigit() and mon == "*" and all(x.isdigit() for x in h.split(",")):
        times = " and ".join(f"{int(x):02d}:{int(m):02d}" for x in sorted(h.split(","), key=int))
        if dom == "*":
            if c.weekdays_any:
                return f"every day at {times}"
            if c.weekdays == {1, 2, 3, 4, 5}:
                return f"weekdays at {times}"
            if c.weekdays == {0, 6}:
                return f"weekends at {times}"
            return f"every {_day_list(c.weekdays)} at {times}"
        if dow == "*" and dom.isdigit():
            return f"on day {int(dom)} of every month at {times}"
    return f"cron {c.expr}"


def min_gap_minutes(c: Cron, now: datetime.datetime, sample: int = 60) -> float:
    """The shortest time between two runs over the next `sample` runs."""
    runs = c.next_runs(now, sample)
    return min(((b - a).total_seconds() / 60 for a, b in zip(runs, runs[1:])), default=float("inf"))


def validate(kind: str, expr: str, now: Optional[datetime.datetime] = None) -> Cron:
    """Parses and checks the schedule is allowed for its kind; raises CronError with a readable reason."""
    if kind not in KINDS:
        raise CronError(f"kind must be one of {', '.join(KINDS)}")
    c = parse(expr)
    gap = min_gap_minutes(c, now or datetime.datetime.now())
    if gap < MIN_GAP_MINUTES[kind]:
        raise CronError(f"'{expr}' runs every {gap:.0f} min; a {kind} schedule must be at least "
                        f"{MIN_GAP_MINUTES[kind]} min apart")
    return c


def local_iso(t: datetime.datetime) -> str:
    return t.astimezone().isoformat(timespec="seconds")


def timezone_name() -> str:
    return datetime.datetime.now().astimezone().tzname() or "local time"


# ---------------------------------------------------------------------------- store
class ScheduleStore:
    def __init__(self, db: DatabaseManager):
        self.db = db
        conn = db.get_connection()
        with conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,                 -- video | pins | report | reminder | task
                cron TEXT NOT NULL,                 -- 5 fields, the PC's local time
                payload TEXT NOT NULL DEFAULT '{}', -- video: post_to_tiktok, quote; pins: count; reminder: text; task: instruction, project
                enabled INTEGER NOT NULL DEFAULT 1,
                next_run_at TEXT,                   -- ISO 8601 with offset
                last_run_at TEXT,
                last_result TEXT,
                runs INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT ''
            );
            """)

    # -------------------------------------------------------------- reading
    @staticmethod
    def _row(r) -> Dict[str, Any]:
        s = dict(r)
        payload = json.loads(s.pop("payload") or "{}")
        s["enabled"] = bool(s["enabled"])
        s["description"] = describe(s["cron"])
        s["post_to_tiktok"] = bool(payload.get("post_to_tiktok"))
        s["quote"] = payload.get("quote")
        s["text"] = payload.get("text")
        s["instruction"] = payload.get("instruction")
        s["project"] = payload.get("project")
        s["count"] = payload.get("count")
        return s

    def get(self, schedule_id: int) -> Optional[Dict[str, Any]]:
        r = self.db.get_connection().execute("SELECT * FROM schedules WHERE id = ?", (int(schedule_id),)).fetchone()
        return self._row(r) if r else None

    def list(self) -> List[Dict[str, Any]]:
        rows = self.db.get_connection().execute("SELECT * FROM schedules ORDER BY enabled DESC, next_run_at, id").fetchall()
        return [self._row(r) for r in rows]

    # -------------------------------------------------------------- changing
    @staticmethod
    def _payload(kind: str, post_to_tiktok: bool = False, quote: Optional[str] = None, text: Optional[str] = None,
                 instruction: Optional[str] = None, project: Optional[str] = None,
                 count: Optional[int] = None) -> Dict[str, Any]:
        if kind == "video":
            return {"post_to_tiktok": bool(post_to_tiktok), "quote": (quote or "").strip() or None}
        if kind == "pins":
            try:
                n = int(count or 1)
            except (TypeError, ValueError):
                raise CronError(f"pins: count must be a number from 1 to {MAX_PINS_PER_RUN}")
            if not 1 <= n <= MAX_PINS_PER_RUN:
                raise CronError(f"pins: count must be from 1 to {MAX_PINS_PER_RUN}")
            # instruction is only the words DeskAI shows for it (the app lists other kinds by their instruction)
            return {"count": n, "instruction": f"Post {n} pin{'s' if n != 1 else ''} to Pinterest"}
        if kind == "report":
            return {"instruction": "Weekly numbers report (site, TikTok, Pinterest)"}      # shown in DeskAI
        if kind == "reminder":
            text = " ".join((text or "").split())
            if not text:
                raise CronError("a reminder needs its text")
            return {"text": text[:300]}
        instruction = (instruction or "").strip()
        if len(instruction) < 8:
            raise CronError("a task schedule needs its instruction (a sentence, at least 8 characters)")
        return {"instruction": instruction[:2000], "project": (project or "").strip() or None}

    def create(self, name: str, kind: str, cron: str, source: str = "", enabled: bool = True,
               **payload_fields) -> Dict[str, Any]:
        kind = (kind or "").strip().lower()
        c = validate(kind, cron)
        if len(self.list()) >= MAX_SCHEDULES:
            raise CronError(f"there are already {MAX_SCHEDULES} schedules; delete one first")
        payload = self._payload(kind, **payload_fields)
        name = " ".join((name or "").split())[:80] or {"video": "Video", "pins": "Pins", "report": "Weekly report",
                                                        "reminder": "Reminder", "task": "Task"}[kind]
        conn = self.db.get_connection()
        with conn:
            cur = conn.execute(
                "INSERT INTO schedules (name, kind, cron, payload, enabled, next_run_at, created_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, kind, c.expr, json.dumps(payload), int(bool(enabled)),
                 local_iso(c.next_after(datetime.datetime.now())), local_iso(datetime.datetime.now()), source))
        self.db.append_log("INFO", f"Schedule #{cur.lastrowid} '{name}' created ({kind}, {describe(c.expr)}"
                                   f"{', posts to TikTok' if payload.get('post_to_tiktok') else ''}) from {source or 'unknown'}")
        return self.get(cur.lastrowid)

    def update(self, schedule_id: int, name: Optional[str] = None, cron: Optional[str] = None,
               enabled: Optional[bool] = None, **payload_changes) -> Dict[str, Any]:
        s = self.get(schedule_id)
        if s is None:
            raise KeyError(f"there's no schedule #{schedule_id}")
        new_cron = s["cron"] if cron is None else cron
        c = validate(s["kind"], new_cron)
        fields = {k: s.get(k) for k in ("post_to_tiktok", "quote", "text", "instruction", "project", "count")}
        if s["kind"] in ("pins", "report"):
            fields.pop("instruction")                     # made by _payload
        fields.update({k: v for k, v in payload_changes.items() if v is not None})
        payload = self._payload(s["kind"], **fields)
        now_enabled = s["enabled"] if enabled is None else bool(enabled)
        # Re-plan from now when the time changes or the schedule is switched back on
        next_run = s["next_run_at"]
        if cron is not None or (now_enabled and not s["enabled"]) or not next_run:
            next_run = local_iso(c.next_after(datetime.datetime.now()))
        conn = self.db.get_connection()
        with conn:
            conn.execute("UPDATE schedules SET name = ?, cron = ?, payload = ?, enabled = ?, next_run_at = ? WHERE id = ?",
                         ((" ".join((name or "").split())[:80] or s["name"]) if name is not None else s["name"],
                          c.expr, json.dumps(payload), int(now_enabled), next_run, int(schedule_id)))
        return self.get(schedule_id)

    def delete(self, schedule_id: int) -> bool:
        conn = self.db.get_connection()
        with conn:
            cur = conn.execute("DELETE FROM schedules WHERE id = ?", (int(schedule_id),))
        return cur.rowcount > 0

    def _record_run(self, schedule_id: int, result: str, next_run: Optional[str], counted: bool) -> None:
        conn = self.db.get_connection()
        with conn:
            conn.execute("UPDATE schedules SET last_run_at = ?, last_result = ?, runs = runs + ?, "
                         "next_run_at = COALESCE(?, next_run_at) WHERE id = ?",
                         (local_iso(datetime.datetime.now()), result[:300], 1 if counted else 0, next_run,
                          int(schedule_id)))


# ---------------------------------------------------------------------------- running
def fire(db: DatabaseManager, s: Dict[str, Any], manual: bool = False) -> Dict[str, Any]:
    """Does what a schedule says, once. Returns {"result": text, "task_id": id or None, "ran": bool}."""
    source = f"schedule #{s['id']} '{s['name']}'" + (" (run now)" if manual else "")
    if s["kind"] == "reminder":
        sent = push.send("reminder", "⏰ Reminder", s["text"] or s["name"], channel="reminders")
        return {"result": f"reminder sent to {sent} device(s)" if sent else "reminder not delivered (no phone registered for push)",
                "task_id": None, "ran": True}
    if not manual and db.get_state("daemon_status", "idle") in ("paused", "stopped", "sleep"):
        return {"result": "skipped: the agent was in standby", "task_id": None, "ran": False}
    if s["kind"] == "video":
        task_id = actions.queue_video(db, s.get("quote"), post_to_tiktok=s["post_to_tiktok"], source=source)
    elif s["kind"] == "pins":
        task_id = actions.queue_pins(db, s.get("count") or 1, source=source)
    elif s["kind"] == "report":
        task_id = actions.queue_report(db, source=source)
    else:
        task_id = actions.queue_instruction(db, s["instruction"], s.get("project"), source=source)
    return {"result": f"started {task_id}", "task_id": task_id, "ran": True}


def run_now(db: DatabaseManager, schedule_id: int) -> Dict[str, Any]:
    """The Run now button: fires once (even in standby) without changing the next scheduled time."""
    store = ScheduleStore(db)
    s = store.get(schedule_id)
    if s is None:
        raise KeyError(f"there's no schedule #{schedule_id}")
    outcome = fire(db, s, manual=True)
    store._record_run(s["id"], outcome["result"], None, counted=outcome["ran"])
    db.append_log("INFO", f"Schedule #{s['id']} '{s['name']}' run now: {outcome['result']}")
    return outcome


LAST_PASS = 0.0       # time.time() of the runner's last pass (0 = not running in this process)


class ScheduleRunner:
    """Fires due schedules. One pass every `interval` seconds (a supervisor thread)."""

    def __init__(self, db: DatabaseManager, on_event: Optional[Callable[[str], None]] = None, interval: float = 20.0):
        self.store = ScheduleStore(db)
        self.db = db
        self.on_event = on_event          # e.g. MemoryManager.record_event, so the assistant knows what ran
        self.interval = interval
        self.running = True
        self._lock = threading.Lock()

    def _event(self, text: str) -> None:
        if self.on_event:
            try:
                self.on_event(text)
            except Exception as e:
                logger.warning(f"Could not record schedule event: {e}")

    def check_once(self, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
        """Fires every enabled schedule that is due. Returns what happened, one entry per schedule handled."""
        now = now or datetime.datetime.now()
        done = []
        held = maintenance.holding()    # a restart is waiting: work fires after it (MISSED_GRACE), reminders now
        with self._lock:
            for s in self.store.list():
                if not s["enabled"] or not s["next_run_at"] or (held and s["kind"] != "reminder"):
                    continue
                due = datetime.datetime.fromisoformat(s["next_run_at"]).astimezone().replace(tzinfo=None)
                if due > now:
                    continue
                c = parse(s["cron"])
                next_run = local_iso(c.next_after(now))
                if now - due > MISSED_GRACE:
                    result = f"missed at {due:%Y-%m-%d %H:%M} (the agent was off); next run {next_run[:16]}"
                    self.store._record_run(s["id"], result, next_run, counted=False)
                    self.db.append_log("WARNING", f"Schedule #{s['id']} '{s['name']}' {result}")
                    done.append({"id": s["id"], "result": result})
                    continue
                try:
                    outcome = fire(self.db, s)
                except Exception as e:
                    outcome = {"result": f"failed: {e}", "task_id": None, "ran": False}
                    logger.exception(f"Schedule #{s['id']} failed")
                self.store._record_run(s["id"], outcome["result"], next_run, counted=outcome["ran"])
                self.db.append_log("INFO", f"Schedule #{s['id']} '{s['name']}' ran: {outcome['result']}")
                what = {"video": "video" + (" + TikTok post" if s["post_to_tiktok"] else ""),
                        "pins": f"{s.get('count') or 1} Pinterest pin(s)", "report": "weekly report",
                        "reminder": "reminder", "task": "task"}[s["kind"]]
                self._event(f"Schedule '{s['name']}' ({what}, {s['description']}) ran: {outcome['result']}"
                            + (f" (reminder: {s['text']})" if s["kind"] == "reminder" else ""))
                done.append({"id": s["id"], **outcome})
        return done

    def _nightly(self) -> None:
        """VibeStats' nightly collection (Clarity only exports the last 1-3 days), in its own thread."""
        try:
            try:
                from core import weekly_report
            except ImportError:
                import weekly_report
            now = datetime.datetime.now()
            if now.time() >= weekly_report.NIGHTLY_AT and \
                    self.db.get_state(weekly_report.STATE_COLLECTED, "") != now.date().isoformat():
                threading.Thread(target=weekly_report.collect_nightly, args=(self.db,), name="NightlyStats",
                                 daemon=True).start()
        except Exception as e:
            logger.warning(f"Nightly stats not started: {e}")

    def run(self):
        global LAST_PASS
        while self.running:
            try:
                self.check_once()
                self._nightly()
            except Exception as e:
                logger.error(f"Schedule runner error: {e}")
            LAST_PASS = time.time()             # the external heartbeat checks the runner is still going
            time.sleep(self.interval)
