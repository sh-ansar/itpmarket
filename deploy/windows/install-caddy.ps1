[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$target = Join-Path $root '.runtime\caddy'
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("spyon-caddy-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $target -Force | Out-Null
New-Item -ItemType Directory -Path $temporary -Force | Out-Null

try {
    $headers = @{ 'User-Agent' = 'Spyon deployment bootstrap' }
    $release = Invoke-RestMethod `
        -Uri 'https://api.github.com/repos/caddyserver/caddy/releases/latest' `
        -Headers $headers
    $zipAsset = $release.assets | Where-Object {
        $_.name -match '^caddy_.*_windows_amd64\.zip$'
    } | Select-Object -First 1
    $checksumAsset = $release.assets | Where-Object {
        $_.name -match '^caddy_.*_checksums\.txt$'
    } | Select-Object -First 1
    if (-not $zipAsset -or -not $checksumAsset) {
        throw 'The official Caddy Windows release assets were not found.'
    }

    $zipPath = Join-Path $temporary $zipAsset.name
    $checksumPath = Join-Path $temporary $checksumAsset.name
    Invoke-WebRequest -Uri $zipAsset.browser_download_url -Headers $headers -OutFile $zipPath
    Invoke-WebRequest -Uri $checksumAsset.browser_download_url -Headers $headers -OutFile $checksumPath
    $checksumLine = Get-Content -LiteralPath $checksumPath | Where-Object {
        $_ -match ([regex]::Escape($zipAsset.name) + '$')
    } | Select-Object -First 1
    if (-not $checksumLine) {
        throw 'The Caddy archive checksum was not published.'
    }
    $expectedHash = ($checksumLine -split '\s+')[0].ToUpperInvariant()
    $algorithm = switch ($expectedHash.Length) {
        64 { 'SHA256' }
        128 { 'SHA512' }
        default { throw "Unsupported checksum length: $($expectedHash.Length)" }
    }
    $actualHash = (
        Get-FileHash -LiteralPath $zipPath -Algorithm $algorithm
    ).Hash.ToUpperInvariant()
    if ($actualHash -ne $expectedHash) {
        throw 'Caddy checksum verification failed.'
    }

    Expand-Archive -LiteralPath $zipPath -DestinationPath $target -Force
    $binary = Join-Path $target 'caddy.exe'
    if (-not (Test-Path -LiteralPath $binary)) {
        throw 'caddy.exe was not found after extraction.'
    }
    & $binary version
    Write-Output "Verified Caddy installed at $binary"
}
finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
