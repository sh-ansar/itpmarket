@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\.playwright"
if not exist ".runtime\venv_3_2_0\Scripts\python.exe" (
  call "%~dp0INSTALL.bat"
  if errorlevel 1 exit /b 1
)
".runtime\venv_3_2_0\Scripts\python.exe" -c "import flask,waitress,playwright,psutil,selenium;print('ITP runtime: OK')"
if errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_runtime.ps1" -ForceRecreate
  if errorlevel 1 exit /b 1
)
exit /b 0
