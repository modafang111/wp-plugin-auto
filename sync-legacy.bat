@echo off
rem Map past JA listings for auto-delivery and write unified description previews.
rem Does not edit BASE product pages. Template item is included in delivery only.
call "%~dp0run-app.bat" --sync-legacy
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
