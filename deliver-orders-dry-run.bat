@echo off
rem 未対応注文の確認のみ。購入者へは送らない。初回の BASE ログインにも使う。
rem 認証番号があるとき: deliver-orders-dry-run.bat --otp 123456
call "%~dp0run-app.bat" --deliver-orders --dry-run %*
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
