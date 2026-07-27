@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0CHECK_ENV.bat"
if errorlevel 1 (
  echo Environment check failed.
  pause
  exit /b 1
)
".runtime\venv_3_2_0\Scripts\python.exe" -c "from config import load_config; from schema import ensure_database; from pathlib import Path; c=load_config(); print('Config host:',c['app']['host']); print('Port:',c['app']['port']); print('Runtime verification: OK')"
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
