@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist ".venv\Scripts\python.exe" (
  set "PYTHON=.venv\Scripts\python.exe"
) else (
  set "PYTHON=py -3"
)

%PYTHON% "migrate_3_1_0.py"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo Hotfix 3.1.0 database migration completed successfully.
  echo Start the application and run Kaspi - Exact seller offers.
) else (
  echo Hotfix migration failed. Exit code: %RC%
)
pause
exit /b %RC%
