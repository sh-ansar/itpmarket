@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist ".runtime\venv_3_2_0\Scripts\python.exe" (
  ".runtime\venv_3_2_0\Scripts\python.exe" "migrate_3_3_2.py"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "migrate_3_3_2.py"
) else (
  py -3.11 "migrate_3_3_2.py"
)
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo Hotfix 3.3.2 migration completed successfully.
if not "%RC%"=="0" echo Hotfix 3.3.2 migration failed. Exit code: %RC%
pause
exit /b %RC%
