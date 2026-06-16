@echo off
setlocal
cd /d "%~dp0"
title Upwork AI Agent - SINGLE TICK (test, safe)

rem One pass of the pipeline for testing. Safe by default: no real submits/messages.
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
    echo [ABORT] Tests failed - single tick cancelled. Fix tests first.
    pause
    exit /b 1
)

echo ============================================================
echo  AUTONOMOUS AGENT - SINGLE TICK (test)
echo  Runs the pipeline ONCE, then exits.  AUTO_SUBMIT=0 (no connects spent).
echo  Use this to verify login/session and that ingest/qualify/draft work.
echo  Logs: data\worker.log
echo ============================================================
echo.

".venv\Scripts\python.exe" worker.py --once

echo.
echo Single tick done.
pause
