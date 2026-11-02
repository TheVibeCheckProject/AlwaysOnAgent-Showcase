"""Drift guard for the docs: docs/AI_HANDOFF.md is the single source of truth, and it must not drift from the code.

Why (2026-10-07): the docs had drifted from the code in several places at once. The README, .env.example, the
supervisor's startup banner and IDEAS.md each named a different first chat model than core/llm.py, three test
counts were in circulation, two deploy procedures, two "start here" files, and the README linked to a file that
didn't exist. These checks catch the cheapest of those mistakes before they ship.

Deliberately light: pytest and the standard library only. core/llm.py is parsed with `ast`, never imported, so
this test can't reach a model, Ollama or the network, and it runs in milliseconds.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HANDOFF = ROOT / "docs" / "AI_HANDOFF.md"

# Every per-AI entry file is a thin pointer to docs/AI_HANDOFF.md
ENTRY_FILES = [
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".github/copilot-instructions.md",
    ".agents/rules/start_here.md",
]

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _first_chat_model() -> str:
    tree = ast.parse((ROOT / "core" / "llm.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "CloudLLM":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "CHAT_MODELS" for t in stmt.targets):
                    return ast.literal_eval(stmt.value)[0]
    raise AssertionError("CloudLLM.CHAT_MODELS not found in core/llm.py")


def _markdown_files():
    files = sorted((ROOT / "docs").rglob("*.md"))
    files += [ROOT / f for f in ENTRY_FILES if (ROOT / f).exists()]
    files += sorted((ROOT / ".agents").rglob("*.md")) if (ROOT / ".agents").exists() else []
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def test_handoff_names_the_first_chat_model_from_code():
    first = _first_chat_model()
    text = HANDOFF.read_text(encoding="utf-8")
    m = re.search(r"First chat model:\s*`([^`]+)`", text)
    assert m, "docs/AI_HANDOFF.md must have a line 'First chat model: `<route>`'"
    assert m.group(1) == first, (
        f"docs/AI_HANDOFF.md says the first chat model is {m.group(1)!r}, but core/llm.py CHAT_MODELS starts "
        f"with {first!r}. Code wins: update the doc in the same commit.")


def test_relative_markdown_links_resolve():
    broken = []
    for md in _markdown_files():
        text = _INLINE_CODE.sub("", _FENCE.sub("", md.read_text(encoding="utf-8")))
        for target in _LINK.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                continue        # http(s), mailto, ... and in-page anchors
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not path:
                continue
            resolved = (md.parent / path).resolve()
            # Links to sibling repos (../../TiktokVideos/...) live outside this repo and can't be checked here
            if ROOT not in resolved.parents and resolved != ROOT:
                continue
            if not resolved.exists():
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not broken, "Broken relative links:\n" + "\n".join(broken)


def test_entry_files_point_to_the_handoff():
    missing, not_pointing = [], []
    for name in ENTRY_FILES:
        f = ROOT / name
        if not f.exists():
            missing.append(name)
        elif "docs/AI_HANDOFF.md" not in f.read_text(encoding="utf-8"):
            not_pointing.append(name)
    assert not missing, f"Entry files missing: {missing}"
    assert not not_pointing, f"Entry files that don't point to docs/AI_HANDOFF.md: {not_pointing}"
