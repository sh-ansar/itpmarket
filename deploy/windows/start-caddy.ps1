[CmdletBinding()]
param(
    [string]$EnvironmentFile = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $EnvironmentFile) {
    $EnvironmentFile = Join-Path $root '.runtime\production.env'
}
. (Join-Path $PSScriptRoot 'environment.ps1')
Import-SpyonEnvironment -Path $EnvironmentFile

$pathCommand = Get-Command caddy -ErrorAction SilentlyContinue
$candidates = @((Join-Path $root '.runtime\caddy\caddy.exe'))
if ($pathCommand) {
    $candidates += $pathCommand.Source
}
$candidates = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$caddy = $candidates | Select-Object -First 1
if (-not $caddy) {
    throw 'Caddy was not found. Run deploy\windows\install-caddy.ps1 first.'
}

New-Item -ItemType Directory -Path (Join-Path $root '.runtime\logs') -Force | Out-Null
Push-Location $root
try {
    & $caddy validate --config 'deploy\windows\Caddyfile' --adapter caddyfile
    if ($LASTEXITCODE -ne 0) {
        throw 'Caddyfile validation failed.'
    }
    & $caddy run --config 'deploy\windows\Caddyfile' --adapter caddyfile
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
