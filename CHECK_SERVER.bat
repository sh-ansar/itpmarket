@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo ================================================================
echo  ITP SERVER CHECK
echo ================================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port=8765;" ^
  "$listeners=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue;" ^
  "if($listeners){Write-Host 'Port 8765: LISTENING' -ForegroundColor Green}else{Write-Host 'Port 8765: NOT LISTENING' -ForegroundColor Red};" ^
  "$ips=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue|?{$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'}|select -ExpandProperty IPAddress;" ^
  "$ips|%%{Write-Host ('LAN URL: http://' + $_ + ':8765')};" ^
  "try{$h=Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 5;if($h.ok){Write-Host ('Health: OK, version ' + $h.version) -ForegroundColor Green}}catch{Write-Host 'Health endpoint is unavailable.' -ForegroundColor Yellow}"
pause
