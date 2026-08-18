[CmdletBinding()]
param(
    [string]$EnvironmentFile = '',
    [switch]$SkipDatabaseInitialization
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $EnvironmentFile) {
    $EnvironmentFile = Join-Path $root '.runtime\production.env'
}

. (Join-Path $PSScriptRoot 'environment.ps1')
Import-SpyonEnvironment -Path $EnvironmentFile

if ($env:ITP_STORAGE_BACKEND -ne 'postgresql' -or -not $env:DATABASE_URL) {
    throw 'Production startup requires PostgreSQL in production.env.'
}
if ($env:ITP_ENV -eq 'production' -and ($env:ITP_SESSION_SECRET.Length -lt 32)) {
    throw 'ITP_SESSION_SECRET must contain at least 32 characters.'
}

# Waitress is never exposed directly. Caddy is the only public listener.
$env:ITP_HOST = '127.0.0.1'
$env:ITP_OPEN_BROWSER = '0'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root '.playwright'
$python = Get-SpyonPython -Root $root
$pidPath = Join-Path $root 'data\server.pid'
if (Test-Path -LiteralPath $pidPath) {
    $existingPid = 0
    [void][int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$existingPid)
    if ($existingPid -gt 0 -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        throw "Spyon is already running with PID $existingPid."
    }
}

Push-Location $root
try {
    if (-not $SkipDatabaseInitialization) {
        & $python 'engine\postgres_initialize.py'
        if ($LASTEXITCODE -ne 0) {
            throw 'PostgreSQL initialization/check failed.'
        }
    }
    else {
        & $python 'engine\postgres_initialize.py' --check
        if ($LASTEXITCODE -ne 0) {
            throw 'PostgreSQL readiness check failed; database initialization was not attempted.'
        }
    }
    & $python 'environment_check.py' --check-only
    if ($LASTEXITCODE -ne 0) {
        throw 'Python or browser runtime prerequisites are not ready.'
    }
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw 'Python dependency consistency check failed.'
    }
    $chromeCandidates = @([string]$env:OZON_CHROME_PATH)
    foreach ($programRoot in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($programRoot) {
            $chromeCandidates += Join-Path $programRoot 'Google\Chrome\Application\chrome.exe'
        }
    }
    $chromeCandidates = $chromeCandidates | Where-Object {
        $_ -and (Test-Path -LiteralPath $_)
    }
    if (-not $chromeCandidates) {
        throw 'Google Chrome was not found. Install it machine-wide or set OZON_CHROME_PATH.'
    }
    & $python -u 'app.py'
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
