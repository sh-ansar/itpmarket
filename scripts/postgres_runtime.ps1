param(
    [switch]$Initialize
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root ".runtime"
$Cluster = Join-Path $Runtime "postgresql-app-data"
$Log = Join-Path $Runtime "postgresql-app.log"
$UrlFile = Join-Path $Runtime "postgresql-url.txt"
$Port = 55433

function Find-PostgresBin {
    $command = Get-Command pg_ctl -ErrorAction SilentlyContinue
    if ($command) { return Split-Path -Parent $command.Source }
    $roots = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
        Sort-Object { [int]$_.Name } -Descending
    foreach ($item in $roots) {
        $candidate = Join-Path $item.FullName "bin"
        if (Test-Path (Join-Path $candidate "pg_ctl.exe")) { return $candidate }
    }
    throw "PostgreSQL server tools were not found. Install PostgreSQL 14 or newer."
}

function New-RandomPassword {
    $bytes = New-Object byte[] 24
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return ([System.BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
}

New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
$Bin = Find-PostgresBin
$PgCtl = Join-Path $Bin "pg_ctl.exe"

if (-not (Test-Path (Join-Path $Cluster "PG_VERSION"))) {
    if (-not $Initialize) {
        throw "The Spyon PostgreSQL cluster is not initialized. Run SETUP_POSTGRES.bat."
    }
    $password = New-RandomPassword
    $passwordFile = Join-Path $Runtime "postgres-init-password.tmp"
    try {
        Set-Content -LiteralPath $passwordFile -Value $password -Encoding Ascii -NoNewline
        & (Join-Path $Bin "initdb.exe") -D $Cluster -U spyon -A scram-sha-256 `
            --pwfile=$passwordFile --encoding=UTF8 --locale=C
        if ($LASTEXITCODE -ne 0) { throw "initdb failed." }
    } finally {
        Remove-Item -LiteralPath $passwordFile -Force -ErrorAction SilentlyContinue
    }
    $databaseUrl = "postgresql://spyon:$password@127.0.0.1:$Port/spyon"
    Set-Content -LiteralPath $UrlFile -Value $databaseUrl -Encoding Ascii -NoNewline
}

if (-not (Test-Path $UrlFile)) {
    throw "PostgreSQL connection file is missing. Run SETUP_POSTGRES.bat."
}
$databaseUrl = (Get-Content -LiteralPath $UrlFile -Raw).Trim()
$parsed = [Uri]$databaseUrl
$passwordValue = $parsed.UserInfo.Split(':', 2)[1]

& $PgCtl -D $Cluster status *> $null
if ($LASTEXITCODE -ne 0) {
    & $PgCtl -D $Cluster -l $Log -o "-p $Port -h 127.0.0.1" start
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL startup failed. See $Log" }
}

if ($Initialize) {
    $env:PGPASSWORD = $passwordValue
    $existing = & (Join-Path $Bin "psql.exe") -h 127.0.0.1 -p $Port -U spyon -d postgres `
        -Atc "SELECT 1 FROM pg_database WHERE datname='spyon'"
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL connection check failed." }
    if (($existing | Out-String).Trim() -ne "1") {
        & (Join-Path $Bin "createdb.exe") -h 127.0.0.1 -p $Port -U spyon spyon
        if ($LASTEXITCODE -ne 0) { throw "Database creation failed." }
    }
}

Write-Host "Spyon PostgreSQL: ready on 127.0.0.1:$Port" -ForegroundColor Green
