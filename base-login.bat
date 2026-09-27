@echo off
rem Open BASE admin in a visible browser and keep the login session.
rem Usage:
rem   base-login.bat
rem   base-login.bat --otp 123456
setlocal
cd /d "%~dp0"
call "%~dp0run-app.bat" --base-login %*
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
