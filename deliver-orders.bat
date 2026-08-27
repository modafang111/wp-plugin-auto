@echo off
rem タスク スケジューラ用。ウィンドウを開いたままにしない。
call "%~dp0run-app.bat" --deliver-orders
exit /b %ERRORLEVEL%
