"""
External heartbeat (docs/IDEAS.md #1): a dead-man's switch outside the Mini.

Every alert the agent sends comes from the agent itself, so a dead Mini (power, network, a crash, a hang) is
silence, not an alarm. Every PING_EVERY_SECONDS the live daemon pings HEALTHCHECK_URL (a healthchecks.io-style
check); when the pings stop, that service emails or texts the owner. Before pinging, it checks that the API DeskAI
uses answers on this machine; if it doesn't, it pings <url>/fail, so "the agent runs but the app can't reach it"
alerts too. Nothing happens without HEALTHCHECK_URL in .env.
"""
import os
import threading
import time
import urllib.request

try:
    from core.config import get_config
except ImportError:
    from config import get_config  # type: ignore[no-redef]

PING_EVERY_SECONDS = 300
TIMEOUT = 10
# Ollama starts with the Windows session and loads its model in its own time: right after a restart the memory model
# "isn't answering" for a minute or two. Reported only after this grace (2026-10-27: a warning at every restart,
# 4 that week, none of them a real outage; AUDIT_BACKGROUND B7.5)
STARTUP_GRACE_SECONDS = 600
_STARTED = time.time()

_last_attempt = 0.0
_lock = threading.Lock()


def url() -> str:
    return (get_config("HEALTHCHECK_URL") or "").strip().rstrip("/")


def api_answers(port: int | None = None) -> bool:
    port = port or int(os.environ.get("HUD_PORT", "8080"))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=TIMEOUT) as r:
            return r.status == 200
    except OSError:
        return False


SCHEDULER_STALE_SECONDS = 180    # the runner passes every 20 s
DEGRADED_NOTICE_KEY = "heartbeat_degraded_notice"     # the day and problem last pushed, so it's once a day


def critical_problems(db=None, port: int | None = None) -> list[str]:
    """What makes the agent unusable (IDEAS #20): the API DeskAI uses, the database, the schedule runner. The
    daemon itself is fine: it's the one calling this."""
    problems = []
    if not api_answers(port):
        problems.append("the API DeskAI uses didn't answer")
    if db is not None:
        try:
            db.get_state("last_heartbeat", "")
        except Exception as e:
            problems.append(f"the database couldn't be read ({e})")
    try:
        from core import schedules
    except ImportError:
        import schedules  # type: ignore[no-redef]
    if schedules.LAST_PASS and time.time() - schedules.LAST_PASS > SCHEDULER_STALE_SECONDS:
        problems.append(f"the schedule runner has stopped (last pass {int(time.time() - schedules.LAST_PASS)} s ago)")
    return problems


def degraded_problems() -> list[str]:
    """Works, but worse: memory search without its model is keyword-only."""
    if time.time() - _STARTED < STARTUP_GRACE_SECONDS:
        return []
    try:
        from core.embedder import get_memory_embedder
    except ImportError:
        from embedder import get_memory_embedder  # type: ignore[no-redef]
    emb = get_memory_embedder()
    if hasattr(emb, "owner") and not emb.ensure_loaded():           # the Ollama model (bge-small can't be "down")
        return [(f"memory search's model ({getattr(emb, 'model', 'Ollama')}) isn't answering, so search is "
                f"keyword-only; is Ollama running on the Mini?")]
    return []


def _notify_degraded(db, problems: list[str], logger=None) -> None:
    """A push from the agent itself, once a day per problem (healthchecks.io is for the agent being down)."""
    if not problems or db is None:
        return
    key = f"{time.strftime('%Y-%m-%d')}|{problems[0][:60]}"
    if db.get_state(DEGRADED_NOTICE_KEY, "") == key:
        return
    db.set_state(DEGRADED_NOTICE_KEY, key)
    try:
        from core.config import notify_owner
    except ImportError:
        from config import notify_owner  # type: ignore[no-redef]
    notify_owner("⚠️ " + "; ".join(problems))
    if logger:
        logger.warning("Heartbeat: " + "; ".join(problems))


def ping(logger=None, port: int | None = None, db=None) -> str | None:
    """One check and ping; returns the URL pinged (None when not configured or the ping failed). A critical
    problem pings <url>/fail with a body naming it (healthchecks.io puts it in the alert)."""
    base = url()
    if not base:
        return None
    problems = critical_problems(db, port)
    target = f"{base}/fail" if problems else base
    body = ("; ".join(problems) or "ok").encode("utf-8")
    try:
        req = urllib.request.Request(target, data=body, headers={"User-Agent": "AlwaysOnAgent"})
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
    except Exception as e:
        if logger:
            logger.warning(f"External heartbeat ping failed: {e}")      # the service alerts if this goes on
        return None
    if logger and problems:
        logger.warning("External heartbeat reported a failure: " + "; ".join(problems))
    try:
        _notify_degraded(db, degraded_problems(), logger)
    except Exception as e:
        if logger:
            logger.debug(f"Heartbeat degraded check: {e}")
    return target


def maybe_ping(logger=None, db=None) -> threading.Thread | None:
    """Called every daemon heartbeat: starts a ping on its own thread when one is due."""
    global _last_attempt
    if not url():
        return None
    with _lock:
        now = time.monotonic()
        if _last_attempt and now - _last_attempt < PING_EVERY_SECONDS:
            return None
        _last_attempt = now
    t = threading.Thread(target=ping, args=(logger,), kwargs={"db": db}, name="HeartbeatPing", daemon=True)
    t.start()
    return t
