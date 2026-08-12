@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\.playwright"
set "ITP_HOST=127.0.0.1"
set "ITP_PORT=8765"
set "ITP_OPEN_BROWSER=1"

if exist "%~dp0.runtime\postgresql-url.txt" (
  set "ITP_STORAGE_BACKEND=postgresql"
  set /p DATABASE_URL=<"%~dp0.runtime\postgresql-url.txt"
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\postgres_runtime.ps1"
  if errorlevel 1 (
    echo ERROR: PostgreSQL could not be started.
    pause
    exit /b 1
  )
)

call "%~dp0CHECK_ENV.bat"
if errorlevel 1 (
  echo ERROR: Runtime is not ready.
  pause
  exit /b 1
)

if /I "%ITP_STORAGE_BACKEND%"=="postgresql" (
  if "%DATABASE_URL%"=="" (
    echo ERROR: DATABASE_URL is required for PostgreSQL.
    pause
    exit /b 1
  )
  ".runtime\venv_3_2_0\Scripts\python.exe" "engine\postgres_initialize.py"
  if errorlevel 1 (
    echo ERROR: PostgreSQL initialization check failed.
    pause
    exit /b 1
  )
)

echo ================================================================
echo  SPYON
echo  Local:  http://127.0.0.1:8765
echo ================================================================
".runtime\venv_3_2_0\Scripts\python.exe" -u "app.py"
if errorlevel 1 (
  echo.
  echo Application stopped with an error.
  pause
)
