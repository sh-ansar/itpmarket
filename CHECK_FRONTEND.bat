@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================================
echo  ITP MARKET INTELLIGENCE FRONTEND CHECK
echo ================================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$health=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 8;" ^
  "$js=Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:8765/static/js/app.js?check=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -TimeoutSec 8;" ^
  "if(-not $health.ok){throw 'Backend health returned ok=false'};" ^
  "if($js.StatusCode -ne 200){throw ('app.js HTTP ' + $js.StatusCode)};" ^
  "if($js.Content -notmatch 'markFrontendReady'){throw 'app.js is missing or outdated'};" ^
  "Write-Host ('Backend: OK, version ' + $health.version) -ForegroundColor Green;" ^
  "Write-Host ('Frontend JS: OK, bytes ' + $js.RawContentLength) -ForegroundColor Green;"

if errorlevel 1 (
  echo.
  echo Check failed. Start the application with START.bat first.
  pause
  exit /b 1
)

start "" "http://127.0.0.1:8765/?reload=%RANDOM%%RANDOM%"
exit /b 0
