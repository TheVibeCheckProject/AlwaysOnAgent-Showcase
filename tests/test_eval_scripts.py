"""The eval scripts' own housekeeping (scripts/eval_*.py)."""

import importlib.util
import sys
from pathlib import Path

from core.database import DatabaseManager

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_sycophancy_evals_scratch_copy_of_memory_is_deleted(tmp_path):
    """2026-10-31 review, defect 9: every run left a full copy of the live memory database in %TEMP%."""
    eval_sycophancy = _load("eval_sycophancy")
    folder = tmp_path / "syceval-x"
    folder.mkdir()
    db = DatabaseManager(folder / "state.db")
    db.set_state("k", "v")                                     # an open connection, as during a run
    eval_sycophancy.remove_scratch(db)
    assert not folder.exists()
