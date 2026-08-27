[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Local', 'Production')]
    [string]$Mode = 'Auto',

    [string]$EnvironmentFile = '',

    [string]$BaseUri = '',

    [switch]$SkipHttp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$results = New-Object System.Collections.Generic.List[object]
$secretNames = @(
    'DATABASE_URL',
    'ITP_SESSION_SECRET',
    'ITP_CREDENTIAL_MASTER_KEY'
)

function Protect-DiagnosticText {
    param([object]$Value)

    $text = [string]$Value
    foreach ($name in $secretNames) {
        $secret = [Environment]::GetEnvironmentVariable($name, 'Process')
        if ($secret) {
            $text = $text.Replace($secret, '[REDACTED]')
        }
    }
    return [regex]::Replace(
        $text,
        '(?i)postgres(?:ql)?://[^\s]+',
        'postgresql://[REDACTED]'
    )
}

function Add-DiagnosticResult {
    param(
        [ValidateSet('PASS', 'WARN', 'FAIL')]
        [string]$Status,
        [string]$Component,
        [object]$Message
    )

    $results.Add([pscustomobject]@{
        Status = $Status
        Component = $Component
        Message = Protect-DiagnosticText $Message
    }) | Out-Null
}

function Find-SpyonPython {
    $candidates = @(
        (Join-Path $root '.venv\Scripts\python.exe'),
        (Join-Path $root '.runtime\venv_3_2_0\Scripts\python.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

if ($EnvironmentFile) {
    try {
        . (Join-Path $root 'deploy\windows\environment.ps1')
        Import-SpyonEnvironment -Path $EnvironmentFile
        Add-DiagnosticResult PASS 'environment file' 'Loaded without displaying values.'
    }
    catch {
        Add-DiagnosticResult FAIL 'environment file' $_.Exception.Message
    }
}

if ($Mode -eq 'Auto') {
    if ([string]$env:ITP_ENV -eq 'production') {
        $Mode = 'Production'
    }
    else {
        $Mode = 'Local'
    }
}

if (-not $BaseUri) {
    $portValue = 8765
    if ($env:ITP_PORT) {
        [void][int]::TryParse([string]$env:ITP_PORT, [ref]$portValue)
    }
    $BaseUri = "http://127.0.0.1:$portValue"
}

try {
    $branch = (& git -C $root branch --show-current 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw $branch
    }
    if ($Mode -eq 'Production' -and $branch -ne 'production') {
        Add-DiagnosticResult FAIL 'git branch' "Production must run branch production; current branch is $branch."
    }
    else {
        Add-DiagnosticResult PASS 'git branch' $branch
    }

    $trackedChanges = (& git -C $root status --porcelain --untracked-files=no 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw $trackedChanges
    }
    if ($trackedChanges) {
        $status = 'WARN'
        if ($Mode -eq 'Production') { $status = 'FAIL' }
        Add-DiagnosticResult $status 'tracked worktree' 'Tracked files contain local changes.'
    }
    else {
        Add-DiagnosticResult PASS 'tracked worktree' 'Clean.'
    }
}
catch {
    Add-DiagnosticResult FAIL 'git' $_.Exception.Message
}

$python = Find-SpyonPython
if (-not $python) {
    Add-DiagnosticResult FAIL 'Python virtualenv' 'No project Python was found in .venv or .runtime\venv_3_2_0.'
}
else {
    Add-DiagnosticResult PASS 'Python virtualenv' $python
    try {
        $version = (& $python -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info[:2] in {(3,10),(3,11)} else 2)" 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            Add-DiagnosticResult PASS 'Python version' $version
        }
        else {
            Add-DiagnosticResult FAIL 'Python version' "$version; supported versions are 3.10 and 3.11."
        }
    }
    catch {
        Add-DiagnosticResult FAIL 'Python version' $_.Exception.Message
    }

    try {
        $pipCheck = (& $python -m pip check 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            Add-DiagnosticResult PASS 'pip check' $pipCheck
        }
        else {
            Add-DiagnosticResult FAIL 'pip check' $pipCheck
        }
    }
    catch {
        Add-DiagnosticResult FAIL 'pip check' $_.Exception.Message
    }

    try {
        $importCheck = (& $python -c "import certifi,cryptography,flask,playwright,psutil,psycopg,selenium,selenium_stealth,waitress,werkzeug; print('required imports are available')" 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            Add-DiagnosticResult PASS 'Python imports' $importCheck
        }
        else {
            Add-DiagnosticResult FAIL 'Python imports' $importCheck
        }
    }
    catch {
        Add-DiagnosticResult FAIL 'Python imports' $_.Exception.Message
    }

    try {
        $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root '.playwright'
        $browserCheck = (& $python (Join-Path $root 'environment_check.py') --check-only 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            Add-DiagnosticResult PASS 'Playwright Chromium' $browserCheck
        }
        else {
            Add-DiagnosticResult FAIL 'Playwright Chromium' $browserCheck
        }
    }
    catch {
        Add-DiagnosticResult FAIL 'Playwright Chromium' $_.Exception.Message
    }
}

$requiredEnvironment = @()
if ($Mode -eq 'Production') {
    $requiredEnvironment = @(
        'ITP_ENV',
        'ITP_STORAGE_BACKEND',
        'DATABASE_URL',
        'ITP_SESSION_SECRET'
    )
}
foreach ($name in $requiredEnvironment) {
    if ([Environment]::GetEnvironmentVariable($name, 'Process')) {
        Add-DiagnosticResult PASS "env $name" 'Set (value hidden).'
    }
    else {
        Add-DiagnosticResult FAIL "env $name" 'Missing.'
    }
}

if ($Mode -eq 'Production') {
    if ([string]$env:ITP_ENV -ne 'production') {
        Add-DiagnosticResult FAIL 'env contract ITP_ENV' 'Must equal production.'
    }
    else {
        Add-DiagnosticResult PASS 'env contract ITP_ENV' 'Production mode is explicit.'
    }
    if ([string]$env:ITP_STORAGE_BACKEND -ne 'postgresql') {
        Add-DiagnosticResult FAIL 'env contract storage' 'Production must use PostgreSQL.'
    }
    else {
        Add-DiagnosticResult PASS 'env contract storage' 'PostgreSQL is selected.'
    }
    if ([string]$env:ITP_SESSION_SECRET -and ([string]$env:ITP_SESSION_SECRET).Length -lt 32) {
        Add-DiagnosticResult FAIL 'env contract session secret' 'Set but shorter than 32 characters.'
    }
    elseif ([string]$env:ITP_SESSION_SECRET) {
        Add-DiagnosticResult PASS 'env contract session secret' 'Length is valid (value hidden).'
    }
    if ([string]$env:ITP_TELEGRAM_BOT_ENABLED -eq '1') {
        if (-not [string]$env:ITP_TELEGRAM_BOT_TOKEN) {
            Add-DiagnosticResult FAIL 'Telegram bot token' 'Enabled but token is missing.'
        }
        elseif ([string]$env:ITP_TELEGRAM_BOT_TOKEN -notmatch '^\d+:[A-Za-z0-9_-]{30,}$') {
            Add-DiagnosticResult FAIL 'Telegram bot token' 'Enabled but token format is invalid.'
        }
        else {
            Add-DiagnosticResult PASS 'Telegram bot token' 'Configured (value hidden).'
        }
    }

    if ([string]$env:ITP_EMAIL_ENABLED -ne '1') {
        Add-DiagnosticResult FAIL 'SMTP email' 'Production transactional email must be enabled.'
    }
    else {
        $smtpHost = [string]$env:ITP_SMTP_HOST
        $mailFrom = [string]$env:ITP_MAIL_FROM
        $smtpSecurity = [string]$env:ITP_SMTP_SECURITY
        $publicUrl = [string]$env:SPYON_PUBLIC_URL
        if (-not $smtpHost -or -not $mailFrom) {
            Add-DiagnosticResult FAIL 'SMTP email' 'ITP_SMTP_HOST and ITP_MAIL_FROM are required.'
        }
        elseif ($smtpHost -match '^(localhost|127\.0\.0\.1|::1)$') {
            Add-DiagnosticResult FAIL 'SMTP email' 'SMTP host must not point to localhost in production.'
        }
        elseif ($smtpSecurity -notin @('starttls', 'smtps')) {
            Add-DiagnosticResult FAIL 'SMTP email' 'ITP_SMTP_SECURITY must be starttls or smtps in production.'
        }
        elseif ($publicUrl -notmatch '^https://' -or $publicUrl -match 'localhost|127\.0\.0\.1') {
            Add-DiagnosticResult FAIL 'SMTP email' 'SPYON_PUBLIC_URL must be a public HTTPS URL.'
        }
        elseif (([string]$env:ITP_SMTP_USERNAME) -and -not ([string]$env:ITP_SMTP_PASSWORD)) {
            Add-DiagnosticResult FAIL 'SMTP email' 'SMTP username is set but password is missing.'
        }
        elseif (([string]$env:ITP_SMTP_PASSWORD) -and -not ([string]$env:ITP_SMTP_USERNAME)) {
            Add-DiagnosticResult FAIL 'SMTP email' 'SMTP password is set but username is missing.'
        }
        else {
            Add-DiagnosticResult PASS 'SMTP email' 'Production SMTP settings are present (values hidden).'
        }
    }
}

$optionalEnvironment = @(
    'ITP_CREDENTIAL_MASTER_KEY',
    'ITP_DISABLE_SCHEDULER',
    'ITP_TELEGRAM_BOT_ENABLED',
    'ITP_TELEGRAM_BOT_USERNAME',
    'ITP_POSTGRES_POOL_SIZE',
    'ITP_TRUSTED_HOSTS',
    'OZON_CHROME_PATH',
    'CHROMEDRIVER_PATH',
    'HTTP_PROXY',
    'HTTPS_PROXY'
)
foreach ($name in $optionalEnvironment) {
    if ([Environment]::GetEnvironmentVariable($name, 'Process')) {
        Add-DiagnosticResult PASS "optional env $name" 'Set (value hidden).'
    }
}

$backend = [string]$env:ITP_STORAGE_BACKEND
if (-not $backend) { $backend = 'sqlite' }
if ($backend -eq 'postgresql') {
    if (-not $env:DATABASE_URL) {
        Add-DiagnosticResult FAIL 'PostgreSQL' 'DATABASE_URL is missing.'
    }
    elseif ($python) {
        try {
            $postgresCheck = Join-Path $root 'scripts\check_postgres.py'
            $databaseCheck = (& $python $postgresCheck 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0) {
                Add-DiagnosticResult PASS 'PostgreSQL' $databaseCheck
            }
            else {
                Add-DiagnosticResult FAIL 'PostgreSQL' $databaseCheck
            }
        }
        catch {
            Add-DiagnosticResult FAIL 'PostgreSQL' $_.Exception.Message
        }
    }
}
else {
    $sqlitePath = Join-Path $root 'data\unityre_kaspi.db'
    if (Test-Path -LiteralPath $sqlitePath) {
        Add-DiagnosticResult PASS 'local SQLite' $sqlitePath
    }
    else {
        Add-DiagnosticResult WARN 'local SQLite' 'Database is absent; the local app will create it on first start.'
    }
}

$chromeCandidates = @([string]$env:OZON_CHROME_PATH)
foreach ($programRoot in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LocalAppData)) {
    if ($programRoot) {
        $chromeCandidates += Join-Path $programRoot 'Google\Chrome\Application\chrome.exe'
    }
}
$chrome = $chromeCandidates | Where-Object {
    $_ -and (Test-Path -LiteralPath $_)
} | Select-Object -First 1
if ($chrome) {
    Add-DiagnosticResult PASS 'Google Chrome' $chrome
}
else {
    Add-DiagnosticResult FAIL 'Google Chrome' 'Not found; install machine-wide or set OZON_CHROME_PATH.'
}

$driverCandidates = @([string]$env:CHROMEDRIVER_PATH)
$driverCommand = Get-Command chromedriver -ErrorAction SilentlyContinue
if ($driverCommand) { $driverCandidates += $driverCommand.Source }
$driverCandidates += Join-Path $root 'collectors\ozon\drivers\chromedriver.exe'
$driver = $driverCandidates | Where-Object {
    $_ -and (Test-Path -LiteralPath $_)
} | Select-Object -First 1
if ($driver) {
    Add-DiagnosticResult PASS 'ChromeDriver' $driver
}
else {
    Add-DiagnosticResult WARN 'ChromeDriver' 'No explicit driver found; Selenium Manager and outbound network access are required.'
}

$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if ($curl) {
    Add-DiagnosticResult PASS 'Halyk curl fallback' $curl.Source
}
else {
    Add-DiagnosticResult WARN 'Halyk curl fallback' 'curl.exe is unavailable; primary certifi-backed HTTPS remains available.'
}

foreach ($relativePath in @('data', 'logs', 'output', 'backups', '.playwright')) {
    $path = Join-Path $root $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        Add-DiagnosticResult FAIL "directory $relativePath" 'Missing.'
        continue
    }
    try {
        $acl = Get-Acl -LiteralPath $path
        Add-DiagnosticResult PASS "directory $relativePath" "Accessible; owner=$($acl.Owner)."
    }
    catch {
        Add-DiagnosticResult FAIL "directory $relativePath" $_.Exception.Message
    }
}

foreach ($profile in @(
    '.kaspi_profile',
    'collectors\ozon\chrome_vpn_profile',
    'collectors\ozon\chrome_kz_profile'
)) {
    $path = Join-Path $root $profile
    if (Test-Path -LiteralPath $path -PathType Container) {
        $profileFile = Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($profileFile) {
            Add-DiagnosticResult PASS "browser profile $profile" 'Present and non-empty.'
        }
        else {
            Add-DiagnosticResult WARN "browser profile $profile" 'Directory exists but contains no live session data.'
        }
    }
    else {
        Add-DiagnosticResult WARN "browser profile $profile" 'Absent; live session-dependent verification is not available.'
    }
}

foreach ($script in @(
    'deploy\windows\start-production.ps1',
    'deploy\windows\stop-production.ps1',
    'engine\postgres_initialize.py'
)) {
    if (Test-Path -LiteralPath (Join-Path $root $script) -PathType Leaf) {
        Add-DiagnosticResult PASS "startup prerequisite $script" 'Present.'
    }
    else {
        Add-DiagnosticResult FAIL "startup prerequisite $script" 'Missing.'
    }
}

if ([string]$env:ITP_DISABLE_SCHEDULER -eq '1') {
    Add-DiagnosticResult WARN 'scheduler' 'Disabled by ITP_DISABLE_SCHEDULER=1.'
}
else {
    Add-DiagnosticResult PASS 'scheduler' 'Configured to start inside the application process.'
}

$uri = [Uri]$BaseUri
$listeners = @()
try {
    $listeners = @(Get-NetTCPConnection -LocalPort $uri.Port -State Listen -ErrorAction SilentlyContinue)
}
catch {
    Add-DiagnosticResult WARN 'application port' 'Get-NetTCPConnection is unavailable.'
}
if ($listeners.Count -gt 0) {
    $unsafeListeners = @($listeners | Where-Object {
        [string]$_.LocalAddress -notin @('127.0.0.1', '::1')
    })
    $listenerSummary = ($listeners | ForEach-Object {
        "$($_.LocalAddress):$($_.LocalPort) PID=$($_.OwningProcess)"
    }) -join '; '
    if ($unsafeListeners.Count -gt 0) {
        $bindingStatus = 'WARN'
        if ($Mode -eq 'Production') { $bindingStatus = 'FAIL' }
        Add-DiagnosticResult $bindingStatus 'application port' (
            "Listener is not loopback-only: $listenerSummary"
        )
    }
    else {
        Add-DiagnosticResult PASS 'application port' "Loopback-only listener: $listenerSummary"
    }
}
else {
    $portStatus = 'WARN'
    if ($Mode -eq 'Production') { $portStatus = 'FAIL' }
    Add-DiagnosticResult $portStatus 'application port' "No listener on port $($uri.Port)."
}

if (-not $SkipHttp -and $listeners.Count -gt 0) {
    foreach ($endpoint in @('/health', '/ready', '/')) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri ($BaseUri.TrimEnd('/') + $endpoint) -TimeoutSec 7
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Add-DiagnosticResult PASS "HTTP $endpoint" "Status $($response.StatusCode)."
            }
            else {
                Add-DiagnosticResult FAIL "HTTP $endpoint" "Status $($response.StatusCode)."
            }
        }
        catch {
            Add-DiagnosticResult FAIL "HTTP $endpoint" $_.Exception.Message
        }
    }
}

if ($Mode -eq 'Production') {
    foreach ($taskName in @('Spyon Production', 'Spyon Auto Deploy')) {
        try {
            $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
            $actionText = ($task.Actions | ForEach-Object {
                "$($_.Execute) $($_.Arguments)"
            }) -join '; '
            Add-DiagnosticResult PASS "Scheduled Task $taskName" (
                "state=$($task.State); user=$($task.Principal.UserId); action=$actionText"
            )
        }
        catch {
            Add-DiagnosticResult FAIL "Scheduled Task $taskName" 'Not found or inaccessible.'
        }
    }
}

$results | Format-Table Status, Component, Message -Wrap -AutoSize
$passCount = @($results | Where-Object { $_.Status -eq 'PASS' }).Count
$warnCount = @($results | Where-Object { $_.Status -eq 'WARN' }).Count
$failCount = @($results | Where-Object { $_.Status -eq 'FAIL' }).Count
Write-Output "SUMMARY PASS=$passCount WARN=$warnCount FAIL=$failCount MODE=$Mode"
if ($failCount -gt 0) { exit 1 }
exit 0
