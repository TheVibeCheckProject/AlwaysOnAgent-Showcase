"""Take over for AgentComputerUse (core/computer_use/takeover.py, moved from core/computer_use_takeover.py §3 on 2026-11-04).

Take over and View only: the screen as JPEG frames, your taps and keys sent back, the
banner showing what a run is doing, and handing control back.

_TAKEOVER, HANDED_BACK and MACHINES live here and are re-exported by
core.computer_use. COMPUTER_USE_DIR and ComputerUseError are resolved from
core.computer_use at call time via _cu(), and _laptop is called via _cu() too, so
tests patching them on that module take effect.
"""

import json
import sys
from typing import Any

from .requests import _write_answer, get_request


def _cu():
    """core.computer_use, resolved at call time so test patches on it take effect."""
    import core.computer_use as _m
    return _m




# ---------------------------------------------------------------------------------------------------- Take over
# The owner uses the computer from their phone: a live picture of the screen, their taps and typing replayed with
# the real mouse and keyboard. A running task waits meanwhile; handing back answers the request it was waiting on
# with HANDED_BACK, so it looks at the screen again instead of acting (the tool's takeover.py).
HANDED_BACK = "__handed_back__"
_TAKEOVER: dict[str, Any] = {}


MACHINES = ("mini", "laptop")


def _takeover():
    """The tool's takeover module, loaded from its folder: the agent runs in the desktop session, so it can take
    the picture and use the mouse and keyboard itself (no program to start for every tap)."""
    path = _cu().COMPUTER_USE_DIR / "takeover.py"
    if not path.is_file():
        raise _cu().ComputerUseError("Take over isn't installed (update AgentComputerUse)")
    mod = _TAKEOVER.get(str(path))
    if mod is None:
        import importlib.util
        if str(_cu().COMPUTER_USE_DIR) not in sys.path:
            sys.path.append(str(_cu().COMPUTER_USE_DIR))      # last, so the agent's own modules always come first
        spec = importlib.util.spec_from_file_location("acu_takeover", path)
        if spec is None or spec.loader is None:
            raise _cu().ComputerUseError("Could not load takeover module specification")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TAKEOVER[str(path)] = mod
    return mod


def _laptop(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 15):
    """The laptop's own Take over (AgentComputerUse takeover-serve, started at its login), over Tailscale with its
    key (LAPTOP_TAKEOVER_URL / LAPTOP_TAKEOVER_TOKEN in .env). Returns (bytes, headers)."""
    import urllib.error
    import urllib.request
    try:
        from core.config import get_config
    except ImportError:
        from config import get_config  # type: ignore[no-redef]
    url, token = (get_config("LAPTOP_TAKEOVER_URL") or "").rstrip("/"), get_config("LAPTOP_TAKEOVER_TOKEN") or ""
    if not url or not token:
        raise _cu().ComputerUseError("The laptop isn't set up for Take over")
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
        raise _cu().ComputerUseError(msg)
    except (urllib.error.URLError, OSError) as e:
        raise _cu().ComputerUseError(f"The laptop isn't answering (asleep, off, or Tailscale is off): {e}")


def _machine(machine: str | None) -> str:
    m = (machine or "mini").strip().lower()
    if m not in MACHINES:
        raise _cu().ComputerUseError(f"no computer called {machine!r}")
    return m


def takeover_machines() -> list[dict[str, Any]]:
    """The computers Take over can show: the Mini always, the laptop once it's set up."""
    try:
        from core.config import get_config
    except ImportError:
        from config import get_config  # type: ignore[no-redef]
    out = [{"id": "mini", "name": "Mini"}]
    if get_config("LAPTOP_TAKEOVER_URL") and get_config("LAPTOP_TAKEOVER_TOKEN"):
        out.append({"id": "laptop", "name": "Laptop"})
    return out


def takeover_status(machine: str | None = None) -> dict[str, Any]:
    if _machine(machine) == "laptop":
        return json.loads(_cu()._laptop("GET", "/status")[0].decode("utf-8"))
    return _takeover().status()


def takeover_start(request_id: str | None = None, machine: str | None = None) -> dict[str, Any]:
    if _machine(machine) == "laptop":
        return json.loads(_cu()._laptop("POST", "/start", timeout=30)[0].decode("utf-8"))
    if request_id:
        req = get_request(request_id)
        if req is None or not req["pending"]:
            raise _cu().ComputerUseError("that request was already answered or has closed")
        request_id = req["id"]
    return _takeover().start(request_id)


def takeover_frame(machine: str | None = None, view_only: bool = False):
    """(JPEG bytes, info) of the screen now: while the owner is in control, or any time with view_only (DeskAI's
    "View only" box). Taking the picture moves nothing, so watching never takes control and a running task carries
    on (2026-10-08: opening Take over just to watch stopped a task)."""
    if _machine(machine) == "laptop":
        jpeg, headers = _cu()._laptop("GET", "/frame?view=1" if view_only else "/frame")
        w, _, h = (headers.get("X-Screen") or "0x0").partition("x")
        return jpeg, {"width": int(w or 0), "height": int(h or 0)}
    t = _takeover()
    if not view_only and not t.active():
        raise _cu().ComputerUseError("take over first")
    # view_only also tells the tool not to count the picture as the owner being active (2026-10-10: View only kept
    # a forgotten Take over alive, and a computer task waited 17 hours' worth of "in control" for a hand-back)
    return t.frame(view_only=True) if view_only else t.frame()


def takeover_activity(machine: str | None = None) -> dict[str, Any]:
    """What a computer-use run is doing now (goal, step, in plain words), for the banner on Take over's screen.
    Read-only: {"running": false} when nothing is running."""
    if _machine(machine) == "laptop":
        return json.loads(_cu()._laptop("GET", "/activity")[0].decode("utf-8"))
    t = _takeover()
    if not hasattr(t, "activity_now"):
        return {"running": False}                 # an AgentComputerUse from before the banner
    return t.activity_now()


def takeover_input(event: dict[str, Any], machine: str | None = None) -> str:
    if _machine(machine) == "laptop":
        return json.loads(_cu()._laptop("POST", "/input", event)[0].decode("utf-8")).get("done", "")
    t = _takeover()
    try:
        return t.apply(event)
    except t.TakeoverError as e:
        raise _cu().ComputerUseError(str(e))


def takeover_stop(request_id: str | None = None, machine: str | None = None) -> dict[str, Any]:
    """Hand back: control returns to the tool, and the request it was taken over for (if still waiting) is answered
    with HANDED_BACK so the task looks again."""
    if _machine(machine) == "laptop":
        _cu()._laptop("POST", "/stop")
        return {"active": False, "handed_back_to": None}
    rid = _takeover().release() or request_id
    handed_back_to = None
    if rid:
        try:
            req = get_request(rid)
        except _cu().ComputerUseError:
            req = None
        if req and req["pending"]:
            _write_answer(req["id"], HANDED_BACK)
            handed_back_to = req["id"]
    return {"active": False, "handed_back_to": handed_back_to}
