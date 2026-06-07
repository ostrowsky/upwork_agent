@echo off
setlocal
cd /d "%~dp0"
title Upwork AI Sales Assistant - Autonomous Agent

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run: python -m venv .venv  ^&^&  .venv\Scripts\python.exe -m pip install -r requirements.txt
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
echo  Upwork AI Sales Assistant - AUTONOMOUS AGENT (worker)
echo ------------------------------------------------------------
echo  Full pipeline each tick:
echo    probe -^> ingest -^> qualify -^> draft -^> submit -^> messages -^> report
echo.
echo  Settings come from .env:
echo    AUTO_SUBMIT=1   -^> REAL proposals are sent (spends connects!)
echo    MSG_AUTO_SEND   -^> worker only imports/drafts replies (never auto-sends)
echo    WORKER_INTERVAL -^> seconds between ticks
echo.
echo  Logs:   data\worker.log     Status: data\worker_status.json
echo  Daily report + anomaly alerts go to Telegram/Discord (if configured).
echo.
echo  WARNING: do not run UI browser actions at the same time (Edge profile lock).
echo  Make sure you are logged into Upwork in the agent Edge profile first.
echo  Press Ctrl+C to stop.
echo ============================================================
echo.

".venv\Scripts\python.exe" worker.py

echo.
echo Agent stopped.
pause
