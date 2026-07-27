@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\.playwright"
set "ITP_HOST=0.0.0.0"
set "ITP_PORT=8765"
set "ITP_OPEN_BROWSER=1"

call "%~dp0CHECK_ENV.bat"
if errorlevel 1 (
  echo ERROR: Runtime is not ready.
  pause
  exit /b 1
)

echo ================================================================
echo  ITP MARKET INTELLIGENCE 3.2.0
echo  Local:  http://127.0.0.1:8765
echo  Server: http://192.168.1.75:8765
echo ================================================================
".runtime\venv_3_2_0\Scripts\python.exe" -u "app.py"
if errorlevel 1 (
  echo.
  echo Application stopped with an error.
  pause
)
