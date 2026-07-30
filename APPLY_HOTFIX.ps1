param(
    [string]$ProjectPath = ""
)

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceDir = Join-Path $PackageDir "hotfix_files"
$ManifestPath = Join-Path $PackageDir "FILES_SHA256.txt"

if (-not $ProjectPath) {
    $ParentCandidate = Split-Path -Parent $PackageDir
    if (Test-Path (Join-Path $ParentCandidate "app.py")) {
        $ProjectPath = $ParentCandidate
    } elseif (Test-Path (Join-Path (Get-Location) "app.py")) {
        $ProjectPath = (Get-Location).Path
    } else {
        throw "Project root was not found. Put the hotfix folder inside the project or pass -ProjectPath."
    }
}

$ProjectPath = (Resolve-Path $ProjectPath).Path
foreach ($Required in @("app.py", "templates", "static")) {
    if (-not (Test-Path (Join-Path $ProjectPath $Required))) {
        throw "Required project item was not found: $Required"
    }
}
if (-not (Test-Path $SourceDir)) {
    throw "hotfix_files directory was not found: $SourceDir"
}
if (-not (Test-Path $ManifestPath)) {
    throw "FILES_SHA256.txt was not found: $ManifestPath"
}

Write-Host "Verifying hotfix package..."
$ManifestEntries = New-Object System.Collections.Generic.List[object]
foreach ($Line in Get-Content -LiteralPath $ManifestPath) {
    $Value = $Line.Trim()
    if (-not $Value -or $Value.StartsWith("#")) { continue }
    $Parts = $Value -split "\s+", 2
    if ($Parts.Count -ne 2) { throw "Invalid manifest line: $Value" }
    $Expected = $Parts[0].ToLowerInvariant()
    $Relative = $Parts[1].Trim().Replace('/', '\')
    if ([System.IO.Path]::IsPathRooted($Relative) -or $Relative.Split('\') -contains '..') {
        throw "Unsafe manifest path: $Relative"
    }
    $Source = Join-Path $SourceDir $Relative
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Package file is missing: $Relative"
    }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Source).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "Checksum mismatch: $Relative"
    }
    $ManifestEntries.Add([PSCustomObject]@{ Relative = $Relative; Source = $Source })
}
if ($ManifestEntries.Count -eq 0) {
    throw "The manifest does not contain files."
}
$ManifestPaths = @{}
foreach ($Entry in $ManifestEntries) {
    $ManifestPaths[$Entry.Relative.ToLowerInvariant()] = $true
}
$ExtraFiles = @(
    Get-ChildItem -Path $SourceDir -Recurse -File | ForEach-Object {
        $Relative = $_.FullName.Substring($SourceDir.Length).TrimStart('\','/').Replace('/', '\')
        if (-not $ManifestPaths.ContainsKey($Relative.ToLowerInvariant())) {
            $Relative
        }
    }
)
if ($ExtraFiles.Count -gt 0) {
    Write-Host "Warning: extra files were found in hotfix_files and will be ignored:" -ForegroundColor Yellow
    foreach ($Extra in $ExtraFiles) {
        Write-Host "  - $Extra" -ForegroundColor Yellow
    }
}
Write-Host "Package verification: OK" -ForegroundColor Green

Write-Host "Project: $ProjectPath" -ForegroundColor Cyan
Write-Host "Stopping Spyon before replacing files..."
$StopBat = Join-Path $ProjectPath "STOP.bat"
if (Test-Path $StopBat) {
    & cmd.exe /c "`"$StopBat`"" | Out-Host
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupDir = Join-Path $ProjectPath "backups\hotfix_3_4_14_$Stamp"
$BackupFiles = Join-Path $BackupDir "files"
New-Item -ItemType Directory -Path $BackupFiles -Force | Out-Null
$NewFiles = New-Object System.Collections.Generic.List[string]

foreach ($Entry in $ManifestEntries) {
    $Relative = $Entry.Relative
    $Target = Join-Path $ProjectPath $Relative
    $BackupTarget = Join-Path $BackupFiles $Relative

    if (Test-Path -LiteralPath $Target) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $BackupTarget) -Force | Out-Null
        Copy-Item -LiteralPath $Target -Destination $BackupTarget -Force
    } else {
        $NewFiles.Add($Relative)
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
    Copy-Item -LiteralPath $Entry.Source -Destination $Target -Force
    Write-Host "Updated: $Relative"
}
$NewFiles | Set-Content -Path (Join-Path $BackupDir "new_files.txt") -Encoding UTF8
Set-Content -Path (Join-Path $BackupDir "project_path.txt") -Value $ProjectPath -Encoding UTF8

function Restore-HotfixBackup {
    Write-Host "Restoring files from backup..." -ForegroundColor Yellow
    if (Test-Path $BackupFiles) {
        Get-ChildItem -Path $BackupFiles -Recurse -File | ForEach-Object {
            $Relative = $_.FullName.Substring($BackupFiles.Length).TrimStart('\','/')
            $Target = Join-Path $ProjectPath $Relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $Target -Force
        }
    }
    foreach ($Relative in $NewFiles) {
        $Target = Join-Path $ProjectPath $Relative
        if (Test-Path -LiteralPath $Target) {
            Remove-Item -LiteralPath $Target -Force
        }
    }
}

try {
    $Python = $null
    foreach ($Candidate in @(
        (Join-Path $ProjectPath ".runtime\venv_3_2_0\Scripts\python.exe"),
        (Join-Path $ProjectPath ".venv\Scripts\python.exe")
    )) {
        if (Test-Path $Candidate) {
            $Python = $Candidate
            break
        }
    }
    if (-not $Python) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($PythonCommand) { $Python = $PythonCommand.Source }
    }

    if ($Python) {
        Push-Location $ProjectPath
        try {
            & $Python -m compileall -q app.py data_service.py public_product_service.py SELF_TEST_HOTFIX_3_4_14.py
            if ($LASTEXITCODE -ne 0) {
                throw "Python compileall failed with exit code $LASTEXITCODE"
            }
        } finally {
            Pop-Location
        }
        Write-Host "Python syntax check: OK" -ForegroundColor Green
    } else {
        Write-Host "Python was not found. Syntax check was skipped." -ForegroundColor Yellow
    }

    $Node = Get-Command node -ErrorAction SilentlyContinue
    if ($Node) {
        foreach ($Script in @("static\js\app.js", "static\js\help_content.js", "static\js\public_i18n.js")) {
            & $Node.Source --check (Join-Path $ProjectPath $Script)
            if ($LASTEXITCODE -ne 0) {
                throw "node --check failed for $Script with exit code $LASTEXITCODE"
            }
        }
        Write-Host "JavaScript syntax check: OK" -ForegroundColor Green
    } else {
        Write-Host "Node.js was not found. JavaScript syntax check was skipped." -ForegroundColor Yellow
    }
} catch {
    Restore-HotfixBackup
    throw "Hotfix was rolled back because validation failed: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "Hotfix 3.4.14 installed successfully." -ForegroundColor Green
Write-Host "Backup: $BackupDir"
Write-Host "Run SELF_TEST_HOTFIX_3_4_14.bat and then START.bat."
