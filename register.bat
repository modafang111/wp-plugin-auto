@echo off
rem Translate a plugin and register it as a public BASE item.
rem Usage: register.bat https://wordpress.org/plugins/slug/
setlocal
cd /d "%~dp0"
set "URL=%~1"
if "%URL%"=="" set /p URL=WordPress plugin URL: 
if "%URL%"=="" (
  echo URL is empty.
  pause
  exit /b 2
)
call "%~dp0run-app.bat" --register "%URL%"
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
