[CmdletBinding()]
param(
    [string]$TaskName = 'Spyon Ozon Browsers',
    [string]$UserId = "$env:USERDOMAIN\$env:USERNAME"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Python runtime not found: $python" }

# The launcher identifies a managed profile and SessionId 0 before it closes an
# exact PID. Ordinary user Chrome profiles are never selected.
& (Join-Path $PSScriptRoot 'register_ozon_browser_task.ps1') -TaskName $TaskName -UserId $UserId
& $python (Join-Path $PSScriptRoot 'open_ozon_browsers.py') --bootstrap
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
