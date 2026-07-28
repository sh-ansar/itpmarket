@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================================
echo  ITP MARKET INTELLIGENCE - LAN CHECK
echo ================================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "$listen=Get-NetTCPConnection -LocalPort 8765 -State Listen;" ^
  "if($listen){Write-Host 'Server port 8765: LISTENING' -ForegroundColor Green}else{Write-Host 'Server port 8765: NOT LISTENING. Start START.bat first.' -ForegroundColor Red};" ^
  "$ips=Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.AddressState -eq 'Preferred'} | Select-Object -ExpandProperty IPAddress -Unique;" ^
  "Write-Host ''; Write-Host 'Open one of these addresses on a device connected to the same Wi-Fi:' -ForegroundColor Cyan;" ^
  "foreach($ip in $ips){Write-Host ('  http://' + $ip + ':8765')};" ^
  "$rule=Get-NetFirewallRule -DisplayName 'Spyon 8765';" ^
  "if($rule -and $rule.Enabled -eq 'True'){Write-Host ''; Write-Host 'Windows Firewall rule: ENABLED' -ForegroundColor Green}else{Write-Host ''; Write-Host 'Windows Firewall rule: MISSING. Run ALLOW_LAN_ACCESS.bat as Administrator.' -ForegroundColor Yellow}"

echo.
echo The other device must be in the same Wi-Fi or LAN and must not use client isolation.
pause
