@echo off
rem Refresh the saved BASE session. For Task Scheduler. Do not pause.
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir logs
echo %date% %time% started >> "logs\base-keepalive-heartbeat.txt"
call "%~dp0run-app.bat" --base-keepalive
set "ERR=%ERRORLEVEL%"
echo %date% %time% exit=%ERR% >> "logs\base-keepalive-heartbeat.txt"
exit /b %ERR%
