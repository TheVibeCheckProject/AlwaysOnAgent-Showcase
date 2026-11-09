"""Sandbox QA of the tests a coding task wrote (core/task_engines.run_sandbox_qa), 2026-10-28 audit 1.2: without Docker
the "sandbox" was only a folder, so AI-written tests ran with the owner's full rights (the Mini has no Docker); file
names from the model went into a shell command; and an error counted as a pass."""
import pytest

from core import sandbox
from core.task_engines import FileArtifactHelper


@pytest.fixture
def work(tmp_path):
    (tmp_path / "test_ok.py").write_text("import unittest\n", encoding="utf-8")
    return tmp_path


class Refused:
    """Stands in for WarmDockerSandbox: any use of it fails the test that didn't expect one."""
    def __init__(self, *a, **k):
        raise AssertionError("no sandbox should have been started")


def test_without_docker_ai_written_tests_are_not_run(work, monkeypatch):
    monkeypatch.setattr(sandbox, "is_docker_available", lambda: False)
    monkeypatch.setattr(sandbox, "WarmDockerSandbox", Refused)
    passed, report = FileArtifactHelper.run_sandbox_qa("task-1", work, ["test_ok.py"])
    assert passed is True and "not run" in report and "test_ok.py" in report


def test_an_unusual_test_file_name_is_never_run(work, monkeypatch):
    monkeypatch.setattr(sandbox, "is_docker_available", lambda: True)
    monkeypatch.setattr(sandbox, "WarmDockerSandbox", Refused)
    passed, report = FileArtifactHelper.run_sandbox_qa("task-2", work, ["test_a & calc & b.py"])
    assert passed is True and "unusual file names" in report


def test_a_sandbox_that_fails_means_not_verified(work, monkeypatch):
    class Broken:
        def __init__(self, *a, **k):
            self.sandbox_dir = work

        def __enter__(self):
            raise RuntimeError("Docker sandbox start failed")

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(sandbox, "is_docker_available", lambda: True)
    monkeypatch.setattr(sandbox, "WarmDockerSandbox", Broken)
    passed, report = FileArtifactHelper.run_sandbox_qa("task-3", work, ["test_ok.py"])
    assert passed is False and "couldn't verify" in report


def test_in_docker_the_file_name_is_quoted_for_the_shell(work, monkeypatch):
    ran = []

    class Fake:
        def __init__(self, *a, **k):
            self.sandbox_dir = work / "box"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, command, timeout=30):
            ran.append(command)
            return 0, "ok", ""
    monkeypatch.setattr(sandbox, "is_docker_available", lambda: True)
    monkeypatch.setattr(sandbox, "WarmDockerSandbox", Fake)
    passed, _ = FileArtifactHelper.run_sandbox_qa("task-4", work, ["test_ok.py"])
    assert passed is True and ran == ["python -m unittest test_ok.py"]
