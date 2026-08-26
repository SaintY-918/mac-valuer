# Run the Shopee scraper from this machine and write into the cloud database.
#
# Why this exists: GitHub Actions cannot scrape Shopee (datacenter IPs are
# blocked, and the login session cannot follow an ephemeral runner). Running
# from your own residential IP with the persisted shopee_state.json is the
# fallback until the Affiliate Open API is approved and verified.
#
# Already registered as the scheduled task "mac-valuer-shopee" (daily 02:30).
# To re-register it, or to change the time, run this as your own user:
#
#   $repo = "C:\project\mac-valuer"
#   $action = New-ScheduledTaskAction -Execute "powershell.exe" `
#               -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\scripts\run_local_shopee.ps1`"" `
#               -WorkingDirectory $repo
#   $trigger = New-ScheduledTaskTrigger -Daily -At 2:30am
#   $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
#               -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
#               -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
#   Register-ScheduledTask -TaskName "mac-valuer-shopee" -Action $action -Trigger $trigger `
#               -Settings $settings -Description "Scrape Shopee for mac-valuer and push to Neon" -Force
#
# -StartWhenAvailable makes a missed run (machine off/asleep) fire at the next
# opportunity instead of being skipped; -WakeToRun lets it wake the machine.
#
# Useful commands:
#   Start-ScheduledTask   -TaskName "mac-valuer-shopee"   # run it now
#   Get-ScheduledTaskInfo -TaskName "mac-valuer-shopee"   # last/next run + result
#   Unregister-ScheduledTask -TaskName "mac-valuer-shopee" -Confirm:$false

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

Write-Log "=== run_local_shopee start ==="

# The session must already exist. Without it a headless run raises
# ShopeeSessionExpired, which now reaches Discord as an explicit failure.
$StatePath = Join-Path $RepoRoot "shopee_state.json"
if (-not (Test-Path $StatePath)) {
    Write-Log "shopee_state.json missing - run 'SHOPEE_HEADLESS=false python -m src.main --source shopee' once to log in."
    exit 1
}

$StateAgeDays = ((Get-Date) - (Get-Item $StatePath).LastWriteTime).TotalDays
Write-Log ("Session file is {0:N1} days old" -f $StateAgeDays)

$Python = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

# Headless so the scheduled task never blocks on a visible browser window.
$env:SHOPEE_HEADLESS = "true"
# Python's logging writes to stderr; without this the Chinese log lines are
# written in the ANSI codepage and come back as mojibake.
$env:PYTHONIOENCODING = "utf-8"

Write-Log "Running: $Python -m src.main --source shopee"

# Redirection is handed to cmd rather than done with `2>&1 | Tee-Object`.
# Windows PowerShell 5.1 wraps a native command's stderr in NativeCommandError
# records, and python logs to stderr: under $ErrorActionPreference="Stop" the
# first INFO line aborts the run, and even under "Continue" the first line lands
# in the log as an error blob. Letting cmd redirect keeps the log clean and the
# exit code intact.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& cmd /c "`"$Python`" -m src.main --source shopee >> `"$LogFile`" 2>&1"
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

Write-Log "=== run_local_shopee finished with exit code $code ==="
exit $code
