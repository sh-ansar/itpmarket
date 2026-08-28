[CmdletBinding()]
param(
    [string]$TaskName = 'Spyon Ozon Browsers',
    [string]$UserId = "$env:USERDOMAIN\$env:USERNAME"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python runtime not found: $python"
}

# Interactive means “Run only when user is logged on”; no password or
# Session-0 service identity is used. -Force updates this helper's task only.
$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}"' -f (Join-Path $root 'scripts\open_ozon_browsers.py')) -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel LeastPrivilege
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -TaskPath '\Spyon\' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output "Registered interactive logon task: \\Spyon\\$TaskName"
