@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0CHECK_ENV.bat"
if errorlevel 1 exit /b 1
".runtime\venv_3_2_0\Scripts\python.exe" "SELF_TEST_MVP.py"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo MVP self-test completed successfully.
if not "%RC%"=="0" echo MVP self-test failed. Exit code: %RC%
pause
exit /b %RC%
