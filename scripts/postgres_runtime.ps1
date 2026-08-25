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

function Get-PostgresMajorVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PostgresExe
    )

    if (-not (Test-Path -LiteralPath $PostgresExe)) {
        throw "PostgreSQL executable not found: $PostgresExe"
    }

    $output = (
        & $PostgresExe --version 2>$null |
        Out-String
    ).Trim()

    if (
        $LASTEXITCODE -ne 0 -or
        $output -notmatch 'PostgreSQL\)\s+(\d+)'
    ) {
        throw "Unable to determine PostgreSQL version: $PostgresExe"
    }

    return [string]$Matches[1]
}

function Test-PostgresInstallation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Bin,

        [string]$RequiredMajorVersion = ""
    )

    $requiredExecutables = @(
        "postgres.exe",
        "pg_ctl.exe",
        "pg_isready.exe",
        "psql.exe",
        "createdb.exe",
        "initdb.exe"
    )

    foreach ($name in $requiredExecutables) {
        if (-not (Test-Path -LiteralPath (Join-Path $Bin $name))) {
            return $false
        }
    }

    $installationRoot = Split-Path -Parent $Bin
    $timezonePath = Join-Path $installationRoot "share\timezone"

    if (-not (Test-Path -LiteralPath $timezonePath)) {
        return $false
    }

    if ($RequiredMajorVersion) {
        try {
            $actualMajor = Get-PostgresMajorVersion `
                -PostgresExe (Join-Path $Bin "postgres.exe")
        }
        catch {
            return $false
        }

        if ($actualMajor -ne $RequiredMajorVersion) {
            return $false
        }
    }

    return $true
}

function Find-PostgresBin {
    param(
        [string]$RequiredMajorVersion = ""
    )

    $candidates = New-Object System.Collections.Generic.List[string]

    if ($RequiredMajorVersion) {
        $standardPath = (
            "C:\Program Files\PostgreSQL\" +
            $RequiredMajorVersion +
            "\bin"
        )

        $candidates.Add($standardPath)
    }

    $command = Get-Command pg_ctl.exe -ErrorAction SilentlyContinue

    if ($command) {
        $candidates.Add(
            (Split-Path -Parent $command.Source)
        )
    }

    if (-not $RequiredMajorVersion) {
        $roots = Get-ChildItem `
            "C:\Program Files\PostgreSQL" `
            -Directory `
            -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -match '^\d+$'
            } |
            Sort-Object {
                [int]$_.Name
            } -Descending

        foreach ($item in $roots) {
            $candidates.Add(
                (Join-Path $item.FullName "bin")
            )
        }
    }

    $seen = @{}

    foreach ($candidate in $candidates) {
        if (
            -not $candidate -or
            $seen.ContainsKey($candidate)
        ) {
            continue
        }

        $seen[$candidate] = $true

        if (
            Test-PostgresInstallation `
                -Bin $candidate `
                -RequiredMajorVersion $RequiredMajorVersion
        ) {
            return $candidate
        }
    }

    if ($RequiredMajorVersion) {
        throw (
            "PostgreSQL $RequiredMajorVersion is required by " +
            "$Cluster\PG_VERSION, but a complete matching installation " +
            "was not found. Existing cluster will not be started with " +
            "another PostgreSQL major version."
        )
    }

    throw (
        "A complete PostgreSQL installation was not found. " +
        "Install PostgreSQL 14 or newer."
    )
}

function New-RandomPassword {
    $bytes = New-Object byte[] 24
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()

    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }

    return (
        [System.BitConverter]::ToString($bytes)
    ).Replace("-", "").ToLowerInvariant()
}

function Wait-PostgresReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PgIsReady,

        [int]$Attempts = 30
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        & $PgIsReady `
            -h 127.0.0.1 `
            -p $Port `
            -d postgres `
            *> $null

        if ($LASTEXITCODE -eq 0) {
            return
        }

        Start-Sleep -Seconds 1
    }

    throw (
        "Spyon PostgreSQL did not become ready on " +
        "127.0.0.1:$Port after $Attempts seconds."
    )
}

New-Item `
    -ItemType Directory `
    -Force `
    -Path $Runtime |
    Out-Null

$clusterVersionFile = Join-Path $Cluster "PG_VERSION"
$requiredMajor = ""

if (Test-Path -LiteralPath $clusterVersionFile) {
    $rawClusterVersion = (
        Get-Content `
            -LiteralPath $clusterVersionFile `
            -Raw
    ).Trim()

    if ($rawClusterVersion -notmatch '^(\d+)') {
        throw (
            "Invalid PostgreSQL cluster version in " +
            "$clusterVersionFile"
        )
    }

    $requiredMajor = [string]$Matches[1]
}

$Bin = Find-PostgresBin `
    -RequiredMajorVersion $requiredMajor

$PgCtl = Join-Path $Bin "pg_ctl.exe"
$PgIsReady = Join-Path $Bin "pg_isready.exe"

if (-not (Test-Path -LiteralPath $clusterVersionFile)) {
    if (-not $Initialize) {
        throw (
            "The Spyon PostgreSQL cluster is not initialized. " +
            "Run SETUP_POSTGRES.bat."
        )
    }

    $password = New-RandomPassword
    $passwordFile = Join-Path $Runtime "postgres-init-password.tmp"

    try {
        Set-Content `
            -LiteralPath $passwordFile `
            -Value $password `
            -Encoding Ascii `
            -NoNewline

        & (Join-Path $Bin "initdb.exe") `
            -D $Cluster `
            -U spyon `
            -A scram-sha-256 `
            --pwfile=$passwordFile `
            --encoding=UTF8 `
            --locale=C

        if ($LASTEXITCODE -ne 0) {
            throw "initdb failed."
        }
    }
    finally {
        Remove-Item `
            -LiteralPath $passwordFile `
            -Force `
            -ErrorAction SilentlyContinue
    }

    $createdMajor = (
        Get-Content `
            -LiteralPath $clusterVersionFile `
            -Raw
    ).Trim()

    $databaseUrl = (
        "postgresql://spyon:" +
        $password +
        "@127.0.0.1:" +
        $Port +
        "/spyon"
    )

    Set-Content `
        -LiteralPath $UrlFile `
        -Value $databaseUrl `
        -Encoding Ascii `
        -NoNewline

    $requiredMajor = $createdMajor
}

if (-not (Test-Path -LiteralPath $UrlFile)) {
    throw (
        "PostgreSQL connection file is missing. " +
        "Run SETUP_POSTGRES.bat."
    )
}

$databaseUrl = (
    Get-Content `
        -LiteralPath $UrlFile `
        -Raw
).Trim()

$parsed = [Uri]$databaseUrl
$passwordValue = $parsed.UserInfo.Split(':', 2)[1]

& $PgCtl `
    -D $Cluster `
    status `
    *> $null

$serverAlreadyRunning = ($LASTEXITCODE -eq 0)

if (-not $serverAlreadyRunning) {
    & $PgCtl `
        -D $Cluster `
        -l $Log `
        -o "-p $Port -h 127.0.0.1" `
        start

    if ($LASTEXITCODE -ne 0) {
        throw (
            "PostgreSQL startup failed. " +
            "See $Log"
        )
    }
}

Wait-PostgresReady `
    -PgIsReady $PgIsReady

if ($Initialize) {
    $env:PGPASSWORD = $passwordValue

    $existing = & (Join-Path $Bin "psql.exe") `
        -h 127.0.0.1 `
        -p $Port `
        -U spyon `
        -d postgres `
        -Atc "SELECT 1 FROM pg_database WHERE datname='spyon'"

    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL connection check failed."
    }

    if (($existing | Out-String).Trim() -ne "1") {
        & (Join-Path $Bin "createdb.exe") `
            -h 127.0.0.1 `
            -p $Port `
            -U spyon `
            spyon

        if ($LASTEXITCODE -ne 0) {
            throw "Database creation failed."
        }
    }
}

$activeMajor = Get-PostgresMajorVersion `
    -PostgresExe (Join-Path $Bin "postgres.exe")

Write-Host (
    "Spyon PostgreSQL ${activeMajor}: ready on " +
    "127.0.0.1:$Port"
) -ForegroundColor Green
