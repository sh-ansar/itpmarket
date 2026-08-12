@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0CHECK_ENV.bat"
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\postgres_runtime.ps1" -Initialize
if errorlevel 1 exit /b 1
set "ITP_STORAGE_BACKEND=postgresql"
set /p DATABASE_URL=<"%~dp0.runtime\postgresql-url.txt"
".runtime\venv_3_2_0\Scripts\python.exe" "engine\postgres_initialize.py"
if errorlevel 1 exit /b 1
echo PostgreSQL setup completed. START.bat will use it automatically.
exit /b 0
