# Register (or update) the daily local scraper task.
#
#   .\scripts\install_schedule.ps1                    # install with the defaults
#   .\scripts\install_schedule.ps1 -At 03:00          # a different time
#   .\scripts\install_schedule.ps1 -Sources "carousell,shopee"
#   .\scripts\install_schedule.ps1 -Uninstall
#   .\scripts\install_schedule.ps1 -NoVerifyTask     # skip the logon captcha task
#
# This file is the definition of the task, not a description of one. The
# settings used to live in a comment block that you were expected to retype by
# hand, and the comment and the registered task had already drifted apart. If
# the schedule needs to change, change it here and run this again -- it
# re-registers in place.
#
# Safe to run repeatedly. Needs no administrator rights: the task runs as you,
# at normal privilege, and no password is stored.
#
# Why any of this is local: Shopee and Carousell both refuse datacenter IPs, so
# a GitHub runner cannot reach them. Measured, see docs/decisions.md.

[CmdletBinding()]
param(
    [string]$TaskName = "mac-valuer-scrape",
    [string]$At       = "02:30",
    # Which scrapers the run should fetch. Both of these refuse datacenter IPs,
    # so CI cannot reach either; Carousell first because it is plain HTTP and
    # finishes in a minute, and the task is capped at an hour that Shopee's
    # browser path could otherwise consume.
    [string]$Sources  = "carousell,shopee",
    # Shopee demands a human slide captcha every day or two and no setting gets
    # past it (decisions #42). A second task runs at logon -- when a human is
    # demonstrably present -- so the clearance is fresh by the time the nightly
    # scrape runs. It probes headless first and only opens a window when Shopee
    # actually asks, so most logons are silent.
    [string]$VerifyTaskName = "mac-valuer-shopee-verify",
    [switch]$NoVerifyTask,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
    $verify = Get-ScheduledTask -TaskName $VerifyTaskName -ErrorAction SilentlyContinue
    if ($verify) {
        Unregister-ScheduledTask -TaskName $VerifyTaskName -Confirm:$false
        Write-Host "Removed scheduled task '$VerifyTaskName'."
    }
    return
}

# ── Prerequisites ─────────────────────────────────────────────────────────────
# Checked rather than assumed: a task that fires nightly into a missing venv
# fails silently for weeks, because nobody watches a job that is supposed to be
# quiet when it works.
$problems = @()

$Python = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $problems += "venv is missing. python -m venv venv; venv\Scripts\pip install -r requirements.txt"
}
if (-not (Test-Path (Join-Path $RepoRoot ".env"))) {
    $problems += ".env is missing. Copy .env.example and fill in DATABASE_URL, GEMINI_API_KEY, DISCORD_WEBHOOK_URL."
}
if (($Sources -match "shopee") -and -not (Test-Path (Join-Path $RepoRoot "shopee_state.json"))) {
    $problems += "shopee_state.json is missing. Clear the captcha once, with a visible browser:`n" +
                 "       venv\Scripts\python -m src.scripts.refresh_shopee_session"
}

if ($problems) {
    Write-Host "Not ready yet:`n" -ForegroundColor Yellow
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    Write-Host "`nThe task is registered anyway; fix the above before the next run." -ForegroundColor Yellow
    Write-Host ""
}

# ── The task ──────────────────────────────────────────────────────────────────
$scriptPath = Join-Path $RepoRoot "scripts\run_local_scrape.ps1"

# The task was called mac-valuer-shopee while Shopee was the only source it
# fetched. Remove the old registration so renaming does not leave a second task
# behind, pointing at a script that no longer exists.
$legacy = Get-ScheduledTask -TaskName "mac-valuer-shopee" -ErrorAction SilentlyContinue
if ($legacy -and $TaskName -ne "mac-valuer-shopee") {
    Unregister-ScheduledTask -TaskName "mac-valuer-shopee" -Confirm:$false
    Write-Host "Removed the old 'mac-valuer-shopee' task (renamed to '$TaskName')."
}

# -WindowStyle Hidden: with a visible console the run can be ended by closing
# the window, which is what happened on 2026-08-27 and 2026-08-28 -- both runs
# died with STATUS_CONTROL_C_EXIT partway through.
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass " +
               "-File `"$scriptPath`" -Sources `"$Sources`"") `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $At

# -StartWhenAvailable runs a missed occurrence at the next opportunity instead
# of skipping it, which is what makes a laptop that is off at 02:30 still
# collect that day's listings. -WakeToRun only helps from sleep; a machine that
# is fully off cannot be woken, and relies on StartWhenAvailable instead.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "Scrape $Sources for mac-valuer and push results to Neon" | Out-Null

Write-Host "Registered '$TaskName': daily at $At, sources = $Sources"

# ── The logon captcha task ────────────────────────────────────────────────────
# Separate task, not a step inside the nightly run, because it needs the
# opposite conditions: a human present, and a visible browser. Folding it into
# run_local_scrape.ps1 would mean either a window nobody sees at 02:30 or a
# prompt that blocks until the task's time limit kills it.
$existingVerify = Get-ScheduledTask -TaskName $VerifyTaskName -ErrorAction SilentlyContinue
if ($NoVerifyTask -or ($Sources -notmatch "shopee")) {
    if ($existingVerify) {
        Unregister-ScheduledTask -TaskName $VerifyTaskName -Confirm:$false
        Write-Host "Removed '$VerifyTaskName' (not wanted with these options)."
    }
} else {
    $verifyScript = Join-Path $RepoRoot "scripts\refresh_shopee_session.ps1"
    $verifyAction = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument ("-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass " +
                   "-File `"$verifyScript`"") `
        -WorkingDirectory $RepoRoot

    # A few minutes after logon: at logon itself the machine is busy and a
    # browser start competes with everything else the desktop is doing.
    $verifyTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $verifyTrigger.Delay = "PT3M"

    # No -WakeToRun and no -StartWhenAvailable: a missed logon is not worth
    # catching up on, because the point of the trigger is that a person is here.
    # Twenty minutes is generous for a probe plus a captcha; past that, nobody
    # is coming.
    $verifySettings = New-ScheduledTaskSettingsSet `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $VerifyTaskName -Action $verifyAction `
        -Trigger $verifyTrigger -Settings $verifySettings -Force `
        -Description "Keep the Shopee captcha clearance fresh so the nightly scrape can run headless" | Out-Null

    Write-Host "Registered '$VerifyTaskName': at logon + 3 min, silent unless Shopee asks"
}

Write-Host ""
Write-Host "  Start-ScheduledTask   -TaskName '$TaskName'   # run it now"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName'   # last result, next run"
Write-Host "  Get-Content '$RepoRoot\logs\scrape_$(Get-Date -Format yyyy-MM-dd).log' -Tail 20"
