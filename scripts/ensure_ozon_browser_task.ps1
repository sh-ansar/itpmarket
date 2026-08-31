[CmdletBinding()]
param(
    [string]$TaskName = 'Spyon Ozon Browsers',
    [string]$RepoRoot = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}

$taskPath = '\Spyon\'
$registerScript = Join-Path $RepoRoot 'scripts\register_ozon_browser_task.ps1'
$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$launcher = Join-Path $RepoRoot 'scripts\open_ozon_browsers.py'

if (-not (Test-Path -LiteralPath $registerScript -PathType Leaf)) {
    throw 'Tracked Ozon browser task registration script was not found.'
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Current checkout Python runtime for Ozon browser task was not found.'
}

function Test-SystemPrincipal {
    param([string]$UserId)

    $value = [string]$UserId
    return $value -match '(?i)(^S-1-5-18$|SYSTEM|LOCAL SERVICE|NETWORK SERVICE)'
}

function Get-ExistingInteractiveUser {
    param($Task)

    if (-not $Task) {
        return ''
    }

    $userId = [string]$Task.Principal.UserId
    $logonType = [string]$Task.Principal.LogonType

    if ($userId -and -not (Test-SystemPrincipal $userId) -and $logonType -eq 'Interactive') {
        return $userId
    }

    return ''
}

function Get-ProductionTaskUser {
    try {
        $productionTask = Get-ScheduledTask -TaskName 'Spyon Production' -ErrorAction Stop
    }
    catch {
        return ''
    }

    $userId = [string]$productionTask.Principal.UserId
    if ($userId -and -not (Test-SystemPrincipal $userId)) {
        return $userId
    }

    return ''
}

function Test-InteractiveDesktop {
    param([string]$UserId)

    $shortName = ($UserId -split '[\\@]')[-1]
    if (-not $shortName) {
        return $false
    }

    $rows = @(query.exe user $shortName 2>$null)
    $escaped = [regex]::Escape($shortName)

    return [bool](
        $rows | Where-Object {
            $_ -match "(?i)^\s*>?\s*$escaped\s+.*active"
        }
    )
}

function Test-OzonDevToolsReady {
    param([int]$Port)

    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -TimeoutSec 2 `
            -Uri ("http://127.0.0.1:{0}/json/version" -f $Port)
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

$existingTask = Get-ScheduledTask `
    -TaskPath $taskPath `
    -TaskName $TaskName `
    -ErrorAction SilentlyContinue

$userId = Get-ExistingInteractiveUser $existingTask

if (-not $userId) {
    $configuredUser = [string]$env:SPYON_OZON_BROWSER_USER
    if ($configuredUser -and -not (Test-SystemPrincipal $configuredUser)) {
        $userId = $configuredUser.Trim()
    }
}

if (-not $userId) {
    $userId = Get-ProductionTaskUser
}

if (-not $userId) {
    throw 'No non-SYSTEM interactive user is available for Spyon Ozon Browsers.'
}

& $registerScript -TaskName $TaskName -UserId $userId
if ($LASTEXITCODE -ne 0) {
    throw 'Ozon browser task registration returned a non-zero exit code.'
}

$task = Get-ScheduledTask -TaskPath $taskPath -TaskName $TaskName -ErrorAction Stop
$action = @($task.Actions | Where-Object {
    [string]$_.Execute -eq $python -and
    [string]$_.Arguments -match [regex]::Escape($launcher) -and
    [string]$_.Arguments -match '(?i)(^|\s)--bootstrap(\s|$)'
})
$atLogon = @($task.Triggers | Where-Object {
    [string]$_.CimClass.CimClassName -match 'LogonTrigger'
})

$taskIsInteractive = [string]$task.Principal.LogonType -eq 'Interactive'
$taskUsesSystemPrincipal = Test-SystemPrincipal ([string]$task.Principal.UserId)

if (-not $taskIsInteractive -or $taskUsesSystemPrincipal -or -not $action -or -not $atLogon) {
    throw 'Spyon Ozon Browsers task does not satisfy the interactive runtime contract.'
}

Enable-ScheduledTask -TaskPath $taskPath -TaskName $TaskName | Out-Null

if (
    (Test-OzonDevToolsReady 9222) -and
    (Test-OzonDevToolsReady 9333)
) {
    Write-Output 'OZON_BROWSER_READY ports=9222,9333 source=existing'
    exit 0
}

if (-not (Test-InteractiveDesktop $userId)) {
    Write-Output 'OZON_BROWSER_DEFERRED reason=no_active_interactive_session'
    exit 0
}

try {
    Start-ScheduledTask -TaskPath $taskPath -TaskName $TaskName
}
catch {
    # The task definition is valid, but an interactive desktop can disappear
    # between the session check and Task Scheduler activation.  Leave AtLogOn
    # enabled rather than turning that transient GUI condition into a failed deploy.
    Write-Output 'OZON_BROWSER_DEFERRED reason=interactive_task_start_unavailable'
    exit 0
}

for ($attempt = 1; $attempt -le 20; $attempt++) {
    if ((Test-OzonDevToolsReady 9222) -and (Test-OzonDevToolsReady 9333)) {
        Write-Output 'OZON_BROWSER_READY ports=9222,9333 source=interactive_task'
        exit 0
    }
    Start-Sleep -Seconds 2
}

Write-Output 'OZON_BROWSER_DEFERRED reason=browser_not_ready_after_interactive_task'
exit 0
