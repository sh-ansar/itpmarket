@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv was not found. Run 0_SETUP.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u ozon_collector.py stats
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%

