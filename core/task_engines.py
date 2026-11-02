"""
Task engines: how a background task gets done
=============================================
- AntigravityEngine: the Antigravity CLI (agy), a capable coding agent with a real terminal.
- CloudEngine:       the free cloud models (Codestral first), then local Ollama if every cloud
                     route is unavailable.
- OllamaEngine:      the local model only (offline).
- AgentWorkEngine:   hands a project job (a change to one of the user's repos) to AgentWork, a
                     separate tool in its own folder (C:/Projects/AgentWork).
- FileArtifactHelper: saves the files a model's reply contains into the task's folder, then runs
                     any tests it wrote in the sandbox.

Every engine works inside the task's own folder (AgentWork/workspaces/<task id>/, given by the dispatcher).
The media pipeline is dispatched separately (core/daemon.py).
"""

import collections
import json
import logging
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from core.config import get_safe_subprocess_env, git_lockdown_env, resolve_agy_binary
    from core.llm import CloudLLM, OllamaClient
    from core.llm_usage import usage_purpose
    from core.processes import kill_process_tree
except ImportError:
    from config import get_safe_subprocess_env, git_lockdown_env, resolve_agy_binary
    from llm import CloudLLM, OllamaClient
    from llm_usage import usage_purpose
    from processes import kill_process_tree

# Returned when no model could produce an answer (no keys, rate limits everywhere, network
# errors). The caller may try another engine. An answer whose code fails sandbox QA returns 1
# instead and must not fall back: a weaker model would not do better.
UNAVAILABLE_EXIT_CODE = 75
# Antigravity refused because the account's quota for that model is used up: retrying soon is
# pointless (it resets hours later), so the daemon fails the task at once and says when to retry.
QUOTA_EXIT_CODE = 76
_QUOTA_MARKERS = re.compile(r"quota reached|RESOURCE_EXHAUSTED", re.IGNORECASE)
_QUOTA_RESET = re.compile(r"Resets in ([0-9hms]+)")
SAFE_TEST_NAME = re.compile(r"[\w./-]+")        # a test file a task wrote may be run only under a plain name
OUTPUT_HEAD, OUTPUT_TAIL = 200, 400              # lines of a task's output kept (run_streaming)
OUTPUT_LOG_LINES = 100                           # lines logged at INFO; the rest at DEBUG


def quota_message(output: str) -> Optional[str]:
    """A one-line explanation when an engine's output says its quota is used up, else None."""
    if not _QUOTA_MARKERS.search(output or ""):
        return None
    reset = _QUOTA_RESET.search(output)
    return ("Antigravity's quota for this model is used up" +
            (f"; it resets in {reset.group(1)}" if reset else "") + ". Retry the task (/retry) after that.")

ENGINES = ("auto", "antigravity", "cloud", "ollama", "agentwork", "media_pipeline", "social_posts", "weekly_report",
           "computer")
DEFAULT_ENGINE_CHOICES = ("auto", "antigravity", "cloud", "ollama")    # what /engine and the HUD can set
# Older names still accepted everywhere (typed commands, stored settings and tasks)
ENGINE_ALIASES = {"hermes": "cloud", "groq": "cloud",
                  "video": "media_pipeline", "tiktok": "media_pipeline", "social_publisher": "media_pipeline",
                  "pinterest": "social_posts", "pins": "social_posts", "report": "weekly_report",
                  "computer_use": "computer"}


def normalize_engine(name: Optional[str]) -> str:
    """The engine a name refers to; unknown or empty names mean 'auto'."""
    name = (name or "auto").strip().lower()
    name = ENGINE_ALIASES.get(name, name)
    return name if name in ENGINES else "auto"


CODING_SYSTEM_PROMPT = (
    "You are an autonomous AI software engineer executing tasks headlessly for an Always-On Background Worker.\n"
    "Provide the complete, full content of any file requested. Do not merely output the filename. "
    "Format the content clearly so it can be materialized into the project."
)

# Prepended to every task Antigravity runs. It has a real terminal and runs as the PC's user,
# so these rules (plus git_lockdown_env and the audit after each run) keep it to the task's folder.
TASK_RULES = (
    "[Rules for this task]\n"
    "- Work only inside this folder: {folder}\n"
    "  Create, change and delete files only there. Do not read or modify anything outside it.\n"
    "- Never run git push, gh, or anything that changes git configuration, remotes or credentials.\n"
    "- Never kill or restart other programs (especially Google Chrome), and install nothing globally.\n"
    "- If something you need is blocked or missing, stop and explain what you needed. Do not work around it."
)


class FileArtifactHelper:
    """Extracts synthesized code and documentation into workspace files."""

    @staticmethod
    def apply_file_artifacts(response_text: str, working_dir: Path, prompt: str = "", logger: Optional[logging.Logger] = None):
        log = logger or logging.getLogger("FileArtifactHelper")
        response_text = (response_text or "").replace("\r\n", "\n")      # Windows line endings break the patterns
        patterns = [
            r"(?:###|##)?\s*(?:Create|Write|File:?)\s+[`\*]*([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+)[`\*]*\s*\n+```[a-zA-Z0-9_-]*\n(.*?)```",
            r"\*\*([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+)\*\*\s*\n+```[a-zA-Z0-9_-]*\n(.*?)```",
            r"```[a-zA-Z0-9_-]*\s+filepath=([^\n]+)\n(.*?)```"
        ]

        created_files = set()
        for pat in patterns:
            matches = re.findall(pat, response_text, re.DOTALL)
            for file_name, content in matches:
                file_name = file_name.strip().strip("`").strip("'").strip('"')
                clean_path = (working_dir / file_name).resolve()
                try:
                    if not clean_path.is_relative_to(working_dir.resolve()):
                        log.error(f"Security Alert: Path traversal attempt blocked for '{file_name}'")
                        continue
                    clean_path.parent.mkdir(parents=True, exist_ok=True)
                    clean_path.write_text(content, encoding="utf-8")
                    log.info(f"Synthesized file written to workspace: {file_name}")
                    created_files.add(file_name)
                except Exception as e:
                    log.warning(f"Could not write file {file_name}: {e}")

        # Check if prompt requested a specific file (e.g. "Create a file named SUMMARY.txt")
        file_match = re.search(r"(?:create|write|scaffold|generate)\s+(?:a\s+file\s+)?(?:named\s+)?([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)", prompt, re.IGNORECASE)
        if file_match:
            target_name = file_match.group(1).strip()
            target_path = (working_dir / target_name).resolve()
            if not target_path.is_relative_to(working_dir.resolve()):
                log.error(f"Security Alert: Target file path traversal blocked for '{target_name}'")
                return

            if not target_path.exists() and target_name not in created_files:
                header_match = re.search(r"(#+\s+" + re.escape(target_name) + r".*)", response_text, re.DOTALL | re.IGNORECASE)
                if header_match:
                    content_to_write = header_match.group(1).strip()
                else:
                    lines = response_text.splitlines()
                    start_idx = 0
                    for idx, line in enumerate(lines):
                        if line.strip().startswith("#") or line.strip().startswith("```") or line.strip().startswith("==="):
                            start_idx = idx
                            break
                    content_to_write = "\n".join(lines[start_idx:]).strip()

                if content_to_write:
                    try:
                        target_path.write_text(content_to_write, encoding="utf-8")
                        log.info(f"Successfully materialized target file: {target_name}")
                        created_files.add(target_name)
                    except Exception as e:
                        log.warning(f"Failed writing {target_name}: {e}")

        return list(created_files)

    @staticmethod
    def run_sandbox_qa(task_id: str, working_dir: Path, created_files: List[str], logger: Optional[logging.Logger] = None) -> Tuple[bool, str]:
        """Runs unit tests inside isolated sandbox to verify code correctness before approval."""
        test_files = [f.replace("\\", "/") for f in created_files
                      if Path(f).name.startswith("test_") and f.endswith(".py")]
        # The names come from the model's answer: only plain ones. "test_a & <command> & b.py" passed the test_*.py
        # check and went into a shell command (2026-10-28 audit 1.2)
        odd = [f for f in test_files if not SAFE_TEST_NAME.fullmatch(f)]
        test_files = [f for f in test_files if f not in odd]
        skipped_note = f"\nNot run (unusual file names): {', '.join(odd)}" if odd else ""
        if not test_files:
            return True, "No unit test files identified for sandboxed verification." + skipped_note

        log = logger or logging.getLogger("SandboxQA")
        try:
            from core.sandbox import WarmDockerSandbox, is_docker_available
        except ImportError:
            from sandbox import WarmDockerSandbox, is_docker_available
        if not is_docker_available():
            # Without Docker the "sandbox" was only a folder: AI-written tests ran with the owner's full rights, files,
            # network and the local API (the Mini has no Docker). They aren't run; the task's own result stands
            log.info(f"[sandbox:qa] No Docker: the AI-written tests of [{task_id}] aren't run: {test_files}")
            return True, ("AI-written tests were not run: this PC has no Docker to isolate them. Files: "
                          + ", ".join(test_files) + skipped_note)
        log.info(f"[sandbox:qa] Running Sandboxed QA for task [{task_id}] on: {test_files}")

        try:
            sandbox = WarmDockerSandbox(task_id, working_dir)
            with sandbox:
                # Copy all created files to sandbox
                for cf in created_files:
                    src = working_dir / cf
                    dst = sandbox.sandbox_dir / cf
                    if src.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, dst)

                all_passed = True
                reports = []
                for tf in test_files:
                    code, stdout, stderr = sandbox.execute(f"python -m unittest {shlex.quote(tf)}", timeout=30)
                    out_text = (stdout + "\n" + stderr).strip()
                    reports.append(f"Test: {tf} | Exit: {code}\n{out_text}")
                    if code != 0:
                        all_passed = False

                return all_passed, "\n---\n".join(reports) + skipped_note
        except Exception as e:
            # Not verified isn't passed (until audit 1.2 this returned True, so a broken sandbox approved the work)
            log.warning(f"[sandbox:qa] Sandbox QA could not be executed: {e}")
            return False, f"Sandbox QA couldn't verify the generated code: {e}"


def run_streaming(cmd: List[str], working_dir: Path, env: Dict[str, str], task_id: str, timeout_seconds: float,
                  logger: logging.Logger, tag: str, db: Optional[Any] = None,
                  stdin_text: Optional[str] = None) -> Tuple[int, str]:
    """
    Runs a command in working_dir, logging its output line by line as "[tag] ...". Records its PID
    so /cancel can stop it, and kills the whole process tree after timeout_seconds (exit code -1).
    Returns (exit code, output); a quota refusal in the output becomes QUOTA_EXIT_CODE.
    """
    start_time = time.time()
    # The first OUTPUT_HEAD and last OUTPUT_TAIL lines (only ~1,500 characters are stored), and every line that may be
    # a quota message: a verbose engine was kept and logged in full (2026-10-28 audit 3.7)
    head: List[str] = []
    tail: "collections.deque[str]" = collections.deque(maxlen=OUTPUT_TAIL)
    quota_lines: List[str] = []
    seen = 0
    process = subprocess.Popen(
        cmd,
        cwd=str(working_dir),
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1
    )
    try:
        # Record PID in database for process-tree cancellation
        if db:
            db.set_task_pid(task_id, process.pid)

        # Read stdout on a helper thread so the timeout is enforced even when
        # the program hangs without printing (readline() would otherwise block forever).
        line_queue: "queue.Queue[Optional[str]]" = queue.Queue()

        def _pump_stdout():
            try:
                for raw in process.stdout:
                    line_queue.put(raw)
            finally:
                line_queue.put(None)

        threading.Thread(target=_pump_stdout, name=f"{tag}-stdout-{task_id}", daemon=True).start()
        if stdin_text is not None:
            # On its own thread, after the reader started: a prompt bigger than the pipe, sent to a program that prints
            # before reading all of it, left both waiting where the timeout couldn't fire (audit 3.6)
            def _feed_stdin():
                try:
                    process.stdin.write(stdin_text)
                    process.stdin.close()
                except (OSError, ValueError):
                    pass                          # it exited or closed its input early
            threading.Thread(target=_feed_stdin, name=f"{tag}-stdin-{task_id}", daemon=True).start()

        def _output() -> str:
            dropped = seen - len(head) - len(tail)
            return "\n".join(head + ([f"[... {dropped} lines not kept ...]"] if dropped > 0 else []) + list(tail))

        def _timed_out() -> Tuple[int, str]:
            logger.error(f"Task timed out after {timeout_seconds}s! Terminating process tree...")
            kill_process_tree(process.pid, logger=logger)
            return -1, _output() + "\n[ERROR] Task execution exceeded timeout limit."

        while True:
            remaining = timeout_seconds - (time.time() - start_time)
            if remaining <= 0:
                return _timed_out()
            try:
                line = line_queue.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if line is None:
                break
            stripped = line.rstrip()
            if stripped:
                seen += 1
                if seen <= OUTPUT_LOG_LINES:
                    logger.info(f"[{tag}] {stripped}")
                else:
                    logger.debug(f"[{tag}] {stripped}")
                if len(head) < OUTPUT_HEAD:
                    head.append(stripped)
                else:
                    tail.append(stripped)
                if _QUOTA_MARKERS.search(stripped) or _QUOTA_RESET.search(stripped):
                    quota_lines.append(stripped)

        try:
            exit_code = process.wait(timeout=max(0.1, timeout_seconds - (time.time() - start_time)))
        except subprocess.TimeoutExpired:
            return _timed_out()
        logger.info(f"[{tag}] Process completed with exit code {exit_code} in {time.time() - start_time:.2f}s"
                    + (f" ({seen} lines; the first {OUTPUT_LOG_LINES} logged)" if seen > OUTPUT_LOG_LINES else ""))
        output = _output()
        quota = quota_message("\n".join(quota_lines)) if exit_code != 0 else None
        if quota:
            logger.warning(f"[{tag}] {quota}")
            return QUOTA_EXIT_CODE, f"{output}\n\n[QUOTA] {quota}"
        return exit_code, output
    finally:
        if db:
            db.set_task_pid(task_id, None)


class AntigravityEngine:
    """Runs a task with the Antigravity CLI (agy): a capable coding agent with a real terminal."""

    def __init__(
        self,
        custom_agy_path: Optional[str] = None,
        db: Optional[Any] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.logger = logger or logging.getLogger("AntigravityEngine")
        self.agy_path = custom_agy_path or resolve_agy_binary()
        self.db = db

    def execute_task(
        self,
        task: Dict[str, Any],
        working_dir: Path
    ) -> Tuple[int, str]:
        """
        Executes a prompt via Antigravity CLI non-interactively in headless mode.
        Captures exit code and streams real-time stdout/stderr.
        """
        mode = task.get("mode", "accept-edits")
        timeout_seconds = task.get("timeout_seconds", 300)
        task_id = task.get("id", "unknown")
        prompt = TASK_RULES.format(folder=Path(working_dir).resolve()) + "\n\n" + task.get("prompt", "")

        cmd = [
            self.agy_path,
            "--prompt", prompt,
            "--dangerously-skip-permissions",
            "--mode", mode,
            "--add-dir", str(Path(working_dir).resolve()),
        ]

        self.logger.info(f"Triggering Antigravity CLI in {working_dir} (task rules + task prompt, {len(prompt)} chars)")
        self.logger.info(f"Task ID: {task_id} | Mode: {mode} | Timeout: {timeout_seconds}s")
        try:
            return run_streaming(cmd, working_dir, get_safe_subprocess_env(git_lockdown_env()), task_id,
                                 timeout_seconds, self.logger, "agy", db=self.db)
        except FileNotFoundError:
            err_msg = f"Antigravity CLI binary '{self.agy_path}' was not found."
            self.logger.critical(err_msg)
            return 127, err_msg
        except Exception as e:
            err_msg = f"Unexpected error launching Antigravity CLI: {str(e)}"
            self.logger.exception(err_msg)
            return 1, err_msg


class AgentWorkEngine:
    """
    Hands a job to AgentWork, a separate tool in its own folder (AGENT_WORK_DIR, C:/Projects/AgentWork).
    AgentWork keeps the projects (repos and their rules), makes a fresh copy of the repo on its own
    branch, runs the coding agent there and reports what changed. This class only calls it:
        agentwork.py list                          the projects, as JSON
        agentwork.py add <name> --repo --description   registers a repo (AgentWork checks it first)
        agentwork.py run <project> <job id>        one job (the instruction on stdin)
    """

    def __init__(self, home: Path, db: Optional[Any] = None, logger: Optional[logging.Logger] = None):
        self.home = Path(home)
        self.db = db
        self.logger = logger or logging.getLogger("AgentWorkEngine")

    @property
    def script(self) -> Path:
        return self.home / "agentwork.py"

    def _env(self) -> Dict[str, str]:
        extra = {"PYTHONIOENCODING": "utf-8"}
        agy = resolve_agy_binary()
        if Path(agy).is_file():
            extra["AGY_PATH"] = agy
        return get_safe_subprocess_env(extra)

    def list_projects(self) -> List[Dict[str, Any]]:
        """AgentWork's projects ([{name, description, repo, ...}]); [] if the tool is missing or fails."""
        if not self.script.is_file():
            self.logger.warning(f"[agentwork] Not found: {self.script}")
            return []
        try:
            result = subprocess.run([sys.executable, str(self.script), "list"], cwd=str(self.home), env=self._env(),
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            projects = json.loads(result.stdout) if result.returncode == 0 else None
            if not isinstance(projects, list):
                self.logger.warning(f"[agentwork] 'list' failed ({result.returncode}): {result.stdout[-300:]}")
                return []
            return [p for p in projects if isinstance(p, dict) and p.get("name")]
        except Exception as e:
            self.logger.warning(f"[agentwork] 'list' failed: {e}")
            return []

    def add_project(self, name: str, repo: str, description: str, base: str = "main") -> Dict[str, Any]:
        """Registers a repo with AgentWork. Returns the new project; raises ValueError with AgentWork's
        reason (bad name, unreachable repo, duplicate...) if it refuses. base: the branch jobs start from (the
        repo's default branch, from the repo browser)."""
        if not self.script.is_file():
            raise ValueError(f"AgentWork wasn't found at {self.home}")
        try:
            result = subprocess.run([sys.executable, str(self.script), "add", name, "--repo", repo,
                                     "--description", description, "--base", base or "main"],
                                    cwd=str(self.home), env=self._env(),
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        except subprocess.TimeoutExpired:
            raise ValueError("AgentWork took too long checking the repo")
        if result.returncode != 0:
            reason = (result.stderr or result.stdout).strip().removeprefix("[agentwork]").strip()
            raise ValueError(reason[-400:] or f"AgentWork refused (exit {result.returncode})")
        return json.loads(result.stdout)

    def execute_task(self, task: Dict[str, Any]) -> Tuple[int, str]:
        task_id = str(task.get("id", "unknown"))
        project = str((task.get("metadata") or {}).get("project") or "")
        timeout_seconds = int(task.get("timeout_seconds") or 1800)
        if not project:
            return 2, "This AgentWork task has no project."
        if not self.script.is_file():
            return 127, f"AgentWork was not found at {self.script}."
        cmd = [sys.executable, str(self.script), "run", project, task_id, "--timeout", str(timeout_seconds)]
        self.logger.info(f"[agentwork] Job {task_id} on project '{project}' (timeout {timeout_seconds}s)")
        try:
            # A little extra time so AgentWork's own timeout (which reports what it managed) fires first
            return run_streaming(cmd, self.home, self._env(), task_id, timeout_seconds + 120, self.logger,
                                 "agentwork", db=self.db, stdin_text=task.get("prompt", ""))
        except Exception as e:
            self.logger.exception(f"[agentwork] Couldn't start AgentWork: {e}")
            return 1, f"Couldn't start AgentWork: {e}"


class CloudEngine:
    """The free cloud models (CloudLLM.TASK_MODELS: Codestral first), then local Ollama if none answers."""

    def __init__(self, llm: CloudLLM, ollama: "OllamaEngine", logger: Optional[logging.Logger] = None):
        self.llm = llm
        self.ollama = ollama
        self.logger = logger or logging.getLogger("CloudEngine")

    def execute_task(self, task: Dict[str, Any], working_dir: Path) -> Tuple[int, str]:
        code, output = self.run_in_cloud(task, working_dir)
        if code != UNAVAILABLE_EXIT_CODE:
            return code, output      # done, or its code failed QA (a weaker local model would not do better)
        self.logger.warning(f"[cloud] No cloud model available ({output}). Falling back to local Ollama.")
        return self.ollama.execute_task(task, working_dir)

    def run_in_cloud(self, task: Dict[str, Any], working_dir: Path) -> Tuple[int, str]:
        """The cloud step alone: (0, reply) done, (1, report) its code failed QA, or UNAVAILABLE_EXIT_CODE."""
        if not self.llm.is_available():
            return UNAVAILABLE_EXIT_CODE, "no cloud AI provider is configured (.env)"
        prompt = task.get("prompt", "")
        routes = ([task["model"]] if task.get("model") else []) + list(self.llm.TASK_MODELS)
        messages = [{"role": "system", "content": CODING_SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        started = time.time()
        with usage_purpose("task"):
            reply, route = self.llm.complete(messages, max_tokens=4096, temperature=0.2,
                                             timeout=task.get("timeout_seconds", 120), models=routes)
        if not reply:
            return UNAVAILABLE_EXIT_CODE, f"cloud generation failed on every provider: {route}"
        self.logger.info(f"[cloud] Completed via {route} in {time.time() - started:.2f}s (~{len(reply)} chars)")
        return _save_and_check(task, working_dir, reply, prompt, self.logger)


class OllamaEngine:
    """The local model only (offline); also the cloud engine's last resort."""

    def __init__(self, client: OllamaClient, logger: Optional[logging.Logger] = None):
        self.client = client
        self.logger = logger or logging.getLogger("OllamaEngine")

    def execute_task(self, task: Dict[str, Any], working_dir: Path) -> Tuple[int, str]:
        prompt = task.get("prompt", "")
        model = self.client.select_best_model(task.get("model"))
        self.logger.info(f"[ollama] Connecting to {self.client.host} with model: {model}")
        full_prompt = (
            "You are an autonomous AI software engineer executing tasks headlessly for an Always-On Agent.\n"
            "If the prompt asks you to create or modify a file, include the filename and complete code.\n\n"
            f"TASK INSTRUCTION:\n{prompt}\n\n"
            "Produce clear, high-quality, executable code or solution."
        )
        started = time.time()
        reply = self.client.generate(full_prompt, timeout=task.get("timeout_seconds", 300), model=model,
                                     temperature=0.2, max_tokens=4096)
        if not reply:
            return 1, f"Ollama generation failed (is Ollama running at {self.client.host}?)"
        self.logger.info(f"[ollama] Completed in {time.time() - started:.2f}s ({len(reply)} characters)")
        return _save_and_check(task, working_dir, reply, prompt, self.logger)


def _save_and_check(task: Dict[str, Any], working_dir: Path, reply: str, prompt: str,
                    logger: logging.Logger) -> Tuple[int, str]:
    """Saves the reply's files into the task folder and runs any tests it wrote."""
    created = FileArtifactHelper.apply_file_artifacts(reply, working_dir, prompt=prompt, logger=logger)
    passed, qa_report = FileArtifactHelper.run_sandbox_qa(task.get("id", "task-temp"), working_dir, created, logger=logger)
    if not passed:
        logger.warning(f"Sandboxed QA failed:\n{qa_report}")
        return 1, f"Generated code failed sandbox verification tests:\n{qa_report}\n\nFull Output:\n{reply}"
    return 0, reply
