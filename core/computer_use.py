"""
Calling AgentComputerUse
========================
AgentComputerUse (COMPUTER_USE_DIR, a separate tool) uses the computer like a person: it opens its own Edge,
reads pages and apps through Windows' accessibility list plus OCR, and acts only with the real mouse and
keyboard. The agent runs it as its own program from inside the desktop session (the agent's own session),
reads the JSON lines it prints (progress to the task card, the last line is the result), and a cancel ends
the program. It never submits, sends, pays or posts on its own: those stop for the owner.
"""

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from core.config import COMPUTER_USE_DIR, get_safe_subprocess_env
except ImportError:
    from config import COMPUTER_USE_DIR, get_safe_subprocess_env

logger = logging.getLogger("ComputerUse")
# It needs to find Edge and run in the owner's profile; it reads the model keys from the agent's .env itself
DESKTOP_ENV = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles", "ProgramData",
               "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "USERNAME", "USERDOMAIN", "USERPROFILE",
               "LOCALAPPDATA", "APPDATA", "SystemRoot", "windir", "TEMP", "TMP")


class ComputerUseError(Exception):
    """The tool reported an error (its message is written for the owner)."""


def installed() -> bool:
    return (COMPUTER_USE_DIR / "agentcomputeruse.py").is_file()


# ---------------------------------------------------------------------------------------- the owner's answers
# The tool saves each request under its data/requests/<id>.json (+ <id>.png) and waits for data/answers/<id>.json
# secret: a password or code typed in the app; the tool types it straight into the field (the model never sees it).
# The agent never logs, stores (beyond the answer file the tool deletes on reading) or echoes it.
ANSWER_KINDS = ("approve", "choice", "text", "secret")
_REMINDER_EVENTS: Dict[str, Any] = {}     # request_id -> threading.Event
_REMINDER_SENDERS: Dict[str, Any] = {}    # request_id -> send callback
_TASK_REQUESTS: Dict[str, Any] = {}       # task_id -> set of request_ids


def _requests_dir():
    return COMPUTER_USE_DIR / "data" / "requests"


def _answers_dir():
    return COMPUTER_USE_DIR / "data" / "answers"


def _safe_id(request_id: str) -> str:
    rid = "".join(ch for ch in (request_id or "") if ch.isalnum())
    if not rid or len(rid) > 40:
        raise ComputerUseError("no such request")
    return rid


def get_request(request_id: str) -> Optional[Dict[str, Any]]:
    path = _requests_dir() / f"{_safe_id(request_id)}.json"
    if not path.is_file():
        return None
    try:
        req = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    req["answered"] = (_answers_dir() / f"{req['id']}.json").is_file()
    req["pending"] = not req.get("closed") and not req["answered"]
    return req


def close_request(request_id: str, reason: str = "closed", task_id: str = "",
                  send: Optional[Callable] = None) -> bool:
    """Closes a pending request on disk, terminates its reminder thread, and notifies DeskAI to dismiss."""
    import datetime
    try:
        rid = _safe_id(request_id)
    except Exception:
        return False
    ev = _REMINDER_EVENTS.pop(rid, None)
    if ev:
        ev.set()
    registered_send = _REMINDER_SENDERS.pop(rid, None)
    send = send or registered_send

    path = _requests_dir() / f"{rid}.json"
    if not path.is_file():
        return False
    try:
        req = json.loads(path.read_text(encoding="utf-8"))
        if not req.get("closed"):
            req["closed"] = datetime.datetime.now().isoformat(timespec="seconds")
            req["close_reason"] = reason
            path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    try:
        if send is None:
            try:
                from core import push
                send = push.send
            except ImportError:
                import push
                send = push.send
        send("needs_you_cancel", "Request dismissed", "", task_id, channel="needs_you",
             extra={"cancel_request_id": rid, "request_id": rid, "reason": reason})
    except Exception:
        pass
    return True


def cancel_for_task(task_id: str, reason: str = "task cancelled", send: Optional[Callable] = None) -> None:
    """Closes all pending requests and reminder threads associated with a task."""
    if not task_id:
        return
    rids = list(_TASK_REQUESTS.pop(task_id, set()))
    for rid in rids:
        close_request(rid, reason=reason, task_id=task_id, send=send)

    # Clean up any pending request on disk if this task is no longer in progress or no tasks active
    try:
        from core.database import DatabaseManager
        db = DatabaseManager()
        t = db.get_task(task_id)
        active = db.list_tasks(limit=1, phase="in_progress")
        if (t and (t.get("phase") != "in_progress" or t.get("cancelled"))) or not active:
            for req in pending_requests():
                close_request(req["id"], reason=reason, task_id=task_id, send=send)
    except Exception:
        pass


def pending_requests() -> List[Dict[str, Any]]:
    """Requests the tool is waiting on, newest first."""
    folder = _requests_dir()
    if not folder.is_dir():
        return []
    out = []
    for p in folder.glob("*.json"):
        req = get_request(p.stem)
        if req and req["pending"]:
            out.append(req)
    return sorted(out, key=lambda r: r.get("created", ""), reverse=True)


def screenshot_path(request_id: str):
    req = get_request(request_id)
    if not req or not req.get("screenshot"):
        return None
    path = _requests_dir() / req["screenshot"]
    return path if path.is_file() else None


def answer(request_id: str, value: str) -> Dict[str, Any]:
    """The owner's answer from the app, checked against the request, for the waiting tool."""
    req = get_request(request_id)
    if req is None:
        raise ComputerUseError("no such request")
    if not req["pending"]:
        raise ComputerUseError("that request was already answered or has closed")
    if req["kind"] == "secret":
        value = (value or "").strip("\r\n")              # exactly as typed (a password may have spaces)
        if not value or len(value) > 200:
            raise ComputerUseError("type it (up to 200 characters)")
    else:
        value = (value or "").strip()
    if req["kind"] == "approve":
        if value.lower() not in ("approve", "deny"):
            raise ComputerUseError("answer Approve or Deny")
        value = value.capitalize()
    elif req["kind"] == "choice":
        if value not in (req.get("choices") or []):
            raise ComputerUseError("pick one of the choices")
    elif req["kind"] != "secret" and (not value or len(value) > 1000):
        raise ComputerUseError("type an answer (up to 1,000 characters)")
    _write_answer(req["id"], value)
    return {"id": req["id"], "kind": req["kind"], "answer": "(sent)" if req["kind"] == "secret" else value}


def _write_answer(rid: str, value: str) -> None:
    _answers_dir().mkdir(parents=True, exist_ok=True)
    tmp = _answers_dir() / f"{rid}.json.tmp"
    tmp.write_text(json.dumps({"answer": value}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_answers_dir() / f"{rid}.json")       # the tool never reads half a file
    ev = _REMINDER_EVENTS.pop(rid, None)
    if ev:
        ev.set()


# ---------------------------------------------------------------------------------------------------- Take over
# The owner uses the computer from their phone: a live picture of the screen, their taps and typing replayed with
# the real mouse and keyboard. A running task waits meanwhile; handing back answers the request it was waiting on
# with HANDED_BACK, so it looks at the screen again instead of acting (the tool's takeover.py).
HANDED_BACK = "__handed_back__"
_TAKEOVER: Dict[str, Any] = {}


def _takeover():
    """The tool's takeover module, loaded from its folder: the agent runs in the desktop session, so it can take
    the picture and use the mouse and keyboard itself (no program to start for every tap)."""
    path = COMPUTER_USE_DIR / "takeover.py"
    if not path.is_file():
        raise ComputerUseError("Take over isn't installed (update AgentComputerUse)")
    mod = _TAKEOVER.get(str(path))
    if mod is None:
        import importlib.util
        if str(COMPUTER_USE_DIR) not in sys.path:
            sys.path.append(str(COMPUTER_USE_DIR))      # last, so the agent's own modules always come first
        spec = importlib.util.spec_from_file_location("acu_takeover", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TAKEOVER[str(path)] = mod
    return mod


MACHINES = ("mini", "laptop")


def _laptop(method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 15):
    """The laptop's own Take over (AgentComputerUse takeover-serve, started at its login), over Tailscale with its
    key (LAPTOP_TAKEOVER_URL / LAPTOP_TAKEOVER_TOKEN in .env). Returns (bytes, headers)."""
    import urllib.error
    import urllib.request
    try:
        from core.config import get_config
    except ImportError:
        from config import get_config
    url, token = (get_config("LAPTOP_TAKEOVER_URL") or "").rstrip("/"), get_config("LAPTOP_TAKEOVER_TOKEN") or ""
    if not url or not token:
        raise ComputerUseError("The laptop isn't set up for Take over")
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url + path, data=data, method=method,
                                 headers={"X-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error") or f"HTTP {e.code}"
        except Exception:
            msg = f"HTTP {e.code}"
        raise ComputerUseError(msg)
    except (urllib.error.URLError, OSError) as e:
        raise ComputerUseError(f"The laptop isn't answering (asleep, off, or Tailscale is off): {e}")


def _machine(machine: Optional[str]) -> str:
    m = (machine or "mini").strip().lower()
    if m not in MACHINES:
        raise ComputerUseError(f"no computer called {machine!r}")
    return m


def takeover_machines() -> List[Dict[str, Any]]:
    """The computers Take over can show: the Mini always, the laptop once it's set up."""
    try:
        from core.config import get_config
    except ImportError:
        from config import get_config
    out = [{"id": "mini", "name": "Mini"}]
    if get_config("LAPTOP_TAKEOVER_URL") and get_config("LAPTOP_TAKEOVER_TOKEN"):
        out.append({"id": "laptop", "name": "Laptop"})
    return out


def takeover_status(machine: Optional[str] = None) -> Dict[str, Any]:
    if _machine(machine) == "laptop":
        return json.loads(_laptop("GET", "/status")[0].decode("utf-8"))
    return _takeover().status()


def takeover_start(request_id: Optional[str] = None, machine: Optional[str] = None) -> Dict[str, Any]:
    if _machine(machine) == "laptop":
        return json.loads(_laptop("POST", "/start", timeout=30)[0].decode("utf-8"))
    if request_id:
        req = get_request(request_id)
        if req is None or not req["pending"]:
            raise ComputerUseError("that request was already answered or has closed")
        request_id = req["id"]
    return _takeover().start(request_id)


def takeover_frame(machine: Optional[str] = None, view_only: bool = False):
    """(JPEG bytes, info) of the screen now: while the owner is in control, or any time with view_only (DeskAI's
    "View only" box). Taking the picture moves nothing, so watching never takes control and a running task carries
    on (2026-10-08: opening Take over just to watch stopped a task)."""
    if _machine(machine) == "laptop":
        jpeg, headers = _laptop("GET", "/frame?view=1" if view_only else "/frame")
        w, _, h = (headers.get("X-Screen") or "0x0").partition("x")
        return jpeg, {"width": int(w or 0), "height": int(h or 0)}
    t = _takeover()
    if not view_only and not t.active():
        raise ComputerUseError("take over first")
    # view_only also tells the tool not to count the picture as the owner being active (2026-10-10: View only kept
    # a forgotten Take over alive, and a computer task waited 17 hours' worth of "in control" for a hand-back)
    return t.frame(view_only=True) if view_only else t.frame()


def takeover_activity(machine: Optional[str] = None) -> Dict[str, Any]:
    """What a computer-use run is doing now (goal, step, in plain words), for the banner on Take over's screen.
    Read-only: {"running": false} when nothing is running."""
    if _machine(machine) == "laptop":
        return json.loads(_laptop("GET", "/activity")[0].decode("utf-8"))
    t = _takeover()
    if not hasattr(t, "activity_now"):
        return {"running": False}                 # an AgentComputerUse from before the banner
    return t.activity_now()


def takeover_input(event: Dict[str, Any], machine: Optional[str] = None) -> str:
    if _machine(machine) == "laptop":
        return json.loads(_laptop("POST", "/input", event)[0].decode("utf-8")).get("done", "")
    t = _takeover()
    try:
        return t.apply(event)
    except t.TakeoverError as e:
        raise ComputerUseError(str(e))


def takeover_stop(request_id: Optional[str] = None, machine: Optional[str] = None) -> Dict[str, Any]:
    """Hand back: control returns to the tool, and the request it was taken over for (if still waiting) is answered
    with HANDED_BACK so the task looks again."""
    if _machine(machine) == "laptop":
        _laptop("POST", "/stop")
        return {"active": False, "handed_back_to": None}
    rid = _takeover().release() or request_id
    handed_back_to = None
    if rid:
        try:
            req = get_request(rid)
        except ComputerUseError:
            req = None
        if req and req["pending"]:
            _write_answer(req["id"], HANDED_BACK)
            handed_back_to = req["id"]
    return {"active": False, "handed_back_to": handed_back_to}


REMINDERS = (60, 60, 60, 60, 60, 600, 600)     # seconds between alerts; the tool pauses after 30 minutes anyway


def alert_text(req: Dict[str, Any]) -> tuple:
    site = ""
    if req.get("url"):
        site = req["url"].split("//", 1)[-1].split("/", 1)[0]
    where = f" ({site})" if site else ""
    q = (req.get("question") or "").strip()
    if req.get("kind") == "approve":
        return f"Approve?{where}", q
    if req.get("kind") == "secret":
        return f"Type it in DeskAI{where}", f"{q}. Open DeskAI to type it; it goes straight into the box, not to the AI."
    if req.get("kind") == "choice":
        ql = q.lower()
        if "phone" in ql or "sign" in ql or "google" in ql or "password" in ql:
            return f"Sign-in verification needed{where}", q
        return f"A choice is needed{where}", q
    return f"The computer task needs you{where}", q


def alert_owner(req: Dict[str, Any], task_id: str = "", send: Optional[Callable] = None,
                sleep: Callable[[float], None] = None, db: Any = None) -> None:
    """The loud "Needs you" alert (its own DeskAI channel) with the request's screenshot, then reminders until
    the owner answers or the request closes. The screenshot is fetched by the app from
    /api/computer/requests/<id>/screenshot (the push itself carries only text)."""
    import threading
    import time as _time
    if send is None:
        try:
            from core import push
        except ImportError:
            import push
        send = push.send
    custom_sleep = sleep is not None
    sleep = sleep or _time.sleep
    title, body = alert_text(req)
    rid = req.get("id")
    extra = {"request_id": rid or "", "request_kind": req.get("kind", ""),
             "choices": json.dumps(req.get("choices") or [], ensure_ascii=False),
             "screenshot": f"/api/computer/requests/{rid}/screenshot" if req.get("screenshot") else "",
             "url": req.get("url") or ""}
    send("needs_you", title, body, task_id, channel="needs_you", extra=extra)

    stop_event = threading.Event()
    if rid:
        _REMINDER_EVENTS[rid] = stop_event
        if send:
            _REMINDER_SENDERS[rid] = send
    if task_id and rid:
        _TASK_REQUESTS.setdefault(task_id, set()).add(rid)

    def remind():
        try:
            for n, wait in enumerate(REMINDERS, 1):
                if custom_sleep:
                    sleep(wait)
                    if stop_event.is_set():
                        return
                else:
                    if stop_event.wait(wait):
                        return
                current = get_request(rid)
                if not current or not current.get("pending"):
                    return
                if task_id:
                    try:
                        database = db
                        if database is None:
                            from core.database import DatabaseManager
                            database = DatabaseManager()
                        t = database.get_task(task_id)
                        if t and t.get("engine") == "computer" and (t.get("phase") in ("completed", "failed", "cancelled") or t.get("cancelled")):
                            close_request(rid, reason="task inactive", task_id=task_id, send=send)
                            return
                    except Exception:
                        pass
                send("needs_you", "Reminder: " + title, body, task_id, channel="needs_you", extra={**extra, "reminder": str(n)})
        finally:
            if rid:
                _REMINDER_EVENTS.pop(rid, None)
                _REMINDER_SENDERS.pop(rid, None)
                if task_id and task_id in _TASK_REQUESTS:
                    _TASK_REQUESTS[task_id].discard(rid)

    threading.Thread(target=remind, daemon=True, name=f"needs-you-{rid}").start()


def run(args: List[str], on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_start: Optional[Callable[[int], None]] = None, timeout: float = 1200,
        on_needs_you: Optional[Callable[[Dict[str, Any]], None]] = None,
        input_text: Optional[str] = None) -> Dict[str, Any]:
    """Runs `agentcomputeruse.py <args>`; progress events go to on_progress, the process id to on_start (a cancel
    ends it). Returns the final "done" event; raises ComputerUseError on an "error" event or no result."""
    import os
    script = COMPUTER_USE_DIR / "agentcomputeruse.py"
    if not script.is_file():
        raise ComputerUseError(f"AgentComputerUse isn't installed at {COMPUTER_USE_DIR}")
    env = get_safe_subprocess_env({k: os.environ[k] for k in DESKTOP_ENV if k in os.environ})
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, "-u", str(script), *args], cwd=str(COMPUTER_USE_DIR), env=env,
                            stdin=subprocess.PIPE if input_text is not None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if input_text is not None and proc.stdin:
        try:
            proc.stdin.write(input_text)
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass
    if on_start:
        on_start(proc.pid)
    final: Optional[Dict[str, Any]] = None
    try:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.pop("event", "")
            if kind == "progress":
                if on_progress:
                    try:
                        on_progress(event)
                    except Exception as e:           # progress is cosmetic
                        logger.debug(f"progress callback failed: {e}")
            elif kind == "needs_you":
                if on_needs_you:
                    try:
                        on_needs_you(event)
                    except Exception as e:
                        logger.warning(f"needs_you callback failed: {e}")
            elif kind in ("done", "error"):
                final = {"event": kind, **event}
        proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
    if final is None:
        raise ComputerUseError(f"AgentComputerUse stopped without a result (exit code {proc.returncode})")
    if final["event"] == "error":
        raise ComputerUseError(final.get("message") or "AgentComputerUse failed")
    final.pop("event")
    return final


def describe(result: Dict[str, Any]) -> str:
    """The result in words for the task card and the chat, with where its step screenshots are. 2026-10-26: the
    run id wasn't in the result, so asked where task-233's screenshots went, the chat guessed a folder."""
    text = _status_words(result)
    run_id = str(result.get("run") or "")
    shots = run_screenshots(run_id)
    if shots:
        text += f"\nScreenshots: {len(shots)}, in {COMPUTER_USE_DIR / 'data' / 'runs' / run_id}"
    return text


# ------------------------------------------------------------------------------------------- run screenshots
# Every run keeps a screenshot per step (AgentComputerUse steplog.py: data/runs/<run id>/step-NN.jpg, snap-NNN.jpg).
# 2026-10-27, Alex: "I thought the assistant can view the stuff on the mini so it should be able to show the
# images". It couldn't: its file tools see only the agent's own code, so asked for task-233's screenshots it named
# folders that didn't exist. The chat tool computer_screenshots finds them; GET /api/computer/runs/... serves them.
# (Not screenshot_path: that name is the Needs-you request's screenshot, above.)
RUN_ID = re.compile(r"[0-9]{8}-[0-9]{6}")
SHOT_NAME = re.compile(r"(step-[0-9]{2,3}|snap-[0-9]{3})\.jpg")


def run_screenshots(run_id: str) -> List[str]:
    """The run's screenshot file names in step order ([] for an unknown or odd id)."""
    if not RUN_ID.fullmatch(run_id or ""):
        return []
    folder = COMPUTER_USE_DIR / "data" / "runs" / run_id
    if not folder.is_dir():
        return []
    names = [p.name for p in folder.iterdir() if SHOT_NAME.fullmatch(p.name)]
    return sorted(names, key=lambda n: (int(re.search(r"\d+", n).group()), n))


def run_screenshot_path(run_id: str, name: str) -> Optional[Path]:
    if not RUN_ID.fullmatch(run_id or "") or not SHOT_NAME.fullmatch(name or ""):
        return None
    p = COMPUTER_USE_DIR / "data" / "runs" / run_id / name
    return p if p.is_file() else None


def run_for_task(task: Optional[Dict[str, Any]]) -> Optional[str]:
    """The run id a computer task recorded (its result's "run")."""
    meta = (task or {}).get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    run_id = str(((meta.get("result") or {}) if isinstance(meta, dict) else {}).get("run") or "")
    return run_id if RUN_ID.fullmatch(run_id) else None


def latest_runs(limit: int = 5) -> List[str]:
    folder = COMPUTER_USE_DIR / "data" / "runs"
    if not folder.is_dir():
        return []
    return sorted((p.name for p in folder.iterdir() if p.is_dir() and RUN_ID.fullmatch(p.name)), reverse=True)[:limit]


def _status_words(result: Dict[str, Any]) -> str:
    status = result.get("status")
    if status == "done":
        quotes = "; ".join(f'"{q}"' for q in (result.get("evidence") or [])[:3])
        # A retask: the same task done cleanly before, its steps repeated without the AI (the tool's retasks.py)
        n = result.get("requests") or 0
        repeat = (f"\n(Repeated an earlier run: {n} AI request{'' if n == 1 else 's'}.)"
                  if (result.get("detail") or {}).get("retask") else "")
        return f"{result.get('answer')}" + (f"\nFrom the page: {quotes}" if quotes else "") + repeat
    if status == "needs_approval":
        d = result.get("detail") or {}
        return (f"Stopped before {result.get('question') or 'a step that needs your OK'}"
                + (f" ({d.get('item')})" if d.get("item") else "") + ". Nothing was submitted.")
    if status == "needs_owner":
        return f"Needs you: {result.get('question') or 'it got stuck'}"
    if status == "stopped":
        return f"Stopped: {(result.get('detail') or {}).get('reason') or 'stopped'}"
    if status == "out_of_steps":
        return "Ran out of steps before finishing."
    return f"Couldn't finish: {(result.get('detail') or {}).get('reason') or status}"


# ---------------------------------------------------------------------------------------------------- retasks
# A clean run the tool saved, so the SAME task asked again is repeated without the AI (the tool's retasks.py).
# The owner sees them in DeskAI and can delete one.

def list_retasks() -> List[Dict[str, Any]]:
    return run(["retasks"], timeout=60).get("retasks") or []


def forget_retask(rid: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{12}", rid or ""):
        raise ComputerUseError("that isn't a retask id")
    return bool(run(["forget-retask", rid], timeout=60).get("forgotten"))


# ------------------------------------------------------------------------------------------------ credentials
# Stored in Windows Credential Manager under the host desktop session (AgentComputerUse/credentials.py).
# Stored with DPAPI encryption; passwords are never sent in logs, task prompts, or GET API responses.

def list_passwords() -> List[Dict[str, str]]:
    return run(["passwords"], timeout=60).get("saved") or []


def save_password(domain: str, username: str, password: str) -> Dict[str, Any]:
    if not domain or not username or not password:
        raise ComputerUseError("domain, username, and password are required")
    if "\n" in password or "\r" in password:
        raise ComputerUseError("a password can't contain a line break")
    # Saved exactly as given: a leading or trailing space is part of a password (2026-10-27, no strip)
    return run(["save-password", domain.strip(), username.strip()], input_text=password + "\n", timeout=60)


def forget_password(domain: str, username: str) -> bool:
    if not domain or not username:
        raise ComputerUseError("domain and username are required")
    return bool(run(["forget-password", domain.strip(), username.strip()], timeout=60).get("forgotten"))

