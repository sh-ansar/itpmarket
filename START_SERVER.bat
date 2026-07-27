@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\.playwright"
set "ITP_HOST=0.0.0.0"
set "ITP_PORT=8765"
set "ITP_OPEN_BROWSER=0"

call "%~dp0CHECK_ENV.bat"
if errorlevel 1 exit /b 1

echo ================================================================
echo  ITP MARKET INTELLIGENCE 3.2.0 - SERVER
echo  LAN: http://192.168.1.75:8765
echo ================================================================
".runtime\venv_3_2_0\Scripts\python.exe" -u "app.py"
exit /b %ERRORLEVEL%
