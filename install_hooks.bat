@echo off
setlocal
cd /d "%~dp0"
title Install git hooks (test gate before deploy)

if not exist ".git" (
    echo [ERROR] Not a git repository yet. Run "git init" first, then re-run this.
    pause
    exit /b 1
)

if not exist ".git\hooks" mkdir ".git\hooks"
copy /Y "scripts\hooks\pre-push" ".git\hooks\pre-push" >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy pre-push hook.
    pause
    exit /b 1
)

echo Installed pre-push hook -^> .git\hooks\pre-push
echo From now on "git push" (deploy) runs the full test suite first and
echo aborts the push if any test fails.
pause
