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
#   Start-ScheduledTask -TaskName "mac-valuer-shopee"   # run it now

param(
    # Comma-separated, in the order they should run. Put the cheap reliable
    # source first: the task is capped at one hour, and Shopee's browser path
    # is the one that can hang.
    [string]$Sources = "shopee"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("shopee_" + (Get-Date -Format "yyyy-MM-dd") + ".log")

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

Write-Log "=== run_local_shopee start (sources: $Sources) ==="

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

# Redirection is handed to cmd rather than done with `2>&1 | Tee-Object`.
# Windows PowerShell 5.1 wraps a native command's stderr in NativeCommandError
# records, and python logs to stderr: under $ErrorActionPreference="Stop" the
# first INFO line aborts the run, and even under "Continue" the first line lands
# in the log as an error blob. Letting cmd redirect keeps the log clean and the
# exit code intact.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& cmd /c "`"$Python`" -u -m src.main --source $Sources >> `"$LogFile`" 2>&1"
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

Write-Log "=== run_local_shopee finished with exit code $code ==="
exit $code
