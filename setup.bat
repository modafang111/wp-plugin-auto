@echo off
rem 初回または依存関係の更新。仮想環境・pip・Playwright Chromium。
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python が見つかりません。Python 3.10 以上をインストールしてください。
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo 仮想環境を作成します。
  python -m venv .venv
  if errorlevel 1 (
    echo venv の作成に失敗しました。
    pause
    exit /b 1
  )
)

echo パッケージをインストールします。
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo pip install に失敗しました。
  pause
  exit /b 1
)

echo Playwright の Chromium を入れます。
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 (
  echo playwright install に失敗しました。
  pause
  exit /b 1
)

if not exist ".env" (
  if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo .env を作成しました。値を記入してください。
  )
)

echo セットアップ完了。
pause
exit /b 0
