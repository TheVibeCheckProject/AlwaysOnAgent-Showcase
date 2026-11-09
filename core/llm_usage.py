"""
LLM token usage log
===================
Every cloud AI call (Groq, Cloudflare Workers AI, Mistral) records its provider, model and token
counts in the llm_usage table (data/state.db), including how many prompt tokens were served from
the provider's prompt cache. Cached tokens don't count toward Groq's rate limits, so "counted"
tokens (prompt minus cached) are what limit how much memory the assistant can afford to send.

Callers label what a call is for with `with usage_purpose("chat", detail={...}):`. The label is
thread-local, so chat and background memory work running on other threads don't mix.
"""

import contextlib
import datetime
import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from typing import Any

try:
    from core.database import DatabaseManager, get_iso_now
except ImportError:
    from database import DatabaseManager, get_iso_now  # type: ignore[no-redef]

logger = logging.getLogger("AlwaysOnAgent.LlmUsage")

_ctx = threading.local()

# Why a call gave no answer, as llm_failures.kind
FAILURE_KINDS = {
    "overloaded": "the provider was overloaded (HTTP 5xx, after one retry)",
    "timeout": "no answer in time",
    "rate_limit": "a per-minute limit",
    "quota_day": "the daily quota ran out",
    "garbage": "a garbled or empty reply",
    "error": "another error",
}
QUOTA_WARN_AT = 0.8      # warn once a day when a model has used this share of the most calls it has managed in a day
NOTIFY: Callable[[str], Any] | None = None    # set by the live agent (core.config.notify_owner); off in tests/scripts


@contextlib.contextmanager
def usage_purpose(purpose: str, detail: dict[str, Any] | None = None) -> Iterator[None]:
    """Labels the LLM calls made inside the block (e.g. 'chat', 'extract', 'task')."""
    prev = getattr(_ctx, "value", None)
    _ctx.value = (purpose, detail)
    try:
        yield
    finally:
        _ctx.value = prev


def current_purpose() -> tuple:
    return getattr(_ctx, "value", None) or ("other", None)


class UsageLog:
    KEEP_DAYS = 30
    PRUNE_EVERY = 200      # inserts between deletions of rows older than KEEP_DAYS

    def __init__(self, db: DatabaseManager):
        self.db = db
        conn = db.get_connection()
        with conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                model TEXT NOT NULL,
                purpose TEXT NOT NULL,
                step INTEGER NOT NULL DEFAULT 0,       -- 0 = first call of a turn, n = after n rounds of tool results
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                cached_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                remaining_tokens INTEGER,              -- Groq's x-ratelimit-remaining-tokens after this call
                duration_ms INTEGER,
                detail TEXT                            -- JSON, e.g. the prompt's token breakdown by section
            );
            CREATE INDEX IF NOT EXISTS idx_llm_usage_at ON llm_usage (at);
            CREATE TABLE IF NOT EXISTS llm_failures (       -- calls that didn't give an answer (IDEAS #12)
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                purpose TEXT NOT NULL,
                kind TEXT NOT NULL,                    -- see FAILURE_KINDS
                duration_ms INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_llm_failures_at ON llm_failures (at);
            """)
            # Added later: which provider served the call (every earlier row was Groq)
            if "provider" not in {r[1] for r in conn.execute("PRAGMA table_info(llm_usage)")}:
                conn.execute("ALTER TABLE llm_usage ADD COLUMN provider TEXT NOT NULL DEFAULT 'groq'")

    def record(self, model: str, usage: dict[str, Any] | None, step: int = 0,
               duration_ms: int | None = None, remaining_tokens: int | None = None,
               provider: str = "groq") -> None:
        """Stores one call's usage (from the API response's `usage`). Never raises."""
        try:
            usage = usage or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            purpose, detail = current_purpose()
            conn = self.db.get_connection()
            with conn:
                cur = conn.execute(
                    "INSERT INTO llm_usage (at, provider, model, purpose, step, prompt_tokens, cached_tokens, completion_tokens, "
                    "reasoning_tokens, remaining_tokens, duration_ms, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (get_iso_now(), provider, model, purpose, int(step), int(usage.get("prompt_tokens") or 0),
                     int(prompt_details.get("cached_tokens") or 0), int(usage.get("completion_tokens") or 0),
                     int(completion_details.get("reasoning_tokens") or 0), remaining_tokens, duration_ms,
                     json.dumps(detail) if detail and step == 0 else None))
                if cur.lastrowid is not None and cur.lastrowid % self.PRUNE_EVERY == 0:
                    cutoff = (datetime.datetime.now().astimezone() - datetime.timedelta(days=self.KEEP_DAYS)).isoformat()
                    conn.execute("DELETE FROM llm_usage WHERE at < ?", (cutoff,))
                    conn.execute("DELETE FROM llm_failures WHERE at < ?", (cutoff,))
            if provider == "gemini":
                self._maybe_warn_quota(provider, model)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            pass

    # ------------------------------------------------------------------ failures and daily quota (IDEAS #12)
    def record_failure(self, provider: str, model: str, kind: str, duration_ms: int | None = None) -> None:
        """One call that gave no answer, and why (a FAILURE_KINDS key). Never raises."""
        try:
            kind = kind if kind in FAILURE_KINDS else "error"
            conn = self.db.get_connection()
            with conn:
                conn.execute("INSERT INTO llm_failures (at, provider, model, purpose, kind, duration_ms) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             (get_iso_now(), provider, model, current_purpose()[0], kind, duration_ms))
            if kind == "quota_day":
                self._quota_ran_out(provider, model)
        except (OSError, sqlite3.Error):
            pass

    def calls_since_reset(self, provider: str, model: str) -> int:
        """Answered calls to a Gemini model since its daily quota reset (00:00 US Pacific). Only this agent's
        calls: other programs on the same key (test scripts, ImageGen) aren't counted."""
        start = _pacific_now().replace(hour=0, minute=0, second=0, microsecond=0).astimezone().isoformat()
        return self.db.get_connection().execute(
            "SELECT COUNT(*) FROM llm_usage WHERE provider = ? AND model = ? AND at >= ?",
            (provider, model, start)).fetchone()[0]

    def daily_limit(self, provider: str, model: str) -> int:
        """The most calls the model has answered in a day before Google said no more (0 = never run out).
        Google doesn't report the limit or what's left, so it's learned. The highest count is kept: a day that
        ran out early because other programs shared the key would otherwise lower it."""
        return int(self.db.get_state(f"daily_limit:{provider}:{model}", "0") or 0)

    def _quota_ran_out(self, provider: str, model: str) -> None:
        used = self.calls_since_reset(provider, model)
        if used > self.daily_limit(provider, model):
            self.db.set_state(f"daily_limit:{provider}:{model}", str(used))
        self._notify_once(f"quota_out:{provider}:{model}",
                          f"⚠️ {model} has used up today's quota after {used} calls from the agent. Replies come "
                          f"from the next model until it resets at 00:00 Pacific ({_hours_to_pacific_midnight()}).")

    def _maybe_warn_quota(self, provider: str, model: str) -> None:
        limit = self.daily_limit(provider, model)
        if not limit:
            return
        used = self.calls_since_reset(provider, model)
        if used >= QUOTA_WARN_AT * limit:
            self._notify_once(f"quota_warn:{provider}:{model}",
                              f"Heads up: {model} has answered {used} calls today; it has run out at about {limit} "
                              f"before. After that, replies come from the next model until 00:00 Pacific "
                              f"({_hours_to_pacific_midnight()}).")

    def _notify_once(self, key: str, text: str) -> None:
        """One message per key per quota day, and only from the live agent (NOTIFY set)."""
        today = _pacific_now().date().isoformat()
        if NOTIFY is None or self.db.get_state(key, "") == today:
            return
        self.db.set_state(key, today)
        try:
            NOTIFY(text)
        except Exception as e:
            logger.debug(f"quota notification failed: {e}")

    def model_health(self, hours: int = 24, best: str | None = None) -> dict[str, Any]:
        """How chat fared: replies per model (`best` = the route chat tries first, e.g. "gemini:gemini-3.8-flash"),
        the share the best model answered, average reply time, and the failures per model and kind."""
        since = (datetime.datetime.now().astimezone() - datetime.timedelta(hours=hours)).isoformat()
        conn = self.db.get_connection()
        replies = [dict(r) for r in conn.execute(
            "SELECT provider, model, COUNT(*) AS replies, CAST(AVG(duration_ms) AS INTEGER) AS avg_ms FROM llm_usage "
            "WHERE at >= ? AND purpose = 'chat' AND step = 0 GROUP BY provider, model ORDER BY replies DESC",
            (since,)).fetchall()]
        failures = [dict(r) for r in conn.execute(
            "SELECT provider, model, kind, COUNT(*) AS n FROM llm_failures WHERE at >= ? "
            "GROUP BY provider, model, kind ORDER BY n DESC", (since,)).fetchall()]
        total = sum(r["replies"] for r in replies)
        on_best = sum(r["replies"] for r in replies if best and f"{r['provider']}:{r['model']}" == best)
        return {"hours": hours, "best": best, "chat_replies": total,
                "best_share": round(on_best / total, 2) if total and best else None,
                "replies_by_model": replies, "failures": failures}

    def health_note(self, best: str, hours: int = 24, min_replies: int = 5, ok_share: float = 0.7) -> str | None:
        """One plain line for the morning brief when chat mostly missed its best model, with why; else None."""
        h = self.model_health(hours, best)
        if h["chat_replies"] < min_replies or (h["best_share"] or 0) >= ok_share:
            return None
        _, model = best.split(":", 1) if ":" in best else ("", best)
        why = [f"{FAILURE_KINDS[f['kind']]} {f['n']}x" for f in h["failures"]
               if f"{f['provider']}:{f['model']}" == best]
        on_best = round((h["best_share"] or 0) * h["chat_replies"])
        return (f"In the last {hours} hours only {on_best} of {h['chat_replies']} chat replies came from the best "
                f"model ({model}); the rest used a backup model." + (f" Why: {', '.join(why)}." if why else ""))

    def summary(self, hours: int = 24) -> dict[str, Any]:
        """Totals per purpose over the last `hours`, the overall cache hit rate, and the latest chat breakdown."""
        since = (datetime.datetime.now().astimezone() - datetime.timedelta(hours=hours)).isoformat()
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT purpose, COUNT(*) AS calls, SUM(prompt_tokens) AS prompt, SUM(cached_tokens) AS cached, "
            "SUM(completion_tokens) AS completion FROM llm_usage WHERE at >= ? GROUP BY purpose ORDER BY prompt DESC",
            (since,)).fetchall()
        by_purpose = [{**dict(r), "counted": (r["prompt"] or 0) - (r["cached"] or 0)} for r in rows]
        prompt = sum(r["prompt"] or 0 for r in by_purpose)
        cached = sum(r["cached"] or 0 for r in by_purpose)
        by_model = [{**dict(r), "counted": (r["prompt"] or 0) - (r["cached"] or 0)} for r in conn.execute(
            "SELECT provider, model, COUNT(*) AS calls, SUM(prompt_tokens) AS prompt, SUM(cached_tokens) AS cached "
            "FROM llm_usage WHERE at >= ? GROUP BY provider, model ORDER BY calls DESC", (since,)).fetchall()]
        chat = conn.execute(
            "SELECT COUNT(*) AS turns, AVG(prompt_tokens) AS prompt, AVG(prompt_tokens - cached_tokens) AS counted "
            "FROM llm_usage WHERE at >= ? AND purpose = 'chat' AND step = 0", (since,)).fetchone()
        latest = conn.execute(
            "SELECT at, provider, model, prompt_tokens, cached_tokens, detail FROM llm_usage "
            "WHERE purpose = 'chat' AND detail IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "hours": hours,
            "calls": sum(r["calls"] for r in by_purpose),
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "counted_tokens": prompt - cached,
            "cache_hit_rate": round(cached / prompt, 3) if prompt else None,
            "chat_turns": chat["turns"] or 0,
            "chat_avg_prompt": round(chat["prompt"]) if chat["prompt"] is not None else None,
            "chat_avg_counted": round(chat["counted"]) if chat["counted"] is not None else None,
            "by_purpose": by_purpose,
            "by_model": by_model,
            "latest_chat": ({"at": latest["at"], "provider": latest["provider"], "model": latest["model"],
                             "prompt_tokens": latest["prompt_tokens"],
                             "cached_tokens": latest["cached_tokens"], "sections": json.loads(latest["detail"])}
                            if latest else None),
        }

    def daily_budget_summary(self) -> dict[str, Any]:
        """Detailed breakdown of today's free AI usage per model, aligned to provider daily resets."""
        now_local = datetime.datetime.now().astimezone()
        today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        now_pac = _pacific_now()       # Gemini's daily quotas reset at midnight US Pacific

        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT provider, model, COUNT(*) AS calls, SUM(prompt_tokens) AS prompt, "
            "SUM(cached_tokens) AS cached, SUM(completion_tokens) AS completion "
            "FROM llm_usage WHERE at >= ? GROUP BY provider, model ORDER BY calls DESC",
            (today_local,)
        ).fetchall()

        by_model = [{**dict(r), "counted": (r["prompt"] or 0) - (r["cached"] or 0)} for r in rows]

        # Calculate seconds until next resets
        to_midnight_pac = ((now_pac + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) - now_pac).total_seconds()
        now_utc = datetime.datetime.now(datetime.UTC)
        to_midnight_utc = ((now_utc + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) - now_utc).total_seconds()

        return {
            "today_local": today_local,
            "calls_today": sum(r["calls"] for r in by_model),
            "tokens_today": sum((r["prompt"] or 0) + (r["completion"] or 0) for r in by_model),
            "by_model": by_model,
            "resets": {
                "google_gemini_pacific": {
                    "reset_time": "00:00 US/Pacific",
                    "seconds_remaining": int(to_midnight_pac),
                    "hours_remaining": round(to_midnight_pac / 3600, 1),
                },
                "cloudflare_utc": {
                    "reset_time": "00:00 UTC",
                    "seconds_remaining": int(to_midnight_utc),
                    "hours_remaining": round(to_midnight_utc / 3600, 1),
                }
            }
        }



def _pacific_now() -> datetime.datetime:
    try:
        import zoneinfo
        return datetime.datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
    except (ImportError, KeyError):
        now_utc = datetime.datetime.now(datetime.UTC)
        offset = -7 if (3, 8) <= (now_utc.month, now_utc.day) <= (11, 1) else -8
        return now_utc.astimezone(datetime.timezone(datetime.timedelta(hours=offset)))


def _hours_to_pacific_midnight() -> str:
    now = _pacific_now()
    left = ((now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) - now).total_seconds()
    return f"in {left / 3600:.0f} h" if left >= 5400 else f"in {left / 60:.0f} min"
