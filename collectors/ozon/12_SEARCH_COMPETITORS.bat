@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist "..\..\.runtime\venv_3_2_0\Scripts\python.exe" (
  "..\..\.runtime\venv_3_2_0\Scripts\python.exe" "ozon_collector.py" market-search --limit 30
) else if exist "..\..\.venv\Scripts\python.exe" (
  "..\..\.venv\Scripts\python.exe" "ozon_collector.py" market-search --limit 30
) else (
  py -3.11 "ozon_collector.py" market-search --limit 30
)
pause
