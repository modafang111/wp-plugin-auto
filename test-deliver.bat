@echo off
rem Send a test delivery ZIP to NOTIFY_EMAIL. Does not email buyers.
call "%~dp0run-app.bat" --test-deliver
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
