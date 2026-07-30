@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if exist ".runtime\venv_3_2_0\Scripts\python.exe" (
  ".runtime\venv_3_2_0\Scripts\python.exe" "SELF_TEST_HOTFIX_3_4_12.py"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "SELF_TEST_HOTFIX_3_4_12.py"
) else (
  py -3 "SELF_TEST_HOTFIX_3_4_12.py"
)
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
