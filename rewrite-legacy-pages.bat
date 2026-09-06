@echo off
rem Rewrite past JA product pages to the structured description format.
rem Skips the template item. Never deletes. Requires a BASE admin session on this PC.
call "%~dp0run-app.bat" --sync-legacy --rewrite-pages %*
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
