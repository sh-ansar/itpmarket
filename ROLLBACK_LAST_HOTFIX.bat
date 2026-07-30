@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0ROLLBACK_LAST_HOTFIX.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Rollback failed. Exit code: %RC%
if "%RC%"=="0" echo Rollback finished successfully.
pause
exit /b %RC%
