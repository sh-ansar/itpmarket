@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_runtime.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo ERROR: Spyon runtime installation failed. Exit code: %RC%
  pause
  exit /b %RC%
)
echo.
echo Installation completed successfully.
pause
exit /b 0
