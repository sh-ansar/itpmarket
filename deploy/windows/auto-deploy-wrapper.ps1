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

function Resolve-SpyonGit {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)

    $candidates = [System.Collections.Generic.List[string]]::new()
    $command = Get-Command git.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1

    if ($command -and $command.Source) {
        $candidates.Add([string]$command.Source)
    }

    foreach ($programFiles in @(
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)}
    )) {
        if ($programFiles) {
            $candidates.Add((Join-Path $programFiles 'Git\cmd\git.exe'))
        }
    }

    # Existing recovery tooling is an explicit last fallback for Windows
    # installations where Git for Windows is not on PATH.
    $spyonRoot = Split-Path -Parent $RepoRoot
    $candidates.Add((Join-Path $spyonRoot 'recovery-tools\PortableGit\cmd\git.exe'))

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            continue
        }

        & $candidate --version | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return $candidate
        }
    }

    throw 'Git for Windows was not found. Install Git for Windows or restore the approved recovery Git runtime.'
}

$gitExe = Resolve-SpyonGit -RepoRoot $repo

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
