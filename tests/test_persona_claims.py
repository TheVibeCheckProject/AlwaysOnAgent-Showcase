"""The persona tells the chat model how the agent works (docs/IDEAS.md #6). When the code changes and the persona
doesn't, the model confidently describes things that no longer exist. Every code-like name it mentions must be
real: a chat tool, a memory table, the database path, a slash command the chat handles."""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.assistant_tools import GROUP_GUIDES, TOOL_GROUPS, AssistantTools
from core.database import DB_FILE, DatabaseManager
from core.memory import ASSISTANT_PERSONA, FINAL_REMINDER, MemoryManager, count_tokens

# The group guides are the persona's how-to for each tool group, sent only with that group (2026-10-27)
TEXT = ASSISTANT_PERSONA + FINAL_REMINDER + "\n".join(GROUP_GUIDES.values())
ROOT = Path(__file__).resolve().parent.parent


def _tables() -> set:
    db = DatabaseManager(Path(tempfile.mkdtemp(prefix="persona_")) / "p.db")
    MemoryManager(db, llm=None, embedder=None)
    return {r[0] for r in db.get_connection().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_every_snake_case_name_is_a_tool_or_a_table():
    names = set(re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", TEXT))
    tools = {t for group in TOOL_GROUPS.values() for t in group}
    tables = _tables()
    unknown = sorted(n for n in names if n not in tools and n not in tables)
    assert not unknown, f"the persona names things that don't exist: {unknown}"


def test_every_tool_it_names_can_be_called():
    tools = AssistantTools(DatabaseManager(Path(tempfile.mkdtemp(prefix="persona_")) / "t.db"), None, ROOT)
    every = {t for group in TOOL_GROUPS.values() for t in group}
    named = {n for n in re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", TEXT) if n in every}
    specs = {s["function"]["name"] for s in tools.specs([])}
    for name in named:
        assert hasattr(tools, name), f"{name} is in a tool group but has no method"
        assert name in specs, f"{name} has no tool description"


def test_the_database_path_and_slash_commands_are_real():
    assert "data/state.db" in TEXT and DB_FILE.relative_to(ROOT).as_posix() == "data/state.db"
    # The chat was split out of chat.py into chat_*.py modules (2026-11-04); the
    # slash commands live across them, so read all of them, not just chat.py.
    chat = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "interfaces" / "hud").glob("chat*.py"))
    )
    for cmd in ("/image", "/status", "/task"):
        if cmd in TEXT:
            assert f'"{cmd}' in chat, f"the persona mentions {cmd}, but the chat doesn't handle it"


def test_the_persona_stays_small():
    """It goes with every message and every tool round. 3,280 tokens until 2026-10-27, when each tool group's
    how-to moved to GROUP_GUIDES (sent only with its group). A new rule for one group belongs there."""
    assert count_tokens(ASSISTANT_PERSONA) <= 2000, count_tokens(ASSISTANT_PERSONA)


def test_every_tool_group_has_a_guide_that_names_only_its_own_tools():
    every = {t for group in TOOL_GROUPS.values() for t in group}
    for group, guide in GROUP_GUIDES.items():
        named = set(re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", guide)) & every
        assert named <= set(TOOL_GROUPS[group]), f"{group}'s guide names other groups' tools: {named - set(TOOL_GROUPS[group])}"
    assert set(GROUP_GUIDES) <= set(TOOL_GROUPS)
