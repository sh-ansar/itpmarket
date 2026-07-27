@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_runtime.ps1" -ForceRecreate
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
