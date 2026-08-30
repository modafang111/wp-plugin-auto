@echo off
rem Build missing Japanese localization ZIPs for past shop items.
rem Does not register or edit BASE products. Template page is not changed.
call "%~dp0run-app.bat" --sync-legacy --build-zips
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
