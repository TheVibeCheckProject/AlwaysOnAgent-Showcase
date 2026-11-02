@echo off
title AlwaysOnAgent Test Suite
cd /d "%~dp0\.."
echo ========================================================
echo Running AlwaysOnAgent Automated Test Suites
echo ========================================================
set FAILED=
for %%T in (
    test_maintenance_and_ui
    test_rest_endpoints_and_queueing
    test_worker
    test_memory
    test_memory_recall
    test_assistant_tools
    test_media_delivery
    test_backup
    test_robustness
    test_agentwork_link
) do (
    echo.
    echo [%%T] Running tests/%%T.py ...
    python tests/%%T.py
    if errorlevel 1 (
        echo [FAIL] %%T failed!
        set FAILED=1
    )
)

echo.
echo [Pytest-Only Suites] Running pytest on remaining suites...
python -m pytest tests/test_action_tools.py tests/test_api_chat.py tests/test_deskai_phase2.py tests/test_task_cards.py tests/test_llm_routing.py
if errorlevel 1 (
    echo [FAIL] Pytest suites failed!
    set FAILED=1
)

echo.
echo ========================================================
if defined FAILED (
    echo SOME SUITES FAILED - see the output above.
    echo ========================================================
    pause
    exit /b 1
)
echo ALL SUITES PASSED CLEANLY WITH ZERO DATABASE POLLUTION!
echo ========================================================
pause
