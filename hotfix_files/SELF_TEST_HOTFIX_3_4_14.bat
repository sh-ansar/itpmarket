@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=python"
if exist ".runtime\python\python.exe" set "PYTHON_EXE=.runtime\python\python.exe"
"%PYTHON_EXE%" SELF_TEST_HOTFIX_3_4_14.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Self test failed with code %EXIT_CODE%.
  pause
  exit /b %EXIT_CODE%
)
echo.
echo SELF TEST 3.4.14: OK
pause
exit /b 0
