@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined CHROME (
  echo ERROR: Google Chrome was not found.
  pause
  exit /b 1
)
set "START_URL=https://www.ozon.ru/seller/alfa-tires-3381444/"
if exist "START_URLS.txt" set /p START_URL=<"START_URLS.txt"
if not exist "START_URLS.txt" if exist "START_URL.txt" set /p START_URL=<"START_URL.txt"
start "" "%CHROME%" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="%~dp0chrome_vpn_profile" --profile-directory=Default --lang=ru-RU --start-maximized "%START_URL%"
echo.
echo Seller catalogue is opened first.
echo Enable Urban VPN with Russia and wait until cards and prices are visible.
echo Keep this browser open while the collector is running.
pause
