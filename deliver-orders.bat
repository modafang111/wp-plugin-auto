@echo off
rem For Task Scheduler. Do not pause.
call "%~dp0run-app.bat" --deliver-orders
exit /b %ERRORLEVEL%
