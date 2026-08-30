@echo off
rem Sync this repo with Cursor Cloud Agent branches via GitHub.
rem Optional: set CURSOR_API_KEY (or put it in .env) to attach worktrees for agent branches.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" "%~dp0sync-cursor-cloud.py" %*
exit /b %ERRORLEVEL%
