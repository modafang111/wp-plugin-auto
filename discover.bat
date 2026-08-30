@echo off
rem List WordPress.org plugins that do not have an official JA language pack.
rem Does not translate or register. Default: 5 candidates.
setlocal
cd /d "%~dp0"
set "LIMIT=%~1"
if "%LIMIT%"=="" set "LIMIT=5"
call "%~dp0run-app.bat" --discover-only --limit %LIMIT%
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
