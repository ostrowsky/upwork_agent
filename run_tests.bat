@echo off
rem Runs the full test suite. Exit code 0 = all passed, non-zero = failure.
rem Called by the launchers (run_*.bat) as a gate before any launch.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. See README.
    exit /b 1
)

echo ------------------------------------------------------------
echo  Running full test suite (gate before launch/deploy)...
echo ------------------------------------------------------------
".venv\Scripts\python.exe" -m pytest tests/ -q
exit /b %errorlevel%
