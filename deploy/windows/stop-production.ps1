[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pidPath = Join-Path $root 'data\server.pid'
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output 'Spyon is not running (PID file is absent).'
    exit 0
}

$serverPid = 0
if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$serverPid)) {
    throw 'Refusing to stop: invalid PID file.'
}
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$serverPid"
if (-not $process) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Output 'Stale PID file removed; Spyon was not running.'
    exit 0
}

$allowedExecutables = @(
    (Join-Path $root '.venv\Scripts\python.exe'),
    (Join-Path $root '.runtime\venv_3_2_0\Scripts\python.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object {
    [IO.Path]::GetFullPath($_).TrimEnd('\').ToLowerInvariant()
}
$actualExecutable = [IO.Path]::GetFullPath([string]$process.ExecutablePath).TrimEnd('\').ToLowerInvariant()
if ($allowedExecutables -notcontains $actualExecutable -or $process.CommandLine -notmatch '(^|\s|["''])app\.py(["'']|\s|$)') {
    throw 'Refusing to stop a process that does not belong to this Spyon deployment.'
}

Stop-Process -Id $serverPid
Wait-Process -Id $serverPid -Timeout 15 -ErrorAction SilentlyContinue
Write-Output "Spyon PID $serverPid stopped."
