@echo off
rem SMTP / NOTIFY_EMAIL の送信テスト。
call "%~dp0run-app.bat" --test-mail
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
