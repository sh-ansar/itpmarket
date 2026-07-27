@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Run this file as Administrator under the Windows account that will keep the Ozon browser session open.
schtasks /Delete /TN "ITP Market Intelligence Server" /F >nul 2>nul
schtasks /Create /TN "ITP Market Intelligence Server" /TR "\"%~dp0START_SERVER.bat\"" /SC ONLOGON /RL HIGHEST /F
if errorlevel 1 (
  echo Failed to register the startup task.
  pause
  exit /b 1
)
echo Startup task registered.
pause
