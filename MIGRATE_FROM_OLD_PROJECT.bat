@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "OLD=%~1"
if "%OLD%"=="" (
  echo Enter the full path to the old project.
  echo Example: C:\Users\Admin\Downloads\ITP_Market_Intelligence_MVP_3.0.0
  set /p "OLD=Old project path: "
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\migrate_from_old.ps1" -OldProject "%OLD%"
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
