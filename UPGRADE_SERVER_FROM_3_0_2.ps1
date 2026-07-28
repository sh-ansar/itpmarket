param(
    [string]$Branch = "feature/spyon-admin-panel",
    [string]$Remote = "origin",
    [string]$ServerUrl = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackupRoot = Join-Path $Root "backups"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

function Invoke-Step {
    param(
        [Parameter(Mandatory=$true)][string]$Title,
        [Parameter(Mandatory=$true)][scriptblock]$Action
    )
    Write-Host ""
    Write-Host "== $Title ==" -ForegroundColor Cyan
    & $Action
}

function Invoke-Native {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

Set-Location $Root
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

Invoke-Step "Stop server" {
    if (Test-Path ".\STOP.bat") {
        & ".\STOP.bat" | Out-Host
    }
}

Invoke-Step "Backup persistent data" {
    $items = @(
        "data\unityre_kaspi.db",
        "collectors\ozon\data\ozon_registry.db",
        "config.json",
        "config.local.json"
    )
    foreach ($item in $items) {
        if (Test-Path $item) {
            $target = Join-Path $BackupRoot ("pre_spyon_3_4_1_{0}_{1}" -f $Timestamp, (Split-Path $item -Leaf))
            Copy-Item $item $target -Force
            Write-Host "Backed up $item -> $target"
        }
    }
}

Invoke-Step "Update code from Git" {
    if (-not (Test-Path ".git")) {
        throw "This folder is not a Git checkout. Deploy a fresh checkout first, then copy the data folder into it."
    }
    Invoke-Native "git" @("fetch", $Remote)
    Invoke-Native "git" @("checkout", $Branch)
    Invoke-Native "git" @("pull", "--ff-only", $Remote, $Branch)
}

Invoke-Step "Install runtime" {
    Invoke-Native "powershell" @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        ".\scripts\install_runtime.ps1"
    )
}

$Python = ".\.runtime\venv_3_2_0\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Runtime Python was not found at $Python"
}

Invoke-Step "Run database migrations" {
    $migrations = @(
        "migrate_3_1_0.py",
        "migrate_3_2_4.py",
        "migrate_3_3_0.py",
        "migrate_3_3_1.py",
        "migrate_3_3_2.py",
        "migrate_3_3_3.py",
        "migrate_3_4_0.py",
        "migrate_3_4_1.py"
    )
    foreach ($migration in $migrations) {
        if (Test-Path $migration) {
            Write-Host "Running $migration"
            Invoke-Native $Python @($migration)
        }
    }
}

Invoke-Step "Clean Python bytecode" {
    Get-ChildItem -Path $Root -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

Invoke-Step "Validate build" {
    Invoke-Native $Python @("-m", "compileall", "-q", ".")
    if (Test-Path ".\SELF_TEST_3_4_1.py") {
        Invoke-Native $Python @(".\SELF_TEST_3_4_1.py")
    }
}

Invoke-Step "Start server" {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$Root\START_SERVER.bat`"" -WindowStyle Hidden
    Start-Sleep -Seconds 6
}

Invoke-Step "Health check" {
    $health = Invoke-RestMethod -Uri "$ServerUrl/health" -TimeoutSec 15
    if (-not $health.ok) {
        throw "Health check returned ok=false"
    }
    Write-Host "Upgrade completed. Open $ServerUrl and press Ctrl+F5." -ForegroundColor Green
}
