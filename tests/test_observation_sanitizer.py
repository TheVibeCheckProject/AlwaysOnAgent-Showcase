import pytest
from core.observation_sanitizer import (
    strip_tracking_params,
    collapse_whitespace,
    sanitize_email_body,
    clean_web_text,
    format_task_row,
    format_task_details,
    compress_log_lines,
)


def test_strip_tracking_params():
    url = "https://example.com/article?id=42&utm_source=twitter&utm_medium=social&utm_campaign=launch"
    cleaned = strip_tracking_params(url)
    assert cleaned == "https://example.com/article?id=42"

    url2 = "https://news.ycombinator.com/item?id=12345"
    assert strip_tracking_params(url2) == url2

    url3 = "https://example.org/search?q=test&fbclid=IwAR12345&gclid=XYZ987"
    assert strip_tracking_params(url3) == "https://example.org/search?q=test"

    # Functional parameters like ?ref= and ?source= must NOT be stripped (e.g. GitHub branch or code source)
    url_github = "https://github.com/owner/repo/blob/main/README.md?ref=feat-branch&source=github"
    assert strip_tracking_params(url_github) == url_github


def test_collapse_whitespace():
    raw = "Line 1   \n\n\n\n\nLine 2  \n\nLine 3"
    collapsed = collapse_whitespace(raw)
    assert collapsed == "Line 1\n\nLine 2\n\nLine 3"


def test_sanitize_email_body_quotes_and_disclaimers():
    email = (
        "Hi Alex,\n\n"
        "The project deployment succeeded on schedule.\n\n"
        "On Mon, Sep 29, 2026 at 4:32 PM, Bob <bob@example.com> wrote:\n"
        "> Will the deployment finish today?\n"
        "> Thanks,\n"
        "> Bob\n\n"
        "This email and any files transmitted with it are confidential and intended solely for the use of the recipient.\n"
        "If you have received this message in error please delete all copies.\n"
    )
    cleaned = sanitize_email_body(email)
    assert "Hi Alex," in cleaned
    assert "The project deployment succeeded on schedule." in cleaned
    assert "[Quoted reply thread omitted]" in cleaned
    assert "Will the deployment finish today?" not in cleaned
    assert "confidential and intended solely" not in cleaned


def test_sanitize_email_body_unsubscribe_footer():
    email = (
        "Your order #98765 has shipped via UPS.\n\n"
        "Tracking number: 1Z9999999999999999\n\n"
        "Unsubscribe | Manage Preferences\n"
        "You are receiving this email because you subscribed to updates from Store Inc.\n"
    )
    cleaned = sanitize_email_body(email)
    assert "Your order #98765 has shipped" in cleaned
    assert "Tracking number" in cleaned
    assert "Unsubscribe" not in cleaned
    assert "subscribed to updates" not in cleaned


def test_clean_web_text():
    raw_html_text = (
        "Article Headline\n\n"
        "Accept all cookies\n"
        "Cookie preferences\n"
        "This is the actual article content describing the news event.\n\n\n\n"
        "Skip to main content\n"
        "More in-depth details here.\n"
    )
    cleaned = clean_web_text(raw_html_text)
    assert "Article Headline" in cleaned
    assert "This is the actual article content" in cleaned
    assert "More in-depth details here." in cleaned
    assert "Accept all cookies" not in cleaned
    assert "Cookie preferences" not in cleaned
    assert "Skip to main content" not in cleaned
    assert "\n\n\n" not in cleaned


def test_format_task_row():
    task = {
        "id": "task-042",
        "title": "Build landing page",
        "phase": "completed",
        "engine_used": "agentwork",
        "exit_code": 0,
        "completed_at": "2026-09-30T18:00:00",
    }
    row = format_task_row(task)
    assert row == "- [task-042] \"Build landing page\" | completed | agentwork | exit 0 | 2026-09-30 18:00"

    failed_task = {
        "id": "task-043",
        "title": "Render morning video",
        "phase": "failed",
        "engine": "video",
        "exit_code": 1,
        "last_error": "CUDA out of memory error during frame generation\nTraceback...",
        "created_at": "2026-09-30T18:05:00",
    }
    failed_row = format_task_row(failed_task)
    assert "[task-043]" in failed_row
    assert "failed" in failed_row
    assert "exit 1" in failed_row
    assert "err: CUDA out of memory" in failed_row


def test_format_task_details():
    task = {
        "id": "task-010",
        "title": "Fix navbar alignment",
        "phase": "completed",
        "needs_input": True,
        "prompt": "Align the navbar brand to the left and links to right",
        "output_summary": "Adjusted flexbox justify-content to space-between",
        "engine_used": "agentwork",
        "exit_code": 0,
        "created_at": "2026-09-30T17:00:00",
        "completed_at": "2026-09-30T17:04:00",
        "last_error": None,
        "retry_count": 0,
    }
    self_update_info = {
        "status": "applied",
        "message": "Update live and verified",
        "reason": "Fix alignment glitch",
        "steps": ["tests passed", "git merge", "restarted"],
    }
    details = format_task_details(task, self_update_info)
    assert "Task [task-010]: \"Fix navbar alignment\"" in details
    assert "Phase: completed" in details
    assert "Needs input: True" in details
    assert "Engine: agentwork | Exit code: 0" in details
    assert "Prompt: Align the navbar brand" in details
    assert "Output summary: Adjusted flexbox" in details
    assert "Self-Update: applied" in details
    assert "Reason: Fix alignment glitch" in details
    assert "Steps: tests passed; git merge; restarted" in details
    # Null or empty fields are omitted
    assert "null" not in details
    assert "Error:" not in details
    assert "Retries:" not in details


def test_compress_log_lines():
    logs = [
        "[2026-09-23 00:04:00] [WARNING] [cloud-groq] Rate limit (429) hit.",
        "[2026-09-23 00:04:01] [WARNING] [cloud-groq] Rate limit (429) hit.",
        "[2026-09-23 00:04:02] [WARNING] [cloud-groq] Rate limit (429) hit.",
        "[2026-09-23 00:04:03] [INFO] Switched engine to mistral.",
    ]
    compressed = compress_log_lines(logs)
    assert len(compressed) == 3
    assert "[WARNING] [cloud-groq] Rate limit (429) hit." in compressed[0]
    assert "repeated 2 more times" in compressed[1]
    assert "Switched engine to mistral." in compressed[2]
