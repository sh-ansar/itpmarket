[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$LogFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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

$gitExe = Resolve-SpyonGit -RepoRoot $RepoRoot

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

$stateDir = 'C:\Spyon\.state'
$stateFile = Join-Path `
    $stateDir `
    'last-successful-production-sha'

New-Item `
    -ItemType Directory `
    -Force `
    $stateDir `
    | Out-Null

Set-Location $RepoRoot

$gitBase = @(
    '-c',
    'safe.directory=C:/Spyon/current'
)

$branch = (
    & $gitExe @gitBase `
        branch `
        --show-current
).Trim()

if ($branch -ne 'production') {
    Write-DeployLog (
        "STOP: expected production branch, got " +
        $branch
    )
    exit 10
}

$dirty = & $gitExe @gitBase `
    status `
    --porcelain `
    --untracked-files=no

if ($dirty) {
    Write-DeployLog (
        'STOP: tracked production worktree is dirty.'
    )
    exit 11
}

& $gitExe @gitBase `
    fetch `
    origin `
    production

if ($LASTEXITCODE -ne 0) {
    throw 'git fetch failed.'
}

$localBefore = (
    & $gitExe @gitBase `
        rev-parse `
        HEAD
).Trim()

$remote = (
    & $gitExe @gitBase `
        rev-parse `
        origin/production
).Trim()

if ($localBefore -ne $remote) {
    & $gitExe @gitBase `
        merge-base `
        --is-ancestor `
        $localBefore `
        $remote

    if ($LASTEXITCODE -ne 0) {
        Write-DeployLog (
            'STOP: production history diverged.'
        )
        exit 12
    }

    Write-DeployLog (
        "Fast-forward code: " +
        $localBefore +
        " -> " +
        $remote
    )

    & $gitExe @gitBase `
        merge `
        --ff-only `
        origin/production

    if ($LASTEXITCODE -ne 0) {
        throw 'git merge --ff-only failed.'
    }
}

$target = (
    & $gitExe @gitBase `
        rev-parse `
        HEAD
).Trim()

$previousSuccessful = ''

if (Test-Path -LiteralPath $stateFile) {
    $previousSuccessful = (
        Get-Content `
            -LiteralPath $stateFile `
            -Raw
    ).Trim()
}

if ($previousSuccessful -eq $target) {
    Write-DeployLog (
        "No deployment required. HEAD=" +
        $target
    )
    exit 0
}

$postUpdate = Join-Path `
    $RepoRoot `
    'deploy\windows\post-update-production.ps1'

if (-not (Test-Path -LiteralPath $postUpdate)) {
    throw (
        'Target release has no post-update ' +
        'deployment script.'
    )
}

Write-DeployLog (
    "Deploying target=" +
    $target
)

& powershell.exe `
    -NoProfile `
    -NonInteractive `
    -ExecutionPolicy Bypass `
    -File $postUpdate `
    -PreviousSha $previousSuccessful `
    -TargetSha $target `
    -RepoRoot $RepoRoot `
    -LogFile $LogFile

if ($LASTEXITCODE -ne 0) {
    Write-DeployLog (
        "DEPLOY FAILED: " +
        $target
    )
    exit 20
}

[System.IO.File]::WriteAllText(
    $stateFile,
    $target + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)

Write-DeployLog (
    "DEPLOY OK: " +
    $target
)

exit 0
