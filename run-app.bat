@echo off
rem 共通起動。このファイルと同じフォルダをカレントにする。
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" app.py %*
exit /b %ERRORLEVEL%
