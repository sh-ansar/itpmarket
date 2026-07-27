param(
    [Parameter(Mandatory=$true)][string]$OldProject
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Old = [System.IO.Path]::GetFullPath($OldProject)

if (-not (Test-Path $Old)) { throw "Old project directory not found: $Old" }

Write-Host "Source: $Old" -ForegroundColor Cyan
Write-Host "Target: $Root" -ForegroundColor Cyan

$items = @(
    @{ Source = "data\unityre_kaspi.db"; Target = "data\unityre_kaspi.db"; Required = $true },
    @{ Source = ".kaspi_profile"; Target = ".kaspi_profile"; Required = $false },
    @{ Source = "collectors\ozon\data\ozon_registry.db"; Target = "collectors\ozon\data\ozon_registry.db"; Required = $false },
    @{ Source = "collectors\ozon\chrome_vpn_profile"; Target = "collectors\ozon\chrome_vpn_profile"; Required = $false },
    @{ Source = "chrome_vpn_profile"; Target = "collectors\ozon\chrome_vpn_profile"; Required = $false }
)

foreach ($item in $items) {
    $source = Join-Path $Old $item.Source
    $target = Join-Path $Root $item.Target
    if (-not (Test-Path $source)) {
        if ($item.Required) { Write-Warning "Required source was not found: $source" }
        continue
    }

    $targetParent = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $targetParent | Out-Null

    if (Test-Path $source -PathType Container) {
        if (Test-Path $target) {
            $backup = "$target.backup_$(Get-Date -Format yyyyMMdd_HHmmss)"
            Move-Item $target $backup -Force
        }
        Copy-Item $source $target -Recurse -Force
    } else {
        if (Test-Path $target) {
            Copy-Item $target "$target.backup_$(Get-Date -Format yyyyMMdd_HHmmss)" -Force
        }
        Copy-Item $source $target -Force
    }
    Write-Host "Copied: $($item.Source)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Migration completed. Runtime folders and old .venv were not copied." -ForegroundColor Green
