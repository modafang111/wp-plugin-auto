@echo off
rem SMTP / NOTIFY_EMAIL send test.
call "%~dp0run-app.bat" --test-mail
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
