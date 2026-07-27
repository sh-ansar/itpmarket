@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Run this file as Administrator.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\configure_server.ps1" -ServerIp "192.168.1.75" -Port 8765
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
