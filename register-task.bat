@echo off
rem Create the 5-minute Task Scheduler job for deliver-orders.bat
setlocal
cd /d "%~dp0"
set "BAT=%~dp0deliver-orders.bat"
if not exist "%BAT%" (
  echo deliver-orders.bat was not found.
  pause
  exit /b 1
)

schtasks /create /f /tn "base-wp-ja-auto-deliver" /sc minute /mo 5 /it /tr "\"%BAT%\""
if errorlevel 1 (
  echo Failed. Open Command Prompt as Administrator and run this file again.
  pause
  exit /b 1
)

echo.
echo Task created: base-wp-ja-auto-deliver
echo Running once to verify...
schtasks /run /tn "base-wp-ja-auto-deliver"
echo Check logs in this folder.
pause
exit /b 0
