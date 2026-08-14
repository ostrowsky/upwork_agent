@echo off
setlocal
cd /d "%~dp0"
title Upwork AI Sales Assistant - Clean login

rem Refuse to run elevated: an Administrator-launched Chromium/Edge often
rem can't set up its sandbox properly, shows the "--no-sandbox" warning
rem banner, and looks like a bot to Upwork (this is what caused the
rem "Due to technical difficulties" login block).
net session >nul 2>&1
if not errorlevel 1 (
    echo [ABORT] This terminal is running as Administrator.
    echo Close this window and open a NORMAL ^(non-elevated^) terminal instead,
    echo then run login_clean.bat again from there.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run: python -m venv .venv  ^&^&  .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo Stopping all msedge.exe (agent browser profile) ...
powershell -NoProfile -Command "Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force" >nul 2>&1

echo Stopping worker.py / streamlit UI processes ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'worker\.py|streamlit run app\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo ============================================================
echo  Manual Upwork login (non-elevated).
echo  A browser window will open - log in yourself, then press Enter here.
echo ============================================================
echo.

".venv\Scripts\python.exe" upwork_connect.py --login

echo.
pause
