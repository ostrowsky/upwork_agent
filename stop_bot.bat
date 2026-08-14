@echo off
setlocal
title Upwork AI Sales Assistant - Stop bot processes

echo Stopping all msedge.exe (agent browser profile) ...
powershell -NoProfile -Command "Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force" >nul 2>&1

echo Stopping worker.py / streamlit UI processes ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'worker\.py|streamlit run app\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo Done. Safe to run upwork_connect.py --login or run_ui.bat / run_agent.bat now.
pause
