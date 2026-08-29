# Run the scrapers that cannot run on a GitHub runner, from this machine, and
# write into the cloud database.
#
# Why this exists: both Shopee and Carousell refuse datacenter IPs. For Shopee
# the login session also cannot follow an ephemeral runner. Measured, not
# assumed -- see docs/decisions.md and src/scripts/probe_carousell.py.
#
# Registered as a daily scheduled task by scripts/install_schedule.ps1. That
# script is the definition; this one is what it runs. The registration used to
# live in a comment block here that you were meant to retype by hand, and the
# comment and the registered task had already drifted apart.
#
#   .\scripts\install_schedule.ps1              # install or update the task
#   Start-ScheduledTask -TaskName "mac-valuer-scrape"   # run it now

param(
    # Comma-separated, in the order they should run. Put the cheap reliable
    # source first: the task is capped at one hour, and Shopee's browser path
    # is the one that can hang.
    [string]$Sources = "carousell,shopee"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("scrape_" + (Get-Date -Format "yyyy-MM-dd") + ".log")

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

Write-Log "=== run_local_scrape start (sources: $Sources) ==="

# The session must already exist. Without it a headless run raises
# ShopeeSessionExpired, which now reaches Discord as an explicit failure.
$StatePath = Join-Path $RepoRoot "shopee_state.json"
if (($Sources -match "shopee") -and -not (Test-Path $StatePath)) {
    Write-Log "shopee_state.json missing - run 'SHOPEE_HEADLESS=false python -m src.main --source shopee' once to log in."
    exit 1
}

if (Test-Path $StatePath) {
    $StateAgeDays = ((Get-Date) - (Get-Item $StatePath).LastWriteTime).TotalDays
    Write-Log ("Session file is {0:N1} days old" -f $StateAgeDays)
}

$Python = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

# Headless so the scheduled task never blocks on a visible browser window.
$env:SHOPEE_HEADLESS = "true"
# Python's logging writes to stderr; without this the Chinese log lines are
# written in the ANSI codepage and come back as mojibake.
$env:PYTHONIOENCODING = "utf-8"

Write-Log "Running: $Python -u -m src.main --source $Sources"

# The failure notice is sent from here, not from python, because the thing that
# reports a failure must not be inside the thing that fails. src/main.py sends
# its heartbeat from Step 7 and imports the notifier on line 15 -- on 2026-08-29
# Smart App Control blocked a pandas .pyd and the run died on line 9, so neither
# ever ran. The task completed, Discord said nothing, and the only trace was
# this log file. Silence has no shape; nobody can notice a message that never
# arrived.
function Get-DotEnvValue($Name) {
    $envFile = Join-Path $RepoRoot ".env"
    if (-not (Test-Path $envFile)) { return "" }
    foreach ($line in (Get-Content $envFile)) {
        if ($line -match "^\s*$Name\s*=\s*(.*)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Send-FailureNotice($ExitCode, $Retried) {
    $webhook = Get-DotEnvValue "DISCORD_WEBHOOK_URL"
    if (-not $webhook) {
        Write-Log "No DISCORD_WEBHOOK_URL in .env - this failure goes unreported"
        return
    }

    # Windows PowerShell 5.1 still defaults to TLS 1.0 for some hosts, which
    # Discord refuses outright.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $retryText = if ($Retried) { "是（重試後仍失敗）" } else { "否（失敗得太晚，重試會撞上一小時上限）" }
    $tail = (Get-Content $LogFile -Tail 12 -ErrorAction SilentlyContinue) -join "`n"
    if ($tail.Length -gt 1200) { $tail = $tail.Substring($tail.Length - 1200) }

    $msg = @(
        "⛔ **本機爬蟲執行失敗**",
        "- 來源：``$Sources``",
        "- 結束碼：``$ExitCode``",
        "- 已自動重試：$retryText",
        "- 記錄檔：``$LogFile``",
        "``````",
        $tail,
        "``````"
    ) -join "`n"

    try {
        $json  = @{ content = $msg } | ConvertTo-Json -Compress
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        Invoke-RestMethod -Uri $webhook -Method Post -ContentType "application/json" -Body $bytes | Out-Null
        Write-Log "Failure reported to Discord"
    } catch {
        Write-Log "Could not report the failure to Discord: $($_.Exception.Message)"
    }
}

# Redirection is handed to cmd rather than done with `2>&1 | Tee-Object`.
# Windows PowerShell 5.1 wraps a native command's stderr in NativeCommandError
# records, and python logs to stderr: under $ErrorActionPreference="Stop" the
# first INFO line aborts the run, and even under "Continue" the first line lands
# in the log as an error blob. Letting cmd redirect keeps the log clean and the
# exit code intact.
#
# The exit code is handed back through a script-scope variable rather than
# `return`, which in PowerShell emits everything the function put on the
# pipeline, not just the value named.
function Invoke-Pipeline {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & cmd /c "`"$Python`" -u -m src.main --source $Sources >> `"$LogFile`" 2>&1"
    $script:LastPipelineExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
}

# A run that dies within this many seconds never really started -- a blocked
# DLL, a missing session file, a database that would not connect. Those are
# usually transient, and a second attempt costs almost nothing. A run that dies
# after twenty minutes got deep into the work, and the scheduled task is capped
# at one hour (scripts/install_schedule.ps1): retrying that risks being killed
# partway through a write.
$FastFailSeconds = 300

$started = Get-Date
Invoke-Pipeline
$code = $script:LastPipelineExit
$elapsed = ((Get-Date) - $started).TotalSeconds
$retried = $false

if ($code -ne 0 -and $elapsed -lt $FastFailSeconds) {
    Write-Log ("Failed after {0:N0}s with exit code {1} - retrying once in 60s" -f $elapsed, $code)
    Start-Sleep -Seconds 60
    $retried = $true
    Write-Log "Retry attempt starting"
    Invoke-Pipeline
    $code = $script:LastPipelineExit
}

Write-Log "=== run_local_scrape finished with exit code $code ==="

if ($code -ne 0) { Send-FailureNotice -ExitCode $code -Retried $retried }

exit $code
