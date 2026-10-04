@echo off
rem Translate and publicly register a plugin.
rem Usage:
rem   register.bat
rem       Auto-pick the next wordpress.org plugin without an official JA pack.
rem   register.bat https://wordpress.org/plugins/slug/
setlocal
cd /d "%~dp0"
set "URL=%~1"
if "%URL%"=="" (
  echo No URL given. Finding the next WordPress.org plugin without an official JA pack.
  call "%~dp0run-app.bat" --register --discover --limit 1
) else (
  call "%~dp0run-app.bat" --register "%URL%"
)
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
