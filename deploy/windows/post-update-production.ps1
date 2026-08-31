[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PreviousSha,

    [Parameter(Mandatory = $true)]
    [string]$TargetSha,

    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$LogFile,

    [string]$EnvironmentFile = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $EnvironmentFile) {
    $EnvironmentFile = Join-Path `
        $RepoRoot `
        '.runtime\production.env'
}

. (Join-Path `
    $RepoRoot `
    'deploy\windows\environment.ps1'
)

Import-SpyonEnvironment `
    -Path $EnvironmentFile

$python = Get-SpyonPython `
    -Root $RepoRoot

function Write-DeployLog {
    param([string]$Message)

    $line = (
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') " +
        $Message
    )

    Add-Content `
        -Path $LogFile `
        -Value $line
}

Set-Location $RepoRoot

Write-DeployLog (
    "Preparing target=$TargetSha " +
    "previous=$PreviousSha"
)

# Dependencies are synchronized before the running
# production process is restarted.
Write-DeployLog "Synchronizing Python dependencies."

& $python -m pip install `
    --disable-pip-version-check `
    -r requirements.txt `
    -r requirements-postgres.txt

if ($LASTEXITCODE -ne 0) {
    throw 'Dependency synchronization failed.'
}

& $python -m pip check

if ($LASTEXITCODE -ne 0) {
    throw 'pip check failed.'
}

Write-DeployLog "Checking PostgreSQL migrations."

$statusOutput = & $python `
    engine\postgres_migrations.py `
    status `
    --json

if ($LASTEXITCODE -ne 0) {
    throw (
        "Migration status failed: " +
        ($statusOutput -join ' ')
    )
}

$status = (
    $statusOutput -join "`n"
) | ConvertFrom-Json

if (
    [int]$status.changed_count -gt 0 -or
    [int]$status.blocked_count -gt 0
) {
    throw (
        "Unsafe migration state detected. " +
        ($statusOutput -join ' ')
    )
}

if ([int]$status.pending_count -gt 0) {
    Write-DeployLog (
        "Pending migrations=" +
        [int]$status.pending_count +
        ". Creating PostgreSQL backup."
    )

    & $python `
        engine\backup_database.py `
        --db .\data\unityre_kaspi.db `
        --output C:\Spyon\backups

    if ($LASTEXITCODE -ne 0) {
        throw (
            'Automatic PostgreSQL backup failed.'
        )
    }
}

if (
    [int]$status.pending_count -gt 0 -or
    [int]$status.baseline_untracked_count -gt 0
) {
    Write-DeployLog (
        "Applying/baselining database migrations."
    )

    & $python `
        engine\postgres_migrations.py `
        apply

    if ($LASTEXITCODE -ne 0) {
        throw (
            'PostgreSQL migration apply failed.'
        )
    }
}

Write-DeployLog "Checking PostgreSQL schema."

& $python `
    engine\postgres_initialize.py `
    --check

if ($LASTEXITCODE -ne 0) {
    throw (
        'PostgreSQL schema readiness failed.'
    )
}

Write-DeployLog "Checking runtime prerequisites."

& $python `
    environment_check.py `
    --check-only

if ($LASTEXITCODE -ne 0) {
    throw (
        'Runtime prerequisite check failed.'
    )
}

Write-DeployLog "Restarting Spyon Production."

& schtasks.exe `
    /End `
    /TN "Spyon Production" `
    2>$null | Out-Null

Start-Sleep -Seconds 3

& schtasks.exe `
    /Run `
    /TN "Spyon Production" `
    | Out-Null

if ($LASTEXITCODE -ne 0) {
    throw (
        'Unable to start Spyon Production task.'
    )
}

$urls = @(
    'http://127.0.0.1:8765/health',
    'http://127.0.0.1:8765/ready',
    'http://127.0.0.1:8765/'
)

$healthy = $false

for (
    $attempt = 1;
    $attempt -le 18;
    $attempt++
) {
    $allHealthy = $true

    foreach ($url in $urls) {
        try {
            $response = Invoke-WebRequest `
                -Uri $url `
                -UseBasicParsing `
                -TimeoutSec 5

            if (
                $response.StatusCode -lt 200 -or
                $response.StatusCode -ge 400
            ) {
                $allHealthy = $false
            }
        }
        catch {
            $allHealthy = $false
        }
    }

    if ($allHealthy) {
        $healthy = $true
        break
    }

    Start-Sleep -Seconds 5
}

if (-not $healthy) {
    throw (
        'Production HTTP verification failed.'
    )
}

$ozonTaskHelper = Join-Path `
    $RepoRoot `
    'scripts\ensure_ozon_browser_task.ps1'

if (-not (Test-Path -LiteralPath $ozonTaskHelper)) {
    throw 'Ozon browser task helper was not found in the target release.'
}

$ozonStdout = Join-Path `
    'C:\Spyon\.state' `
    'ozon-task.stdout.log'

$ozonStderr = Join-Path `
    'C:\Spyon\.state' `
    'ozon-task.stderr.log'

Remove-Item `
    -LiteralPath $ozonStdout `
    -Force `
    -ErrorAction SilentlyContinue

Remove-Item `
    -LiteralPath $ozonStderr `
    -Force `
    -ErrorAction SilentlyContinue

$ozonArguments = @(
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    ('"' + $ozonTaskHelper + '"'),
    '-RepoRoot',
    ('"' + $RepoRoot + '"')
)

$ozonProcess = Start-Process `
    -FilePath 'powershell.exe' `
    -ArgumentList $ozonArguments `
    -RedirectStandardOutput $ozonStdout `
    -RedirectStandardError $ozonStderr `
    -Wait `
    -PassThru

foreach ($stream in @(
    $ozonStdout,
    $ozonStderr
)) {
    if (-not (Test-Path -LiteralPath $stream)) {
        continue
    }

    foreach ($line in Get-Content -LiteralPath $stream) {
        $value = [string]$line

        if ($value.Trim()) {
            Write-DeployLog (
                'OZON: ' +
                $value.Trim()
            )
        }
    }
}

if ($ozonProcess.ExitCode -ne 0) {
    throw (
        'Ozon browser task registration failed. exit=' +
        $ozonProcess.ExitCode
    )
}

Write-DeployLog (
    "Post-update verification OK: " +
    $TargetSha
)

exit 0
