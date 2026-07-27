@echo off
setlocal EnableExtensions
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Administrator rights are required. Requesting elevation...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

netsh advfirewall firewall delete rule name="ITP Market Intelligence 8765" >nul 2>&1
netsh advfirewall firewall add rule name="ITP Market Intelligence 8765" dir=in action=allow protocol=TCP localport=8765 remoteip=LocalSubnet profile=any edge=no
if errorlevel 1 (
  echo ERROR: Windows Firewall rule could not be created.
  pause
  exit /b 1
)

echo.
echo Firewall access was opened only for devices in the local subnet.
echo TCP port: 8765
pause
