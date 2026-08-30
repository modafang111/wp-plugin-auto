@echo off
rem Translate a plugin and register it unpublished on BASE.
rem Usage: register-draft.bat https://wordpress.org/plugins/slug/
setlocal
cd /d "%~dp0"
set "URL=%~1"
if "%URL%"=="" set /p URL=WordPress plugin URL: 
if "%URL%"=="" (
  echo URL is empty.
  pause
  exit /b 2
)
call "%~dp0run-app.bat" --register-draft "%URL%"
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
