param(
    [switch]$ForceRecreate,
    [switch]$Silent
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RuntimeRoot = Join-Path $Root ".runtime"
$Venv = Join-Path $RuntimeRoot "venv_3_2_0"
$Python = Join-Path $Venv "Scripts\python.exe"
$LegacyVenv = Join-Path $Root ".venv"
$PlaywrightPath = Join-Path $Root ".playwright"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Stop-ProjectProcesses {
    $prefixes = @(
        [System.IO.Path]::GetFullPath($Venv),
        [System.IO.Path]::GetFullPath($LegacyVenv),
        [System.IO.Path]::GetFullPath($Root)
    )
    $pidFile = Join-Path $Root "data\server.pid"
    if (Test-Path $pidFile) {
        try {
            $pidValue = [int](Get-Content $pidFile -Raw).Trim()
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 700
        } catch {}
    }

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        $exe = [string]$_.ExecutablePath
        $cmd = [string]$_.CommandLine
        if (-not $exe) { return }
        $insideRuntime = $false
        foreach ($prefix in $prefixes[0..1]) {
            if ($exe.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                $insideRuntime = $true
                break
            }
        }
        $isApp = $cmd -and $cmd.Contains((Join-Path $Root "app.py"))
        if ($insideRuntime -or $isApp) {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 1
}

function Test-Venv {
    if (-not (Test-Path $Python)) { return $false }
    & $Python -c "import sys, pip, setuptools; assert sys.prefix" *> $null
    return $LASTEXITCODE -eq 0
}

function New-Venv {
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    $launchers = @(
        @("py", "-3.11"),
        @("py", "-3.10"),
        @("py", "-3"),
        @("python")
    )
    foreach ($launcher in $launchers) {
        $command = $launcher[0]
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { continue }
        $args = @()
        if ($launcher.Count -gt 1) { $args += $launcher[1] }
        $args += @("-m", "venv", $Venv)
        Write-Host "Creating runtime with: $command $($args -join ' ')"
        & $command @args
        if ($LASTEXITCODE -eq 0 -and (Test-Path $Python)) { return }
    }
    throw "Python 3.10 or 3.11 could not create the runtime environment."
}

Write-Host "================================================================"
Write-Host " ITP MARKET INTELLIGENCE 3.2.0 - RUNTIME INSTALL"
Write-Host "================================================================"
Write-Host "Legacy .venv is ignored and never deleted."
Write-Host "Runtime path: $Venv"

Stop-ProjectProcesses

if ($ForceRecreate -and (Test-Path $Venv)) {
    Write-Step "Archiving the previous 3.2 runtime"
    $backup = Join-Path $RuntimeRoot ("venv_3_2_0_old_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
    try {
        Move-Item -Path $Venv -Destination $backup -Force
    } catch {
        Write-Warning "The old runtime is still locked. It will be left untouched."
        $Venv = Join-Path $RuntimeRoot ("venv_3_2_0_repair_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
        $Python = Join-Path $Venv "Scripts\python.exe"
    }
}

if (-not (Test-Venv)) {
    Write-Step "Creating an isolated runtime"
    if (Test-Path $Venv) {
        $broken = Join-Path $RuntimeRoot ("venv_broken_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
        try { Move-Item $Venv $broken -Force } catch {}
    }
    New-Venv
}

Write-Step "Repairing pip and build tools"
& $Python -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw "ensurepip failed" }
& $Python -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

Write-Step "Installing application dependencies"
& $Python -m pip install --disable-pip-version-check -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "requirements installation failed" }

Write-Step "Checking Playwright Chromium"
$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightPath
& $Python (Join-Path $Root "environment_check.py")
if ($LASTEXITCODE -ne 0) { throw "environment check failed" }

Write-Step "Runtime is ready"
Write-Host "Python: $Python" -ForegroundColor Green
Write-Host "Legacy .venv was not modified." -ForegroundColor DarkGray
