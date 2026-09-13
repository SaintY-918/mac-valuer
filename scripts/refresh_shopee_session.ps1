# Keep the Shopee captcha clearance fresh. Registered by install_schedule.ps1 to
# run at logon; also fine to run by hand.
#
# Why at logon rather than on a timer: what Shopee periodically demands is a
# person, and a person is at this machine when they log in, not at 02:30 when
# the scrape runs. Solving it in the morning is what makes the nightly run work
# unattended -- the clearance lasts roughly a day or two (docs/decisions.md #42).
#
# The python script probes headless first and opens a window only when Shopee
# actually asks, so most logons are silent.

[CmdletBinding()]
param()

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("scrape_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Host $line
}

Write-Log "=== refresh_shopee_session start ==="

$Python = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

# The session file's timestamp is the last successful Shopee read, which is all
# the freshness signal there is -- the clearance itself is server-side and has
# no expiry we can read.
$StatePath = Join-Path $RepoRoot "shopee_state.json"
if (Test-Path $StatePath) {
    $AgeHours = ((Get-Date) - (Get-Item $StatePath).LastWriteTime).TotalHours
    Write-Log ("Session file is {0:N1} hours old" -f $AgeHours)
}

$env:PYTHONIOENCODING = "utf-8"
Push-Location $RepoRoot
try {
    & $Python -u -m src.scripts.refresh_shopee_session 2>&1 |
        ForEach-Object { Add-Content -Path $LogFile -Value $_ -Encoding utf8; Write-Host $_ }
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

Write-Log "=== refresh_shopee_session finished with exit code $code ==="
exit $code
