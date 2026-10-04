@echo off
rem Refresh the BASE admin session every 3 hours.
setlocal
cd /d "%~dp0"
set "BAT=%~dp0base-keepalive.bat"
if not exist "%BAT%" (
  echo base-keepalive.bat was not found.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_keepalive_task.ps1"
if errorlevel 1 (
  echo Failed. Open Command Prompt as Administrator and run this file again.
  pause
  exit /b 1
)

echo.
echo Task created: base-wp-ja-auto-keepalive
echo Schedule: every 3 hours
echo Running once to verify...
schtasks /run /tn "base-wp-ja-auto-keepalive"
echo Check logs in this folder.
pause
exit /b 0
