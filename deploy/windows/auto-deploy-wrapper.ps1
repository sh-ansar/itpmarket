[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = 'C:\Spyon\current'
$logDir = 'C:\Spyon\logs'
$logFile = Join-Path `
    $logDir `
    'deploy-production.log'

$stateDir = 'C:\Spyon\.state'
$targetScript = Join-Path `
    $stateDir `
    'auto-deploy-target.ps1'

$gitExe = (Get-Command git.exe).Source

New-Item `
    -ItemType Directory `
    -Force `
    $logDir `
    | Out-Null

New-Item `
    -ItemType Directory `
    -Force `
    $stateDir `
    | Out-Null

function Write-DeployLog {
    param([string]$Message)

    Add-Content `
        -Path $logFile `
        -Value (
            "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') " +
            $Message
        )
}

$mutex = [System.Threading.Mutex]::new(
    $false,
    'Global\SpyonProductionDeploy'
)

$locked = $false

try {
    $locked = $mutex.WaitOne(
        0,
        $false
    )

    if (-not $locked) {
        exit 0
    }

    Set-Location $repo

    $gitBase = @(
        '-c',
        'safe.directory=C:/Spyon/current'
    )

    Write-DeployLog (
        'Checking origin/production.'
    )

    & $gitExe @gitBase `
        fetch `
        origin `
        production

    if ($LASTEXITCODE -ne 0) {
        throw 'git fetch failed.'
    }

    $scriptLines = & $gitExe @gitBase `
        show `
        'origin/production:deploy/windows/auto-deploy-production.ps1'

    if ($LASTEXITCODE -ne 0) {
        throw (
            'Production branch has no ' +
            'versioned auto-deploy controller.'
        )
    }

    [System.IO.File]::WriteAllText(
        $targetScript,
        (
            ($scriptLines -join "`n") +
            "`n"
        ),
        [System.Text.UTF8Encoding]::new($false)
    )

    & powershell.exe `
        -NoProfile `
        -NonInteractive `
        -ExecutionPolicy Bypass `
        -File $targetScript `
        -RepoRoot $repo `
        -LogFile $logFile

    exit $LASTEXITCODE
}
catch {
    Write-DeployLog (
        "ERROR: " +
        $_.Exception.Message
    )

    exit 99
}
finally {
    if ($locked) {
        $mutex.ReleaseMutex()
    }

    $mutex.Dispose()
}
