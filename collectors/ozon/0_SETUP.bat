@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  py -3.11 -m venv .venv >nul 2>nul
  if errorlevel 1 py -3.10 -m venv .venv >nul 2>nul
  if errorlevel 1 python -m venv .venv
  if errorlevel 1 (
    echo ERROR: Python 3.10 or 3.11 is required.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install selenium selenium-stealth
if errorlevel 1 goto :error
".venv\Scripts\python.exe" SELF_TEST.py
if errorlevel 1 goto :error
echo.
echo Setup and offline self-test completed successfully.
pause
exit /b 0
:error
echo.
echo ERROR: setup or self-test failed.
pause
exit /b 1

