@echo off
setlocal
cd /d "%~dp0"
title Upwork AI Agent - SAFE MODE (no real submits/messages)

rem Safe autonomous loop: overrides .env so NOTHING irreversible happens.
rem The agent searches/qualifies/drafts/imports messages/sends reports,
rem but does NOT submit proposals or send client messages.
set AUTO_SUBMIT=0
set MSG_AUTO_SEND=0

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. See README.
    pause
    exit /b 1
)

call "%~dp0run_tests.bat"
if errorlevel 1 (
    echo.
    echo [ABORT] Tests failed - agent launch cancelled. Fix tests first.
    pause
    exit /b 1
)

echo ============================================================
echo  AUTONOMOUS AGENT - SAFE MODE
echo  AUTO_SUBMIT=0, MSG_AUTO_SEND=0  (no connects spent, no messages sent)
echo  Full pipeline runs except the irreversible send steps.
echo  Logs: data\worker.log   Press Ctrl+C to stop.
echo ============================================================
echo.

".venv\Scripts\python.exe" worker.py

echo.
echo Agent stopped.
pause
