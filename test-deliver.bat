@echo off
rem 自分宛てに販売ZIP付きのテストメールを送る。購入者には送らない。
call "%~dp0run-app.bat" --test-deliver
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
