"""
Puts an AgentWork website job live, safely
==========================================
Started by the agent when the owner taps "🚀 Push live" on the job's card (POST /api/tasks/{id}/apply). Pushing the
website's main branch deploys example-site.com (GitHub Pages), so nothing is pushed unless every step passes.

    python scripts/apply_website.py --job task-071 --workspace C:/Projects/AgentWork/workspaces/task-071
                                    --title "Add the sympathy card guide"

1. Checks: the job's copy has changes; no other go-live is running.
2. Commits the change on the job's branch, then replays it on top of the live site's newest main (the owner's
   website session pushes too). A clash stops here: nothing is pushed.
3. `npm ci` (when needed), `npm run build` (committing what it regenerates), `npm test` (static checks + the Edge
   browser suite), all on that combined version. Any failure stops here.
4. Pushes to main; GitHub Pages publishes within a minute or two. The outcome goes to data/updates/<job>.json and
   to the owner's phone.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LIVE = Path(__file__).resolve().parent.parent          # the agent (for its notifier and the shared update lock)
UPDATES = LIVE / "data" / "updates"
SITE = "https://example-site.com"
NPM = "npm.cmd" if os.name == "nt" else "npm"
IDENTITY = ["-c", "user.name=AlwaysOnAgent", "-c", "user.email=agent@alwaysonagent.local"]


class GoLiveError(Exception):
    """Stops the go-live; the message is written for the owner."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class GoLive:
    def __init__(self, job: str, workspace: Path, title: str):
        self.job, self.workspace, self.title = job, workspace, title
        self.state = {"job": job, "title": title, "project": "website", "status": "starting", "steps": [],
                      "started": _now()}

    def step(self, text: str, status: str = None) -> None:
        print(f"[{_now()}] {text}", flush=True)
        self.state["steps"].append(f"{_now()[11:19]} {text}")
        if status:
            self.state["status"] = status
        UPDATES.mkdir(parents=True, exist_ok=True)
        (UPDATES / f"{self.job}.json").write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def run_cmd(self, args, what: str, timeout: int = 900, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(args, cwd=str(self.workspace), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        if check and r.returncode != 0:
            tail = [l for l in (r.stdout + "\n" + r.stderr).splitlines() if l.strip()][-4:]
            raise GoLiveError(f"{what} failed: {' | '.join(tail)[-400:]}")
        return r

    def git(self, *args, check: bool = True) -> str:
        return self.run_cmd(["git", *args], f"git {args[0]}", timeout=300, check=check).stdout.strip()

    def run(self) -> str:
        self.step(f"Putting {self.job} live: {self.title}", "checking")
        if not self.workspace.is_dir():
            raise GoLiveError(f"AgentWork's copy of {self.job} is gone ({self.workspace})")
        if not self.git("status", "--porcelain"):
            raise GoLiveError("the job's copy has no changes to put live")

        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", f"{self.title} ({self.job})")
        self.step("Bringing the change up to date with the live site...", "updating")
        self.git("fetch", "-q", "origin", "main")
        rebase = self.run_cmd(["git", *IDENTITY, "rebase", "origin/main"], "rebase", check=False)
        if rebase.returncode != 0:
            said = rebase.stdout + "\n" + rebase.stderr
            self.git("rebase", "--abort", check=False)
            if re.search(r"CONFLICT|could not apply", said):
                raise GoLiveError("the live site changed in the same places since the job started; run the job again")
            # Anything else (a long path, a lock file, permissions) said so: until 2026-10-28 every failure was called a
            # clash, with the wrong advice (audit 5.5: "Filename too long")
            last = [l.strip() for l in said.splitlines() if l.strip()][-2:]
            raise GoLiveError(f"the change couldn't be brought up to date with the live site: {' | '.join(last)[-300:]}")

        self.state["status"] = "testing"
        if not (self.workspace / "node_modules").is_dir():
            self.step("Installing the site's packages (npm ci)...")
            self.run_cmd([NPM, "ci", "--no-audit", "--no-fund"], "npm ci", timeout=900)
        self.step("Building the site (npm run build)...")
        self.run_cmd([NPM, "run", "build"], "npm run build")
        if self.git("status", "--porcelain"):
            self.git("add", "-A")
            self.git(*IDENTITY, "commit", "-q", "-m", f"build: regenerate ({self.job})")
        self.step("Testing the site (npm test: static checks + browser suite)...")
        test = self.run_cmd([NPM, "test"], "npm test", timeout=1500)
        last = [l for l in test.stdout.splitlines() if l.strip()][-1:] or [""]
        self.step(f"Tests passed. {last[0][:120]}")

        self.step("Pushing to the live site...", "pushing")
        self.run_cmd(["git", "push", "-q", "origin", "HEAD:main"], "git push", timeout=300)
        commit = self.git("rev-parse", "--short", "HEAD")
        self.state["commit"] = commit
        self.step(f"Pushed {commit}; GitHub Pages publishes it within a minute or two.", "applied")
        _discard_job(self.workspace, self.job)
        return f"✅ {self.title} is going live on {SITE} (commit {commit}): built and tested first."


def _discard_job(workspace: Path, job: str) -> None:
    agentwork = workspace.parent.parent / "agentwork.py"
    if agentwork.is_file():
        subprocess.run([sys.executable, str(agentwork), "discard", job], cwd=str(agentwork.parent),
                       capture_output=True, timeout=120)


def _notify(text: str) -> None:
    subprocess.run([sys.executable, "-c", "import sys; from core.config import notify_owner; notify_owner(sys.argv[1])",
                    text], cwd=str(LIVE), capture_output=True, timeout=60)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--job", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--title", default="a website change")
    args = p.parse_args()
    UPDATES.mkdir(parents=True, exist_ok=True)
    lock = UPDATES / "lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, args.job.encode())
        os.close(fd)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < 3600:
            print("another go-live is running", flush=True)
            return 2
        lock.unlink(missing_ok=True)
        return main()
    go = GoLive(args.job, Path(args.workspace), args.title)
    try:
        message = go.run()
    except GoLiveError as e:
        go.state["reason"] = str(e)
        go.step(f"Stopped: {e}", "failed")
        message = f"⚠️ {args.job} didn't go live: {e}. Nothing was pushed; the site is unchanged."
    except Exception as e:
        go.state["reason"] = f"{type(e).__name__}: {e}"
        go.step(f"Crashed: {e}", "failed")
        message = f"⚠️ {args.job} stopped unexpectedly ({e}). Nothing was pushed."
    finally:
        lock.unlink(missing_ok=True)
    go.state.update(finished=_now(), message=message)
    go.step(message)
    _notify(message)
    print(message, flush=True)
    return 0 if go.state["status"] == "applied" else 1


if __name__ == "__main__":
    sys.exit(main())
