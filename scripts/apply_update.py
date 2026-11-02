"""
Applies an AgentWork change to the LIVE agent, safely
=====================================================
Started by the agent (the apply_agent_update chat tool, only when the owner says "apply") as its own process,
so it outlives the agent's restart. It imports nothing from the agent: the agent's code is what it replaces.

    python scripts/apply_update.py --job task-061 --workspace C:/Projects/AgentWork/workspaces/task-061
                                   --title "Add a /ping command"

1. Checks: the job's copy has changes, the live repo has none of its own, the agent is idle.
2. Runs the full test suite on the job's copy. A failure stops here: the live agent is never touched.
3. Commits the change on the job's branch, remembers the live commit (the rollback point), merges the change
   into the live repo and runs the tests again there (the live code may have moved on since the job started).
4. Restarts the agent and checks it: back online, stays up for HEALTH_SECONDS, no Traceback in worker.log.
5. Unhealthy: resets the live repo to the rollback point and restarts again (run_forever relaunches a crashed
   agent by itself, now on the old code). Healthy: pushes to GitHub and discards AgentWork's copy.
6. The outcome goes to data/updates/<job>.json and to the owner's phone.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

LIVE = Path(__file__).resolve().parent.parent
UPDATES = LIVE / "data" / "updates"
LOG = LIVE / "logs" / "worker.log"
HEALTH_SECONDS = 60           # the agent must stay up this long after the restart
COME_BACK_SECONDS = 240       # how long it may take to come back (run_forever waits longer after crashes)
TEST_TIMEOUT = 1200
DRAIN_SECONDS = 30 * 60       # core/maintenance.DRAIN_MAX (not imported: this script imports nothing from the agent)


class UpdateError(Exception):
    """Stops the update; the message is written for the owner."""


class Update:
    def __init__(self, job: str, workspace: Path, title: str, port: int):
        self.job, self.workspace, self.title, self.port = job, workspace, title, port
        self.state_file = UPDATES / f"{job}.json"
        self.state = {"job": job, "title": title, "status": "starting", "steps": [], "started": _now()}
        self.rollback_to = None

    # ---------------------------------------------------------------------------------------------- helpers
    def step(self, text: str, status: str = None) -> None:
        print(f"[{_now()}] {text}", flush=True)
        self.state["steps"].append(f"{_now()[11:19]} {text}")
        if status:
            self.state["status"] = status
        self.save()

    def save(self) -> None:
        UPDATES.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def git(self, *args, cwd: Path = None, check: bool = True) -> str:
        r = subprocess.run(["git", *args], cwd=str(cwd or LIVE), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        if check and r.returncode != 0:
            raise UpdateError(f"git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()[-300:]}")
        return r.stdout.strip()

    def tests(self, where: Path, label: str) -> None:
        self.step(f"Running the tests on {label}...")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        temp_dir = Path(tempfile.mkdtemp(prefix="aoa_update_test_"))
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                                f"--basetemp={temp_dir}"], cwd=str(where),
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TEST_TIMEOUT,
                               env=env)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        last = [l for l in r.stdout.strip().splitlines() if l.strip()][-1:] or ["(no output)"]
        if r.returncode != 0:
            failed = [l for l in r.stdout.splitlines() if l.startswith(("FAILED", "ERROR"))][:3]
            detail = [l for l in r.stdout.splitlines() if l.strip()][-30:]
            if detail:
                print("\n--- pytest failure output ---\n" + "\n".join(detail) + "\n-----------------------------\n", flush=True)
            raise UpdateError(f"the tests failed on {label}: {'; '.join(failed) or last[0]}")
        self.step(f"Tests passed on {label}: {last[0]}")

    def status(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/status", timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def restart(self) -> float:
        """Asks for a restart. The agent finishes a running task first (core/maintenance.py), so the wait for it
        to come back stretches by DRAIN_SECONDS when it says it's draining."""
        token = _env_value("HUD_AUTH_TOKEN")
        at = time.time()
        self.drain_wait = 0
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/restart", method="POST",
                                         headers={"X-HUD-Token": token} if token else {})
            answer = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8") or "{}")
            if answer.get("status") == "draining":
                self.drain_wait = DRAIN_SECONDS
                self.step(f"The agent restarts once {', '.join(answer.get('waiting_for') or ['its task'])} finishes.")
        except Exception as e:
            print(f"restart request: {e} (run_forever relaunches a stopped agent by itself)", flush=True)
        return at

    def healthy_after(self, restart_at: float) -> str:
        """'' when the agent came back and stayed up; otherwise what went wrong."""
        deadline = time.time() + COME_BACK_SECONDS + getattr(self, "drain_wait", 0)
        time.sleep(5)
        while True:
            s = self.status()
            if s and s.get("status") == "online" and s.get("uptime_seconds", 1e9) <= time.time() - restart_at + 5:
                break
            if time.time() > deadline:
                return f"the agent didn't come back online within {int(deadline - restart_at)} s"
            time.sleep(3)
        self.step("The agent is back online; watching it for a minute...")
        uptime, until = -1.0, time.time() + HEALTH_SECONDS
        while time.time() < until:
            time.sleep(5)
            s = self.status()
            if not s or s.get("status") != "online":
                return "the agent went offline again after restarting"
            if s.get("uptime_seconds", 0) < uptime:
                return "the agent restarted by itself (it's crashing)"
            uptime = s.get("uptime_seconds", 0)
        errors = _log_errors_since(restart_at)
        if errors:
            return f"errors in worker.log after the restart: {errors[0][:200]}"
        return ""

    # ---------------------------------------------------------------------------------------------- the update
    def run(self) -> str:
        self.step(f"Applying {self.job}: {self.title}", "checking")
        if not self.workspace.is_dir():
            raise UpdateError(f"AgentWork's copy for {self.job} is gone ({self.workspace})")
        if not self.git("status", "--porcelain", cwd=self.workspace):
            raise UpdateError("the job's copy has no changes to apply")
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise UpdateError("the live agent's code has uncommitted changes of its own; not mixing them")
        s = self.status()
        if not s:
            raise UpdateError("the agent isn't answering, so it can't be checked after an update")
        if s.get("active_tasks_count"):
            raise UpdateError("a task is running; try again when it has finished")

        self.state["status"] = "testing"
        self.tests(self.workspace, "the job's copy")

        self.git("add", "-A", cwd=self.workspace)
        self.git("-c", "user.name=AlwaysOnAgent", "-c", "user.email=agent@alwaysonagent.local", "commit", "-q",
                 "-m", f"{self.title} (self-update {self.job})", cwd=self.workspace)
        self.rollback_to = self.git("rev-parse", "HEAD")
        self.state["rollback_to"] = self.rollback_to
        self.step(f"Rollback point: {self.rollback_to[:10]}. Merging the change into the live agent...", "applying")
        self.git("fetch", "-q", str(self.workspace), "HEAD")
        merge = subprocess.run(["git", "-c", "user.name=AlwaysOnAgent", "-c", "user.email=agent@alwaysonagent.local",
                                "merge", "--no-ff", "--no-edit", "-m",
                                f"Self-update {self.job}: {self.title}", "FETCH_HEAD"], cwd=str(LIVE),
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if merge.returncode != 0:
            self.git("merge", "--abort", check=False)
            raise UpdateError("the change conflicts with the live code (it moved on since the job started); "
                              "run the job again")
        try:
            self.tests(LIVE, "the live agent with the change")
        except UpdateError:
            self.git("reset", "--hard", "-q", self.rollback_to)
            self.step("Undid the merge; the live agent was never restarted.")
            raise

        self.step("Restarting the agent on the new code...", "restarting")
        problem = self.healthy_after(self.restart())
        if problem:
            self.step(f"Unhealthy: {problem}. Rolling back...", "rolling_back")
            self.git("reset", "--hard", "-q", self.rollback_to)
            again = self.healthy_after(self.restart())
            if again:
                self.state["reason"] = f"{problem}; after the rollback: {again}"
                self.step("The rollback didn't bring the agent back healthy either.", "rollback_failed")
                return (f"❌ Update {self.job} broke the agent ({problem}) and the rollback didn't fix it "
                        f"({again}). The code is back at {self.rollback_to[:10]}; check the Mini.")
            self.state["reason"] = problem
            self.step("Rolled back; the agent is healthy on the old code.", "rolled_back")
            return (f"↩️ Update {self.job} was rolled back automatically: {problem}. "
                    "The agent is running the previous version.")

        pushed = subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=str(LIVE), capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=180)
        push_note = "and pushed to GitHub" if pushed.returncode == 0 else \
            f"but the push to GitHub failed ({(pushed.stderr or '').strip()[-150:]}); it's live on the Mini only"
        self.step(f"Healthy {push_note}.", "applied")
        self.state["pushed"] = pushed.returncode == 0
        _discard_job(self.workspace, self.job)
        return f"✅ Update {self.job} is live: {self.title}. Tests passed and the agent is healthy, {push_note}."


# -------------------------------------------------------------------------------------------------- helpers
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _env_value(key: str) -> str:
    try:
        for line in (LIVE / ".env").read_text(encoding="utf-8-sig").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _log_errors_since(since: float):
    """Tracebacks and CRITICAL lines in worker.log written after `since`. A traceback's lines carry no
    timestamp of their own, so everything after the first line stamped at or after `since` counts."""
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-600:]
    except OSError:
        return []
    cutoff = datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M:%S")
    start = next((i for i, l in enumerate(lines) if l.startswith("[20") and l[1:20] >= cutoff), len(lines))
    return [l for l in lines[start:] if "Traceback" in l or "[CRITICAL]" in l]


def _discard_job(workspace: Path, job: str) -> None:
    agentwork = workspace.parent.parent / "agentwork.py"
    if agentwork.is_file():
        subprocess.run([sys.executable, str(agentwork), "discard", job], cwd=str(agentwork.parent),
                       capture_output=True, timeout=120)


def _notify(text: str) -> None:
    """Through the (now healthy) agent's own code, in a fresh process."""
    subprocess.run([sys.executable, "-c", "import sys; from core.config import notify_owner; notify_owner(sys.argv[1])",
                    text], cwd=str(LIVE), capture_output=True, timeout=60)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--job", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--title", default="an AgentWork change")
    p.add_argument("--port", type=int, default=int(os.getenv("HUD_PORT") or 8080))
    args = p.parse_args()
    UPDATES.mkdir(parents=True, exist_ok=True)
    lock = UPDATES / "lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, args.job.encode())
        os.close(fd)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < 3600:
            print("another update is running", flush=True)
            return 2
        lock.unlink(missing_ok=True)
        return main()
    update = Update(args.job, Path(args.workspace), args.title, args.port)
    try:
        message = update.run()
    except UpdateError as e:
        update.state["reason"] = str(e)
        update.step(f"Stopped: {e}", "failed")
        message = f"⚠️ Update {args.job} not applied: {e}. The live agent is unchanged."
    except Exception as e:
        update.state["reason"] = f"{type(e).__name__}: {e}"
        if update.rollback_to and update.state.get("status") in ("applying", "restarting"):
            subprocess.run(["git", "reset", "--hard", "-q", update.rollback_to], cwd=str(LIVE))
            update.restart()
        update.step(f"Crashed: {e}", "failed")
        message = f"⚠️ Update {args.job} stopped unexpectedly ({e}); the live code was reset to the previous version."
    finally:
        lock.unlink(missing_ok=True)
    update.state["finished"] = _now()
    update.state["message"] = message
    update.save()
    _notify(message)
    print(message, flush=True)
    return 0 if update.state["status"] == "applied" else 1


if __name__ == "__main__":
    sys.exit(main())
