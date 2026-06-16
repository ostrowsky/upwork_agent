@echo off
setlocal
cd /d "%~dp0"
title Upwork AI Sales Assistant - UI

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run: python -m venv .venv  ^&^&  .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem Kill any previously-running UI instances so there is exactly ONE (avoids
rem duplicate Streamlit processes serving stale code).
echo Closing previous UI instances...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'streamlit run app.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

call "%~dp0run_tests.bat"
if errorlevel 1 (
    echo.
    echo [ABORT] Tests failed - UI launch cancelled. Fix tests first.
    pause
    exit /b 1
)

echo ============================================================
echo  Upwork AI Sales Assistant - UI (Streamlit)
echo  Opens http://localhost:8501 in your browser.
echo  Do NOT run browser actions here while the agent (run_agent.bat) is active.
echo  Press Ctrl+C in this window to stop.
echo ============================================================
echo.

".venv\Scripts\python.exe" -m streamlit run app.py

echo.
echo UI stopped.
pause
