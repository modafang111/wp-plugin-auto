@echo off
rem For Task Scheduler. Do not pause.
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir logs
echo %date% %time% started >> "logs\register-next-heartbeat.txt"
call "%~dp0run-app.bat" --register --discover --limit 1
set "ERR=%ERRORLEVEL%"
echo %date% %time% exit=%ERR% >> "logs\register-next-heartbeat.txt"
exit /b %ERR%
