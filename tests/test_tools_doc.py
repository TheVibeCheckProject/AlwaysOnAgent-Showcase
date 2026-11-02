"""docs/TOOLS.md names every tool the chat can be given, like test_api_contract does for docs/API.md. 2026-10-28 audit
6.1: three of the 71 tools (computer_task, research_ai_opportunities, today_settings) weren't in it, while API.md,
which has a test, was exact."""
import re
from pathlib import Path

from core.assistant_tools import TOOL_GROUPS

ROOT = Path(__file__).resolve().parent.parent


def test_every_tool_in_a_group_is_in_tools_md():
    doc = (ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z_]+)`", doc))
    tools = {t for group in TOOL_GROUPS.values() for t in group}
    assert not tools - named, f"tools missing from docs/TOOLS.md: {sorted(tools - named)}"
