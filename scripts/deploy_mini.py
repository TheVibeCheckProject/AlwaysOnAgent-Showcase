"""Deploys AlwaysOnAgent on the Mini: pull, the full test suite, restart, health check. Runs ON the Mini.

From the laptop (after committing and pushing). Pipe it to the Mini: run directly on the laptop it pulls and
tests the laptop's copy, and every test errors on the Mini-only temp folder (2026-10-27):

    python scripts/mini.py py < scripts/deploy_mini.py

What it does, in order (it stops at the first failure and says why):
  1. Shows `git status` of the live repo. It must be clean: uncommitted edits on the Mini block pulls and
     self-updates. Never edit files on the Mini directly.
  2. Pulls with scripts/pull_task.ps1. Plain `git pull` over SSH fails (an SSH session can't reach the Windows
     credential store), so the pull runs as a one-off scheduled task inside the owner's signed-in session.
  3. Runs the FULL test suite (in parallel, -n auto) with the Mini's own Python. If pytest's exit code isn't 0 it
     stops and does NOT restart (2026-09: a piped `| tail` once hid a failure and broken code went live).
  4. Restarts with POST /api/restart (the token is HUD_AUTH_TOKEN in .env). If a task is running (a video render
     takes ~6 min) the agent answers "draining" and restarts when it ends, so this waits up to 35 minutes.
     scripts/run_forever.ps1 (the "AlwaysOnAgent" scheduled task) starts it again (exit code 3 = restart).
  5. Waits until /api/status answers with a fresh uptime, then 60 s more, and prints any ERROR or Traceback
     lines from the end of logs/worker.log.
  6. Unhealthy (not back in time, restarted by itself, or a Traceback/CRITICAL in worker.log since the restart, the
     same rules as scripts/apply_update.py): resets the live repo to the commit it had before the pull and restarts
     again (2026-10-31; before, an unhealthy deploy was only printed). A failed pull stops at step 2.

Read the printed result yourself and report it honestly (tests failed = say so; nothing was restarted).
Tools the agent runs as separate programs (Connectors, AgentComputerUse, TiktokVideos, ...) are pulled with
scripts/mini_pull_tools.py instead; they need no restart.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

if not os.path.isdir(r"C:\Users\owner\aoa-aside"):        # only the Mini has it
    sys.exit("This runs ON the Mini: python scripts/mini.py py < scripts/deploy_mini.py")
os.chdir(r"C:\Projects\AlwaysOnAgent")
sys.path.insert(0, "scripts")
from apply_update import _env_value, _log_errors_since  # noqa: E402  (_env_value never prints the value)

STATUS_URL = "http://127.0.0.1:8080/api/status"


def sh(*cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def status():
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


# 1. Tracked files must be unchanged. Untracked files are only shown: they don't block a pull, and runtime output
#    (e.g. data/evals/ from a benchmark) can appear there; .gitignore should cover runtime folders.
dirty = sh("git", "status", "--porcelain", "--untracked-files=no")[1]
untracked = sh("git", "status", "--porcelain")[1]
print("git status:", dirty or "clean", "| untracked:", " ".join(l[3:] for l in untracked.splitlines()
                                                                  if l.startswith("??")) or "none")
if dirty:
    sys.exit("The live repo on the Mini has uncommitted changes: NOT deploying. Find out where they came from first.")
s = status()
print("before:", {k: s.get(k) for k in ("status", "active_task", "current_task", "uptime_seconds", "error")})

# 2. Pull inside the signed-in session. A failed pull stops here: until 2026-10-31 it went on to test the old code
#    (which passed) and restart, and the output looked like a deploy
before = sh("git", "rev-parse", "HEAD")[1]
code, out = sh("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", r"scripts\pull_task.ps1")
print("pull:", code, out[-600:])
if code != 0:
    sys.exit("the pull failed: NOT testing or restarting (the live code is unchanged)")
after_pull = sh("git", "rev-parse", "HEAD")[1]
print("HEAD:", sh("git", "log", "--oneline", "-1")[1], "" if after_pull != before else "(unchanged: nothing new was pulled)")

# 3. The full test suite, in parallel (pytest-xdist: ~30 s instead of ~80, 2026-10-28); its own exit code decides.
#    A separate --basetemp keeps it away from the live data.
code, out = sh(sys.executable, "-m", "pytest", "-q", "-n", "auto", "-p", "no:cacheprovider",
               "--basetemp", r"C:\Users\owner\aoa-aside\pt_deploy", timeout=900)
print("pytest exit:", code, "|", out.splitlines()[-1] if out else "")
if code != 0:
    print(out[-3000:])
    sys.exit("tests failed on the Mini: NOT restarting")

# 4. Restart (waits for a running task)
def restart():
    """Asks for a restart; returns (when it was asked, extra seconds to wait while a task drains)."""
    token = _env_value("HUD_AUTH_TOKEN")
    at = time.time()
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/api/restart", method="POST",
                                     headers={"X-HUD-Token": token} if token else {})
        ans = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8") or "{}")
        print("restart:", ans)
        return at, (1800 if ans.get("status") == "draining" else 0)
    except Exception as e:
        print("restart request:", e, "(run_forever relaunches a stopped agent by itself)")
        return at, 0


# 5. Back up, and healthy? '' when healthy, else what went wrong
def health(at, extra):
    deadline = time.time() + 300 + extra
    s = {}
    while time.time() < deadline:
        time.sleep(5)
        s = status()
        if s.get("status") == "online" and s.get("uptime_seconds", 1e9) <= time.time() - at + 5:
            break
    print("after restart:", {k: s.get(k) for k in ("status", "uptime_seconds", "error")})
    if not (s.get("status") == "online" and s.get("uptime_seconds", 1e9) <= time.time() - at + 5):
        return f"it didn't come back online within {int(deadline - at)} s"
    time.sleep(60)
    s2 = status()
    print("60 s later:", {k: s2.get(k) for k in ("status", "uptime_seconds")})
    if s2.get("status") != "online":
        return "it went offline again after restarting"
    if s2.get("uptime_seconds", 0) < s.get("uptime_seconds", 0):
        return "it restarted by itself (it's crashing)"
    errors = _log_errors_since(at)
    if errors:
        return f"errors in worker.log after the restart: {errors[0][:200]}"
    return ""


problem = health(*restart())
code, out = sh("powershell", "-NoProfile", "-Command",
               r"Get-Content logs\worker.log -Tail 400 | Select-String -Pattern 'Traceback|ERROR' | Select-Object -Last 8")
print("recent errors:\n", out or "(none)")
if not problem:
    print("healthy")
    sys.exit(0)

# 6. Unhealthy: back to the commit from before the pull
print("UNHEALTHY:", problem)
if after_pull == before:
    sys.exit("nothing was pulled, so there is nothing to roll back to: check the Mini")
print("rolling back to", before[:10])
code, out = sh("git", "reset", "--hard", "-q", before)
if code != 0:
    sys.exit(f"the rollback's git reset failed ({out[-300:]}): check the Mini")
again = health(*restart())
if again:
    sys.exit(f"ROLLED BACK to {before[:10]}, but it still isn't healthy ({again}): check the Mini")
sys.exit(f"ROLLED BACK to {before[:10]}: the agent is healthy on the old code. The pushed commit stays on GitHub; "
         f"fix it there and deploy again")
