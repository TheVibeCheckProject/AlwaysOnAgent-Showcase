"""run_streaming: run a command, logging its output line by line
=========================================================
Split out of core/task_engines.py §4 on 2026-11-04; moved into core/task_engines/
on 2026-11-04, re-exported by the package for backwards compatibility.
"""

import collections
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

try:
    from core.processes import kill_process_tree
except ImportError:
    from processes import kill_process_tree  # type: ignore[no-redef]
try:
    from .constants import (
        _QUOTA_MARKERS,
        _QUOTA_RESET,
        OUTPUT_HEAD,
        OUTPUT_LOG_LINES,
        OUTPUT_TAIL,
        QUOTA_EXIT_CODE,
        quota_message,
    )
except ImportError:
    from core.task_engines.constants import (
        _QUOTA_MARKERS,  # type: ignore[no-redef]
        _QUOTA_RESET,
        OUTPUT_HEAD,
        OUTPUT_LOG_LINES,
        OUTPUT_TAIL,
        QUOTA_EXIT_CODE,
        quota_message,
    )


class _StreamState:
    """Mutable output state shared by run_streaming and its helpers."""

    def __init__(self) -> None:
        self.head: list[str] = []
        self.tail: collections.deque[str] = collections.deque(maxlen=OUTPUT_TAIL)
        self.quota_lines: list[str] = []
        self.seen = 0


def _start_process(cmd: list[str], working_dir: Path, env: dict[str, str],
                   stdin_text: str | None = None) -> subprocess.Popen:
    """Starts the child process with piped output and optional piped input."""
    return subprocess.Popen(
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


def _pump_stdout(process: subprocess.Popen, line_queue: "queue.Queue[str | None]") -> None:
    """Reads the process stdout into the queue, then signals end-of-output."""
    try:
        if process.stdout:
            for raw in process.stdout:
                line_queue.put(raw)
    finally:
        line_queue.put(None)


def _feed_stdin(process: subprocess.Popen, stdin_text: str) -> None:
    """Writes the prompt into the process stdin, then closes it."""
    try:
        if process.stdin:
            process.stdin.write(stdin_text)
            process.stdin.close()
    except (OSError, ValueError):
        pass                          # it exited or closed its input early


def _format_output(state: _StreamState) -> str:
    """Formats the kept head/tail output, noting any dropped middle lines."""
    dropped = state.seen - len(state.head) - len(state.tail)
    return "\n".join(state.head + ([f"[... {dropped} lines not kept ...]"] if dropped > 0 else []) + list(state.tail))


def _timed_out(process: subprocess.Popen, logger: logging.Logger, timeout_seconds: float, output: str) -> tuple[int, str]:
    """Kills the process tree after a timeout and reports exit code -1."""
    logger.error(f"Task timed out after {timeout_seconds}s! Terminating process tree...")
    kill_process_tree(process.pid, logger=logger)
    return -1, output + "\n[ERROR] Task execution exceeded timeout limit."


def _drain_lines(line_queue: "queue.Queue[str | None]", state: _StreamState, logger: logging.Logger,
                tag: str, timeout_seconds: float, start_time: float,
                process: subprocess.Popen) -> tuple[int, str] | None:
    """Drains stdout lines until end-of-output, or a timeout result. Returns None when the output ended normally."""
    while True:
        remaining = timeout_seconds - (time.time() - start_time)
        if remaining <= 0:
            return _timed_out(process, logger, timeout_seconds, _format_output(state))
        try:
            line = line_queue.get(timeout=min(1.0, remaining))
        except queue.Empty:
            continue
        if line is None:
            break
        stripped = line.rstrip()
        if stripped:
            state.seen += 1
            if state.seen <= OUTPUT_LOG_LINES:
                logger.info(f"[{tag}] {stripped}")
            else:
                logger.debug(f"[{tag}] {stripped}")
            if len(state.head) < OUTPUT_HEAD:
                state.head.append(stripped)
            else:
                state.tail.append(stripped)
            if _QUOTA_MARKERS.search(stripped) or _QUOTA_RESET.search(stripped):
                state.quota_lines.append(stripped)
    return None


def run_streaming(cmd: list[str], working_dir: Path, env: dict[str, str], task_id: str, timeout_seconds: float,
                  logger: logging.Logger, tag: str, db: Any | None = None,
                  stdin_text: str | None = None) -> tuple[int, str]:
    """
    Runs a command in working_dir, logging its output line by line as "[tag] ...". Records its PID
    so /cancel can stop it, and kills the whole process tree after timeout_seconds (exit code -1).
    Returns (exit code, output); a quota refusal in the output becomes QUOTA_EXIT_CODE.
    """
    start_time = time.time()
    # The first OUTPUT_HEAD and last OUTPUT_TAIL lines (only ~1,500 characters are stored), and every line that may be
    # a quota message: a verbose engine was kept and logged in full (2026-10-28 audit 3.7)
    state = _StreamState()
    process = _start_process(cmd, working_dir, env, stdin_text)
    try:
        # Record PID in database for process-tree cancellation
        if db:
            db.set_task_pid(task_id, process.pid)

        # Read stdout on a helper thread so the timeout is enforced even when
        # the program hangs without printing (readline() would otherwise block forever).
        line_queue: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=_pump_stdout, args=(process, line_queue), name=f"{tag}-stdout-{task_id}", daemon=True).start()
        if stdin_text is not None:
            # On its own thread, after the reader started: a prompt bigger than the pipe, sent to a program that prints
            # before reading all of it, left both waiting where the timeout couldn't fire (audit 3.6)
            threading.Thread(target=_feed_stdin, args=(process, stdin_text), name=f"{tag}-stdin-{task_id}", daemon=True).start()

        timed_out = _drain_lines(line_queue, state, logger, tag, timeout_seconds, start_time, process)
        if timed_out is not None:
            return timed_out

        try:
            exit_code = process.wait(timeout=max(0.1, timeout_seconds - (time.time() - start_time)))
        except subprocess.TimeoutExpired:
            return _timed_out(process, logger, timeout_seconds, _format_output(state))
        logger.info(f"[{tag}] Process completed with exit code {exit_code} in {time.time() - start_time:.2f}s"
                    + (f" ({state.seen} lines; the first {OUTPUT_LOG_LINES} logged)" if state.seen > OUTPUT_LOG_LINES else ""))
        output = _format_output(state)
        quota = quota_message("\n".join(state.quota_lines)) if exit_code != 0 else None
        if quota:
            logger.warning(f"[{tag}] {quota}")
            return QUOTA_EXIT_CODE, f"{output}\n\n[QUOTA] {quota}"
        return exit_code, output
    finally:
        if db:
            db.set_task_pid(task_id, None)
