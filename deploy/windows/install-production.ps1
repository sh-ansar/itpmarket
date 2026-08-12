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

if ($DatabaseUrl -match "[`r`n]") {
    throw 'DATABASE_URL must be a single line.'
}
if ($DatabaseUrl -notmatch '^postgres(?:ql)?://') {
    throw 'Production deployment requires a PostgreSQL DATABASE_URL.'
}

New-Item -ItemType Directory -Path $runtime -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $runtime 'logs') -Force | Out-Null

if (-not (Test-Path -LiteralPath $python)) {
    $systemPython = (Get-Command python -ErrorAction Stop).Source
    & $systemPython -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not create the Python virtual environment.'
    }
}

& $python -m pip install --disable-pip-version-check --requirement (Join-Path $root 'requirements.txt')
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
