"""File artifacts and sandbox QA for a task engine's reply
=====================================================
Split out of core/task_engines.py §3 (FileArtifactHelper) and §9 (_save_and_check)
on 2026-11-04; moved into core/task_engines/ on 2026-11-04, re-exported by the
package for backwards compatibility.

_save_and_check lives here (not with the cloud/ollama engines) because it only
saves the reply's files and runs their tests — both CloudEngine and OllamaEngine
call it.
"""

import logging
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

try:
    from .constants import SAFE_TEST_NAME
except ImportError:
    from core.task_engines.constants import SAFE_TEST_NAME  # type: ignore[no-redef]

class FileArtifactHelper:
    """Extracts synthesized code and documentation into workspace files."""

    @staticmethod
    def apply_file_artifacts(response_text: str, working_dir: Path, prompt: str = "", logger: logging.Logger | None = None):
        log = logger or logging.getLogger("FileArtifactHelper")
        response_text = (response_text or "").replace("\r\n", "\n")      # Windows line endings break the patterns
        created_files = FileArtifactHelper._extract_fenced_files(response_text, working_dir, log)
        if FileArtifactHelper._materialize_prompt_target(prompt, response_text, working_dir, created_files, log) is False:
            return
        return list(created_files)

    @staticmethod
    def _extract_fenced_files(response_text: str, working_dir: Path, log: logging.Logger) -> set:
        """Writes every fenced code block that names a file into working_dir."""
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
                except OSError as e:
                    log.warning(f"Could not write file {file_name}: {e}")
        return created_files

    @staticmethod
    def _materialize_prompt_target(prompt: str, response_text: str, working_dir: Path, created_files: set, log: logging.Logger) -> bool:
        """Writes the single file the prompt names, if it is not there yet. Returns False when the name escapes working_dir."""
        # Check if prompt requested a specific file (e.g. "Create a file named SUMMARY.txt")
        file_match = re.search(r"(?:create|write|scaffold|generate)\s+(?:a\s+file\s+)?(?:named\s+)?([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)", prompt, re.IGNORECASE)
        if file_match:
            target_name = file_match.group(1).strip()
            target_path = (working_dir / target_name).resolve()
            if not target_path.is_relative_to(working_dir.resolve()):
                log.error(f"Security Alert: Target file path traversal blocked for '{target_name}'")
                return False

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
                    except OSError as e:
                        log.warning(f"Failed writing {target_name}: {e}")
        return True


    @staticmethod
    def _select_test_files(created_files: list[str]) -> tuple[list[str], str]:
        """Picks the runnable unit-test files out of the created files."""
        test_files = [f.replace("\\", "/") for f in created_files
                      if Path(f).name.startswith("test_") and f.endswith(".py")]
        # The names come from the model's answer: only plain ones. "test_a & <command> & b.py" passed the test_*.py
        # check and went into a shell command (2026-10-28 audit 1.2)
        odd = [f for f in test_files if not SAFE_TEST_NAME.fullmatch(f)]
        test_files = [f for f in test_files if f not in odd]
        skipped_note = f"\nNot run (unusual file names): {', '.join(odd)}" if odd else ""
        return test_files, skipped_note

    @staticmethod
    def run_sandbox_qa(task_id: str, working_dir: Path, created_files: list[str], logger: logging.Logger | None = None) -> tuple[bool, str]:
        """Runs unit tests inside isolated sandbox to verify code correctness before approval."""
        test_files, skipped_note = FileArtifactHelper._select_test_files(created_files)
        if not test_files:
            return True, "No unit test files identified for sandboxed verification." + skipped_note

        log = logger or logging.getLogger("SandboxQA")
        try:
            from core.sandbox import WarmDockerSandbox, is_docker_available
        except ImportError:
            from sandbox import WarmDockerSandbox, is_docker_available  # type: ignore[no-redef]
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


def _save_and_check(task: dict[str, Any], working_dir: Path, reply: str, prompt: str,
                    logger: logging.Logger) -> tuple[int, str]:
    """Saves the reply's files into the task folder and runs any tests it wrote."""
    created = FileArtifactHelper.apply_file_artifacts(reply, working_dir, prompt=prompt, logger=logger)
    passed, qa_report = FileArtifactHelper.run_sandbox_qa(task.get("id", "task-temp"), working_dir, created, logger=logger)
    if not passed:
        logger.warning(f"Sandboxed QA failed:\n{qa_report}")
        return 1, f"Generated code failed sandbox verification tests:\n{qa_report}\n\nFull Output:\n{reply}"
    return 0, reply
