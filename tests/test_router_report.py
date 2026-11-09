"""scripts/router_report.py: read-only numbers on the chat's tool router, from a fake data folder."""

import datetime
import importlib.util
import json
import sys
from pathlib import Path

from core.database import DatabaseManager
from core.llm_usage import UsageLog, usage_purpose

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "router_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("router_report", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch, capsys, data):
    report = _load()
    monkeypatch.setattr(sys, "argv", ["router_report.py", "--data", str(data)])
    assert report.main() == 0
    return capsys.readouterr().out


def test_report_counts_misses_fallbacks_and_flash_lite(tmp_path, monkeypatch, capsys):
    now = datetime.datetime.now().astimezone()
    old = (now - datetime.timedelta(days=30)).isoformat()
    lines = [{"time": old, "groups": [], "added_groups": ["mail"], "tools_called": [], "picker": "router"}]
    lines += [{"time": now.isoformat(), "groups": ["web"], "added_groups": [], "tools_called": ["get_weather"],
               "tool_tokens": 1200, "picker": "router"} for _ in range(6)]
    lines += [{"time": now.isoformat(), "groups": [], "added_groups": ["mail"], "tools_called": ["gmail_search"],
               "tool_tokens": 900, "picker": "keywords"} for _ in range(4)]
    (tmp_path / "tool_routing.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    log = UsageLog(DatabaseManager(tmp_path / "state.db"))
    with usage_purpose("route_tools"):
        for _ in range(6):
            log.record("gemini-3.5-flash-lite", {"prompt_tokens": 300}, duration_ms=4500, provider="gemini")
        log.record_failure("gemini", "gemini-3.5-flash-lite", "timeout", duration_ms=5000)
    with usage_purpose("chat"):
        log.record("gemini-3.5-flash-lite", {"prompt_tokens": 3000}, duration_ms=2000, provider="gemini")
        log.record_failure("gemini", "gemini-3.8-flash", "timeout", duration_ms=25000)

    out = _run(monkeypatch, capsys, tmp_path)
    assert "Replies: 10; needed more_tools (a missed group): 4 (40%)" in out     # the 30-day-old line is outside
    assert "mail x4" in out
    assert "router: 0 of 6 missed" in out and "keywords: 4 of 4 missed" in out
    assert "Answered: 6; failed attempts: 1" in out
    assert "router 6 (86%)" in out                                              # 6 of 7 Flash-Lite calls
    assert "8 calls (1 failed)" in out
    assert "Timed out: 1: gemini:gemini-3.8-flash x1" in out
    assert "Misses are high" in out and "fell back to keywords on 40%" in out and "router is slow" in out


def test_report_runs_on_an_empty_data_folder(tmp_path, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, tmp_path)
    assert "No chat replies logged" in out
    assert "Nothing stands out" in out
