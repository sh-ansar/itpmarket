param([string]$ProjectPath = "")
$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ProjectPath) {
    $ParentCandidate = Split-Path -Parent $PackageDir
    if (Test-Path (Join-Path $ParentCandidate "app.py")) { $ProjectPath = $ParentCandidate }
    elseif (Test-Path (Join-Path (Get-Location) "app.py")) { $ProjectPath = (Get-Location).Path }
    else { throw "Project root was not found. Pass -ProjectPath." }
}
$ProjectPath = (Resolve-Path $ProjectPath).Path
$Backup = Get-ChildItem -Path (Join-Path $ProjectPath "backups") -Directory -Filter "hotfix_3_4_15_*" -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
if (-not $Backup) { throw "A hotfix 3.4.15 backup was not found." }
$StopBat = Join-Path $ProjectPath "STOP.bat"
if (Test-Path $StopBat) { & cmd.exe /c "`"$StopBat`"" | Out-Host }
$BackupFiles = Join-Path $Backup.FullName "files"
if (Test-Path $BackupFiles) {
    Get-ChildItem -Path $BackupFiles -Recurse -File | ForEach-Object {
        $Relative = $_.FullName.Substring($BackupFiles.Length).TrimStart('\','/')
        $Target = Join-Path $ProjectPath $Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $Target -Force
        Write-Host "Restored: $Relative"
    }
}
$NewFilesPath = Join-Path $Backup.FullName "new_files.txt"
if (Test-Path $NewFilesPath) {
    Get-Content $NewFilesPath | Where-Object { $_ } | ForEach-Object {
        $Target = Join-Path $ProjectPath $_
        if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Force; Write-Host "Removed new file: $_" }
    }
}
Write-Host "Rollback completed from: $($Backup.FullName)" -ForegroundColor Green
Write-Host "No database, runtime or environment directories were changed."
Write-Host "Run START.bat."
