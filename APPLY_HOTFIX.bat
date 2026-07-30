@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_HOTFIX.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Hotfix was not installed. Exit code: %RC%
if "%RC%"=="0" echo Hotfix installer finished successfully.
pause
exit /b %RC%
