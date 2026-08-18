[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$DatabaseUrl,

    [ValidateSet('production', 'staging')]
    [string]$EnvironmentName = 'production',

    [ValidateRange(1, 65535)]
    [int]$Port = 8765,

    [string]$Domain = 'spyon.kz',

    [switch]$LocalHttp,
    [switch]$DisableScheduler,
    [switch]$SkipBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$runtime = Join-Path $root '.runtime'
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root '.playwright'

if ($DatabaseUrl -match "[`r`n]") {
    throw 'DATABASE_URL must be a single line.'
}
if ($DatabaseUrl -notmatch '^postgres(?:ql)?://') {
    throw 'Production deployment requires a PostgreSQL DATABASE_URL.'
}

New-Item -ItemType Directory -Path $runtime -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $runtime 'logs') -Force | Out-Null

if (-not (Test-Path -LiteralPath $python)) {
    $launchers = @(
        @('py', '-3.11'),
        @('py', '-3.10'),
        @('python')
    )
    $created = $false
    foreach ($launcher in $launchers) {
        $command = $launcher[0]
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            continue
        }
        $arguments = @()
        if ($launcher.Count -gt 1) {
            $arguments += $launcher[1]
        }
        & $command @arguments -c "import sys; raise SystemExit(0 if sys.version_info[:2] in {(3,10),(3,11)} else 1)"
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        & $command @arguments -m venv $venv
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $python)) {
            $created = $true
            break
        }
    }
    if (-not $created) {
        throw 'Python 3.10 or 3.11 could not create the production virtual environment.'
    }
}

& $python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in {(3,10),(3,11)} else 1)"
if ($LASTEXITCODE -ne 0) {
    throw 'The existing production virtual environment must use Python 3.10 or 3.11.'
}

& $python -m pip install --disable-pip-version-check `
    --requirement (Join-Path $root 'requirements.txt') `
    --requirement (Join-Path $root 'requirements-postgres.txt')
if ($LASTEXITCODE -ne 0) {
    throw 'Python dependency installation failed.'
}
if (-not $SkipBrowser) {
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        throw 'Playwright Chromium installation failed.'
    }
}

$randomBytes = New-Object byte[] 48
$generator = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $generator.GetBytes($randomBytes)
}
finally {
    $generator.Dispose()
}
$sessionSecret = [Convert]::ToBase64String($randomBytes)
$secureCookies = if ($LocalHttp) { '0' } else { '1' }
$trustProxy = if ($LocalHttp) { '0' } else { '1' }
$disableSchedulerValue = if ($DisableScheduler) { '1' } else { '0' }
$environmentPath = Join-Path $runtime 'production.env'
$lines = @(
    "ITP_ENV=$EnvironmentName",
    'ITP_HOST=127.0.0.1',
    "ITP_PORT=$Port",
    'ITP_OPEN_BROWSER=0',
    'ITP_STORAGE_BACKEND=postgresql',
    "DATABASE_URL=$DatabaseUrl",
    "ITP_SESSION_SECRET=$sessionSecret",
    "ITP_COOKIE_SECURE=$secureCookies",
    "ITP_TRUST_PROXY=$trustProxy",
    "ITP_TRUSTED_HOSTS=$Domain,www.$Domain,127.0.0.1,localhost",
    "ITP_DISABLE_SCHEDULER=$disableSchedulerValue",
    "SPYON_DOMAIN=$Domain"
)
[IO.File]::WriteAllLines($environmentPath, $lines, [Text.UTF8Encoding]::new($false))

Write-Output "Spyon environment installed in $root"
Write-Output "Environment file: $environmentPath (contains secrets; do not commit it)"
Write-Output "Start: powershell -File deploy\windows\start-production.ps1"
