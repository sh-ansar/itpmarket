[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$caddy = Join-Path $root '.runtime\caddy\caddy.exe'
if (-not (Test-Path -LiteralPath $caddy)) {
    $command = Get-Command caddy -ErrorAction SilentlyContinue
    if (-not $command) {
        Write-Output 'Caddy is not installed.'
        exit 0
    }
    $caddy = $command.Source
}
& $caddy stop --address 127.0.0.1:2019
