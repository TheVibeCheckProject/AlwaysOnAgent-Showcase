@echo off
title Always-On Autonomous AI Agent
cd /d "%~dp0"
:launch
python supervisor.py
rem Exit code 3 = restart requested (POST /api/restart from DeskAI or a deploy). On the Mini, scripts\run_forever.ps1 is the launcher.
if %ERRORLEVEL% EQU 3 (
    echo Restart requested. Relaunching supervisor...
    goto launch
)
pause
