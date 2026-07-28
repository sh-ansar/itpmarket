@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Run this file as Administrator.
set "SERVER_IP=%~1"
if "%SERVER_IP%"=="" set "SERVER_IP=192.168.1.75"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\configure_server.ps1" -ServerIp "%SERVER_IP%" -Port 8765
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
