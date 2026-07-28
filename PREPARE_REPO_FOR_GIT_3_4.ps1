$ErrorActionPreference="Stop"
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
if (-not (Test-Path ".git")) { throw "Run this script inside the local Git repository. .git was not found." }
$patterns=@('^\.runtime/','^\.venv/','^\.playwright/','^\.kaspi_profile/','^collectors/ozon/chrome_vpn_profile/','^collectors/ozon/data/.*\.db','^data/.*\.db','^data/\.session_secret$','^data/server\.pid$','^data/tasks_state\.json$','^backups/','^logs/','^output/','(^|/)__pycache__/','\.pyc$')
$tracked=@(git ls-files);$blocked=@()
foreach($file in $tracked){foreach($pattern in $patterns){if($file -match $pattern){$blocked+=$file;break}}}
if($blocked.Count -gt 0){Write-Host "Removing machine-local files from Git index only:" -ForegroundColor Yellow;foreach($file in $blocked){Write-Host "  $file";git rm --cached --ignore-unmatch -- "$file" | Out-Null}}else{Write-Host "No machine-local files are currently tracked." -ForegroundColor Green}
git add .gitignore
Write-Host "Local files were NOT deleted." -ForegroundColor Green
git status --short
