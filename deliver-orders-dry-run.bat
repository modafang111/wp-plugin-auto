@echo off
rem Check pending orders only. Does not email buyers.
rem With OTP: deliver-orders-dry-run.bat --otp 123456
call "%~dp0run-app.bat" --deliver-orders --dry-run %*
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
