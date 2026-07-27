@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist ".runtime\venv_3_2_0\Scripts\python.exe" (
  ".runtime\venv_3_2_0\Scripts\python.exe" "stop_server.py"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='data\server.pid';if(Test-Path $p){$id=[int](Get-Content $p -Raw);Stop-Process -Id $id -Force -ErrorAction SilentlyContinue;Remove-Item $p -Force -ErrorAction SilentlyContinue}"
)
exit /b 0
