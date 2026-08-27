@echo off
rem Create a daily Task Scheduler job for register-next.bat
rem Usage: schedule-register.bat
rem Optional time: schedule-register.bat 07:00
setlocal
cd /d "%~dp0"
set "BAT=%~dp0register-next.bat"
set "WHEN=%~1"
if "%WHEN%"=="" set "WHEN=07:00"
if not exist "%BAT%" (
  echo register-next.bat was not found.
  pause
  exit /b 1
)

schtasks /create /f /tn "base-wp-ja-auto-register" /sc daily /st %WHEN% /it /tr "\"%BAT%\""
if errorlevel 1 (
  echo Failed. Open Command Prompt as Administrator and run this file again.
  pause
  exit /b 1
)

echo.
echo Task created: base-wp-ja-auto-register
echo Schedule: every day at %WHEN%
echo Action: 1 plugin --register --discover
echo Running once to verify...
schtasks /run /tn "base-wp-ja-auto-register"
echo Check logs in this folder.
pause
exit /b 0
