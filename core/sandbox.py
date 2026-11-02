"""
Two-Tier Execution Sandboxing Engine
====================================
Provides secure, isolated test and command execution for autonomous agents.
Tier 1: Hardened Host Process Sandbox (stripped environment, isolated directory, process group kill).
Tier 2: Warm Docker Container Sandbox (ephemeral container, zero network, memory limits, sub-50ms docker exec).
"""

import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Tuple

try:
    from core.config import get_safe_subprocess_env
    from core.processes import kill_process_tree
except ImportError:
    from config import get_safe_subprocess_env
    from processes import kill_process_tree

logger = logging.getLogger("Sandbox")


class HostProcessSandbox:
    """Tier 1: Bare-metal hardened subprocess execution with stripped environment."""

    def __init__(self, task_id: str, workspace_dir: Path):
        self.task_id = task_id
        self.workspace_dir = workspace_dir
        self.sandbox_dir = workspace_dir / ".sandbox" / f"run_{task_id}"
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.safe_env = get_safe_subprocess_env({"PYTHONPATH": str(self.sandbox_dir)})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Clean up temporary sandbox directory if desired
        pass

    def execute(self, command: str, timeout: int = 30) -> Tuple[int, str, str]:
        """Runs command in hardened host environment with environment scrubbing."""
        logger.info(f"[sandbox:host] Executing: {command} (timeout={timeout}s)")
        try:
            # On Windows, use cmd or powershell, on POSIX use sh
            shell_executable = None
            if sys.platform != "win32":
                shell_executable = "/bin/sh"

            proc = subprocess.Popen(
                command,
                cwd=str(self.sandbox_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.safe_env,
                shell=True,
                executable=shell_executable
            )

            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                return proc.returncode, stdout, stderr
            except subprocess.TimeoutExpired:
                logger.error(f"[sandbox:host] Command timed out after {timeout}s: {command}")
                # shell=True: proc is the shell; its children (the actual test run) must go too
                kill_process_tree(proc.pid, logger=logger)
                stdout, stderr = proc.communicate()
                return -1, stdout, stderr + f"\n[ERROR] Command timed out after {timeout} seconds."

        except Exception as e:
            logger.exception(f"[sandbox:host] Subprocess execution error: {e}")
            return 1, "", str(e)


class WarmDockerSandbox:
    """Tier 2: Ephemeral, warm Docker container with zero network and memory boundaries."""

    def __init__(self, task_id: str, workspace_dir: Path):
        self.task_id = task_id
        self.workspace_dir = workspace_dir
        self.sandbox_dir = workspace_dir / ".sandbox" / f"run_{task_id}"
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.container_name = f"alwayson_sandbox_{task_id}_{int(time.time())}"
        self.docker_bin = shutil.which("docker") or "docker"

    def __enter__(self):
        # Spin up warm container with tail -f /dev/null
        logger.info(f"[sandbox:docker] Launching warm container '{self.container_name}'...")
        abs_mount = str(self.sandbox_dir.resolve()).replace("\\", "/")
        cmd = [
            self.docker_bin, "run", "-d", "--rm",
            "--name", self.container_name,
            "--network", "none",
            "--memory", "512m",
            "--cpus", "1.0",
            "-v", f"{abs_mount}:/workspace:rw",
            "-w", "/workspace",
            "python:3.11-slim",
            "tail", "-f", "/dev/null"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error(f"[sandbox:docker] Failed to launch warm container: {res.stderr}")
            raise RuntimeError(f"Docker sandbox start failed: {res.stderr}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Force remove container on exit
        logger.info(f"[sandbox:docker] Tearing down container '{self.container_name}'...")
        subprocess.run(
            [self.docker_bin, "rm", "-f", self.container_name],
            capture_output=True,
            timeout=10
        )

    def execute(self, command: str, timeout: int = 30) -> Tuple[int, str, str]:
        """Executes command in warm container in sub-50ms via docker exec."""
        logger.info(f"[sandbox:docker] docker exec: {command}")
        cmd = [
            self.docker_bin, "exec", self.container_name,
            "sh", "-c", command
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"[ERROR] Docker command timed out after {timeout} seconds."


def is_docker_available() -> bool:
    """Checks if Docker daemon is online and responsive."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    try:
        res = subprocess.run([docker_bin, "info"], capture_output=True, timeout=3)
        return res.returncode == 0
    except Exception:
        return False


def get_sandbox(task_id: str, workspace_dir: Path):
    """Factory returning Tier 2 Docker sandbox if available, else Tier 1 Host Process sandbox."""
    if is_docker_available():
        logger.info("[sandbox] Docker detected. Using Tier 2 Warm Docker Sandbox.")
        return WarmDockerSandbox(task_id=task_id, workspace_dir=workspace_dir)
    else:
        logger.info("[sandbox] Docker not available. Using Tier 1 Hardened Host Process Sandbox.")
        return HostProcessSandbox(task_id=task_id, workspace_dir=workspace_dir)
