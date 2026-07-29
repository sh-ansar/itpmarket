param(
    [string]$ProjectRoot = "",
    [string]$Branch = "feature/spyon-admin-panel",
    [string]$Remote = "origin",
    [string]$RemoteUrl = "",
    [string]$ServerUrl = "http://127.0.0.1:8765",
    [int]$FetchRetries = 3,
    [switch]$InternalRelaunched
)

$ErrorActionPreference = "Stop"
$Root = if ($ProjectRoot) { (Resolve-Path $ProjectRoot).Path } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
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

function Invoke-NativeStatus {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    & $FilePath @Arguments
    return $LASTEXITCODE
}

function Invoke-GitFetchBranch {
    param(
        [Parameter(Mandatory=$true)][string]$RemoteName,
        [Parameter(Mandatory=$true)][string]$BranchName,
        [int]$Retries = 3
    )
    $remoteRef = "refs/remotes/$RemoteName/$BranchName"
    $refspec = "+refs/heads/${BranchName}:$remoteRef"
    for ($attempt = 1; $attempt -le [Math]::Max(1, $Retries); $attempt++) {
        Write-Host "Fetching $RemoteName/$BranchName (attempt $attempt/$Retries)..."
        $exitCode = Invoke-NativeStatus "git" @("fetch", "--prune", $RemoteName, $refspec)
        if ($exitCode -eq 0) {
            return $remoteRef
        }
        Start-Sleep -Seconds ([Math]::Min(10, 2 * $attempt))
    }
    & git show-ref --verify --quiet $remoteRef
    if ($LASTEXITCODE -eq 0) {
        Write-Warning "Fetch failed, but $RemoteName/$BranchName already exists locally. Continuing from cached remote ref."
        return $remoteRef
    }
    throw "git fetch failed and $RemoteName/$BranchName is not available locally"
}

function Copy-IfExists {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Destination
    )
    if (Test-Path $Source) {
        $parent = Split-Path -Parent $Destination
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        Copy-Item $Source $Destination -Force
    }
}

Set-Location $Root
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

$scriptPath = $MyInvocation.MyCommand.Path
if (-not $InternalRelaunched -and (Test-Path ".git")) {
    $resolvedScript = (Resolve-Path $scriptPath).Path
    $resolvedRoot = (Resolve-Path $Root).Path
    if ($resolvedScript.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        $relativeScript = Resolve-Path -Relative $resolvedScript
        & git ls-files --error-unmatch $relativeScript *> $null
        if ($LASTEXITCODE -ne 0) {
            $tempScript = Join-Path $env:TEMP ("spyon_upgrade_{0}.ps1" -f $Timestamp)
            Copy-Item $resolvedScript $tempScript -Force
            Write-Host "Relaunching upgrade script from $tempScript so Git can clean the project folder." -ForegroundColor Yellow
            & powershell -NoProfile -ExecutionPolicy Bypass -File $tempScript -ProjectRoot $Root -Branch $Branch -Remote $Remote -RemoteUrl $RemoteUrl -ServerUrl $ServerUrl -FetchRetries $FetchRetries -InternalRelaunched
            exit $LASTEXITCODE
        }
    }
}

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
            $target = Join-Path $BackupRoot ("pre_spyon_3_4_2_{0}_{1}" -f $Timestamp, (Split-Path $item -Leaf))
            Copy-Item $item $target -Force
            Write-Host "Backed up $item -> $target"
        }
    }
}

Invoke-Step "Update code from Git" {
    if (-not (Test-Path ".git")) {
        throw "This folder is not a Git checkout. Deploy a fresh checkout first, then copy the data folder into it."
    }
    if ($RemoteUrl) {
        Invoke-Native "git" @("remote", "set-url", $Remote, $RemoteUrl)
    }
    $currentRemoteUrl = git remote get-url $Remote
    Write-Host "Remote $Remote -> $currentRemoteUrl"
    $worktreeBackup = Join-Path $BackupRoot "pre_upgrade_worktree_$Timestamp"
    $dirty = git status --porcelain --untracked-files=all
    if ($dirty) {
        New-Item -ItemType Directory -Force -Path $worktreeBackup | Out-Null
        foreach ($line in $dirty) {
            if ($line.Length -lt 4) { continue }
            $path = $line.Substring(3).Trim().Trim('"')
            if (-not $path -or $path.StartsWith("backups/") -or $path.StartsWith("backups\")) { continue }
            $source = Join-Path $Root $path
            if (Test-Path $source -PathType Leaf) {
                Copy-IfExists $source (Join-Path $worktreeBackup $path)
            }
        }
        Write-Host "Local worktree changes were backed up to $worktreeBackup" -ForegroundColor Yellow
    }
    $remoteRef = Invoke-GitFetchBranch -RemoteName $Remote -BranchName $Branch -Retries $FetchRetries
    Invoke-Native "git" @("reset", "--hard", "HEAD")
    Invoke-Native "git" @(
        "clean", "-fd",
        "-e", "data/",
        "-e", ".runtime/",
        "-e", ".venv/",
        "-e", ".kaspi_profile/",
        "-e", ".playwright/",
        "-e", "logs/",
        "-e", "output/",
        "-e", "backups/",
        "-e", "config.local.json",
        "-e", "collectors/ozon/data/",
        "-e", "collectors/ozon/chrome_vpn_profile/"
    )
    Invoke-Native "git" @("checkout", "-B", $Branch, $remoteRef)
    $configBackup = Get-ChildItem $BackupRoot -Filter "pre_spyon_3_4_2_${Timestamp}_config.json" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($configBackup -and -not (Test-Path ".\config.local.json")) {
        Copy-Item $configBackup.FullName ".\config.local.json" -Force
        Write-Host "Restored previous config.json as config.local.json"
    }
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
    Invoke-Native $Python @("migrate_spyon.py")
}

Invoke-Step "Clean Python bytecode" {
    Get-ChildItem -Path $Root -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

Invoke-Step "Validate build" {
    Invoke-Native $Python @("-m", "compileall", "-q", ".")
    if (Test-Path ".\SELF_TEST_3_4_2.py") {
        Invoke-Native $Python @(".\SELF_TEST_3_4_2.py")
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
