@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0CHECK_ENV.bat"
if errorlevel 1 exit /b 1
if exist ".runtime\venv_3_2_0\Scripts\python.exe" (
  ".runtime\venv_3_2_0\Scripts\python.exe" "migrate_3_4_0.py"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "migrate_3_4_0.py"
) else (
  py -3.11 "migrate_3_4_0.py"
)
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo Migration 3.4.0 completed successfully.
if not "%RC%"=="0" echo Migration 3.4.0 failed. Exit code: %RC%
pause
exit /b %RC%
