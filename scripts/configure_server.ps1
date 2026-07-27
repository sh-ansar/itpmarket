param(
    [string]$ServerIp = "192.168.1.75",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$LocalConfig = Join-Path $Root "config.local.json"

$config = @{
    app = @{
        host = "0.0.0.0"
        port = $Port
        open_browser = $false
        session_hours = 12
        max_parallel_tasks = 3
        product_page_size = 30
    }
}
$config | ConvertTo-Json -Depth 8 | Set-Content -Path $LocalConfig -Encoding UTF8

$ruleName = "ITP Market Intelligence LAN $Port"
try {
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -RemoteAddress LocalSubnet `
        -Profile Any | Out-Null
    Write-Host "Firewall rule created for LocalSubnet." -ForegroundColor Green
} catch {
    Write-Warning "Firewall rule was not created. Run SERVER_SETUP_192_168_1_75.bat as Administrator."
}

$addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress

Write-Host ""
Write-Host "Configured URL: http://${ServerIp}:$Port" -ForegroundColor Cyan
Write-Host "IPv4 addresses detected on this computer:"
$addresses | ForEach-Object { Write-Host "  http://${_}:$Port" }

if ($addresses -notcontains $ServerIp) {
    Write-Warning "This computer does not currently own $ServerIp. Configure DHCP reservation/static IPv4 before using this exact address."
}
