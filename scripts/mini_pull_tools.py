"""Pulls the agent's tools on the Mini and runs each one's tests. Runs ON the Mini.

From the laptop (after pushing the tool's repo):

    python scripts/mini.py py < scripts/mini_pull_tools.py              every tool
    (edit ONLY below to pull just one, or pass nothing: pulling an unchanged repo is harmless)

The tools are separate programs the agent starts fresh for every call (python <tool>.py <command>), so a pull is
live at once: no restart. Their repos are private, and an SSH session can't reach the stored GitHub sign-in, so
each pull runs inside the owner's signed-in desktop session through scripts/run_in_session.ps1 (a one-off
scheduled task). Plain `git pull` over SSH fails with "could not read Username".
"""
import os
import subprocess
import sys

ONLY = []           # e.g. ["Connectors"]; empty = all of TOOLS
TOOLS = {           # folder under C:\Projects -> its test command (None: no tests to run here)
    "Connectors": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
    "AgentComputerUse": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
    "TiktokVideos": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
    "SocialPostEngine": None,
    "VibeStats": None,
    "ImageGen": None,
    "AgentWork": [sys.executable, "tests/test_agentwork.py"],
}
HELPER = r"C:\Projects\AlwaysOnAgent\scripts\run_in_session.ps1"


def run(cmd, cwd, timeout=300):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


failed = []
for name, tests in TOOLS.items():
    if ONLY and name not in ONLY:
        continue
    folder = rf"C:\Projects\{name}"
    if not os.path.isdir(folder):
        print(f"== {name}: not on this machine")
        continue
    code, out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", HELPER,
                     "-Command", "git pull -q --ff-only", "-WorkDir", folder, "-TimeoutSec", "120"], folder, 200)
    head = run(["git", "log", "--oneline", "-1"], folder)[1]
    print(f"== {name}: {head}")
    if "[exit code 0]" not in out:
        print("   pull FAILED:", out[-500:])
        failed.append(name)
        continue
    if tests:
        code, out = run(tests, folder, 600)
        print("   tests:", out.splitlines()[-1] if out else "(no output)", "| exit", code)
        if code != 0:
            failed.append(name)
print("FAILED:", ", ".join(failed) if failed else "none")
sys.exit(1 if failed else 0)
