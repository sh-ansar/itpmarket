param(
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [string]$InstallRoot = "C:\ITPMarket",
    [string]$ServerIp = "192.168.1.75"
)

$ErrorActionPreference = "Stop"
$AppRoot = Join-Path $InstallRoot "app"
$SharedRoot = Join-Path $InstallRoot "shared"
$BackupRoot = Join-Path $SharedRoot "backups"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

New-Item -ItemType Directory -Force -Path $AppRoot, $SharedRoot, $BackupRoot | Out-Null

if (Test-Path (Join-Path $AppRoot "STOP.bat")) {
    & (Join-Path $AppRoot "STOP.bat") | Out-Host
}

$db = Join-Path $AppRoot "data\unityre_kaspi.db"
if (Test-Path $db) {
    Copy-Item $db (Join-Path $BackupRoot "pre_deploy_$Timestamp.db") -Force
}

$preserve = @(
    "config.local.json",
    "data",
    ".kaspi_profile",
    ".playwright",
    ".runtime",
    "logs",
    "output",
    "backups",
    "collectors\ozon\data",
    "collectors\ozon\chrome_vpn_profile"
)

$robocopyArgs = @(
    $SourceRoot, $AppRoot, "/MIR",
    "/XD", ".git", ".github\runner", ".runtime", ".venv", ".kaspi_profile", ".playwright",
    "data", "logs", "output", "backups",
    "collectors\ozon\data", "collectors\ozon\chrome_vpn_profile",
    "/XF", "config.local.json", "*.db", "*.db-shm", "*.db-wal",
    "/R:2", "/W:2", "/NFL", "/NDL", "/NP"
)
& robocopy @robocopyArgs
if ($LASTEXITCODE -ge 8) { throw "Robocopy failed with exit code $LASTEXITCODE" }

Push-Location $AppRoot
try {
    & .\INSTALL.bat
    if ($LASTEXITCODE -ne 0) { throw "Runtime installation failed" }

    & .\SERVER_SETUP_192_168_1_75.bat
    if ($LASTEXITCODE -ne 0) { Write-Warning "Server setup returned $LASTEXITCODE" }

    if (Test-Path ".\migrate_3_1_0.py") {
        & ".\.runtime\venv_3_2_0\Scripts\python.exe" ".\migrate_3_1_0.py"
        if ($LASTEXITCODE -ne 0) { throw "Database migration failed" }
    }

    & ".\.runtime\venv_3_2_0\Scripts\python.exe" -m compileall -q .
    if ($LASTEXITCODE -ne 0) { throw "Python compile check failed" }

    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$AppRoot\START_SERVER.bat`"" -WindowStyle Minimized
    Start-Sleep -Seconds 5

    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 10
    if (-not $health.ok) { throw "Health check returned ok=false" }
    Write-Host "Deployment completed. LAN URL: http://${ServerIp}:8765" -ForegroundColor Green
}
finally {
    Pop-Location
}
