@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0CHECK_ENV.bat"
if errorlevel 1 exit /b 1
if exist ".runtime\venv_3_2_0\Scripts\python.exe" (
  ".runtime\venv_3_2_0\Scripts\python.exe" "SELF_TEST_3_4_1.py"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "SELF_TEST_3_4_1.py"
) else (
  py -3.11 "SELF_TEST_3_4_1.py"
)
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
