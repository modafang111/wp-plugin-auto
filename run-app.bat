@echo off
rem Shared launcher. Always run from this folder.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" app.py %*
exit /b %ERRORLEVEL%
