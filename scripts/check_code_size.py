"""Enforce the code-size rule (AI_HANDOFF.md rule 11) for source code.

Checks every function is at most 50 lines and every file is at most 500
lines, in core/, interfaces/, scripts/ and supervisor.py. Exits non-zero
with the list of violations, so CI fails the build when the rule breaks.

Deliberately NOT checked here:
- tests/ — test functions are long by nature (setup + assertions) and the
  split waves never touched them.
- Class method counts — the 6 mixin classes (e.g. _TasksMixin, _PromptMixin)
  group 21-26 related methods by design; splitting them is a structural
  decision, not a size fix.
"""
import ast
import pathlib
import sys

MAX_FUNC_LINES = 50
MAX_FILE_LINES = 500
# Source roots, relative to the repo root (this script lives in scripts/).
SOURCE_DIRS = [pathlib.Path("core"), pathlib.Path("interfaces"), pathlib.Path("scripts")]
SOURCE_FILES = [pathlib.Path("supervisor.py")]


def check_file(path: pathlib.Path) -> list[str]:
    violations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations
    lines = text.splitlines()
    if len(lines) > MAX_FILE_LINES:
        violations.append(f"{path}: {len(lines)} lines (max {MAX_FILE_LINES})")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return violations
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n = (node.end_lineno or node.lineno) - node.lineno + 1
            if n > MAX_FUNC_LINES:
                violations.append(f"{path}: {node.name} = {n} lines (max {MAX_FUNC_LINES})")
    return violations


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    paths: list[pathlib.Path] = list(SOURCE_FILES)
    for d in SOURCE_DIRS:
        paths.extend(sorted((root / d).rglob("*.py")))
    violations: list[str] = []
    for p in paths:
        rel = p.relative_to(root) if p.is_absolute() else p
        full = root / rel
        if "__pycache__" in str(full):
            continue
        violations.extend(check_file(full))
    if violations:
        print(f"Code-size rule violated ({len(violations)}):")
        for v in violations:
            print(f"  {v}")
        return 1
    print("Code-size rule OK: no function over 50 lines, no file over 500 lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
