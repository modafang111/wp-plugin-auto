@echo off
rem プラグインを翻訳して BASE に非公開登録する。
rem 使い方: register-draft.bat https://wordpress.org/plugins/スラッグ/
rem 引数なしで起動すると URL を尋ねる。
setlocal
cd /d "%~dp0"
set "URL=%~1"
if "%URL%"=="" set /p URL=WordPressプラグインのURL: 
if "%URL%"=="" (
  echo URLが空です。
  pause
  exit /b 2
)
call "%~dp0run-app.bat" --register-draft "%URL%"
set "ERR=%ERRORLEVEL%"
echo.
pause
exit /b %ERR%
