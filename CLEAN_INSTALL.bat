@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo This recreates only the isolated .runtime environment.
echo Legacy .venv, databases, profiles and reports are not deleted.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_runtime.ps1" -ForceRecreate
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
