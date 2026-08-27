@echo off
rem Task Scheduler から呼ぶ用。この bat と同じフォルダをカレントにする。
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" app.py --deliver-orders
exit /b %ERRORLEVEL%
