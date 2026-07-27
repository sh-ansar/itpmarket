@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv does not exist.
  echo Run CLEAN_INSTALL.bat.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import sys, pip, setuptools, flask, waitress, playwright, psutil, selenium; print('Python:', sys.version); print('Executable:', sys.executable); print('Environment imports: OK')"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo Environment check completed successfully.
if not "%RC%"=="0" echo Environment check failed. Run CLEAN_INSTALL.bat.
pause
exit /b %RC%
