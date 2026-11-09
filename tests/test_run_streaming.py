"""core/task_engines.run_streaming, 2026-10-28 audit 3.6 (stdin written before stdout was read could deadlock outside
the timeout) and 3.7 (every output line was kept and logged)."""
import logging
import sys
import threading

from core import task_engines
from core.task_engines import QUOTA_EXIT_CODE, run_streaming

LOG = logging.getLogger("test-run-streaming")


def _run(code: str, tmp_path, stdin_text=None, timeout=30):
    return run_streaming([sys.executable, "-c", code], tmp_path, None, "task-t", timeout, LOG, "t",
                         stdin_text=stdin_text)


def test_a_big_prompt_to_a_program_that_prints_first_doesnt_deadlock(tmp_path):
    # It prints ~200 KB before reading its input: with the prompt written first, both sides waited on full pipes
    code = "import sys\nfor i in range(20000): print('x' * 9)\nsys.stdout.flush()\nprint(len(sys.stdin.read()))"
    result = {}
    worker = threading.Thread(target=lambda: result.update(out=_run(code, tmp_path, stdin_text="p" * 300_000)),
                              daemon=True)
    worker.start()
    worker.join(20)
    assert not worker.is_alive(), "run_streaming deadlocked"
    exit_code, output = result["out"]
    assert exit_code == 0 and output.rstrip().endswith("300000")


def test_only_the_head_and_tail_of_a_long_output_are_kept(tmp_path):
    exit_code, output = _run("for i in range(1000): print(f'line {i}')", tmp_path)
    lines = output.splitlines()
    assert exit_code == 0 and lines[0] == "line 0" and lines[-1] == "line 999"
    assert f"[... {1000 - task_engines.OUTPUT_HEAD - task_engines.OUTPUT_TAIL} lines not kept ...]" in output
    assert len(lines) == task_engines.OUTPUT_HEAD + task_engines.OUTPUT_TAIL + 1


def test_a_quota_message_in_the_middle_is_still_found(tmp_path):
    code = ("import sys\nfor i in range(500): print(f'step {i}')\n"
            "print('Error: quota reached for gemini. Resets in 3h12m')\n"
            "for i in range(800): print(f'cleanup {i}')\nsys.exit(1)")
    exit_code, output = _run(code, tmp_path)
    assert exit_code == QUOTA_EXIT_CODE and "resets in 3h12m" in output
