@echo off
rem Daily session refresh before the 02:00 register job.
setlocal
cd /d "%~dp0"
set "BAT=%~dp0base-keepalive.bat"
if not exist "%BAT%" (
  echo base-keepalive.bat was not found.
  pause
  exit /b 1
)

schtasks /create /f /tn "base-wp-ja-auto-keepalive" /sc daily /st 01:30 /it /tr "\"%BAT%\""
if errorlevel 1 (
  echo Failed. Open Command Prompt as Administrator and run this file again.
  pause
  exit /b 1
)

echo.
echo Task created: base-wp-ja-auto-keepalive
echo Schedule: every day at 01:30
echo Running once to verify...
schtasks /run /tn "base-wp-ja-auto-keepalive"
echo Check logs in this folder.
pause
exit /b 0
