# git pull in the owner's signed-in session (an SSH session can't reach the Windows credential store), through a
# one-off scheduled task. Exits 0 only when the pull succeeded: until 2026-10-31 it always exited 0, so a failed pull
# looked like a good one to scripts/deploy_mini.py. git's own result is written to the log as PULL_OK / PULL_FAILED,
# and this waits for it (up to 90 s) instead of a fixed 12 s.
$log = "C:\Users\owner\aoa-aside\pull.log"
Remove-Item $log -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c (git pull --ff-only && echo PULL_OK || echo PULL_FAILED) > $log 2>&1" -WorkingDirectory "C:\Projects\AlwaysOnAgent"
$principal = New-ScheduledTaskPrincipal -UserId "AGENT-MINI\owner" -LogonType Interactive
Register-ScheduledTask -TaskName "AOA-Pull" -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName "AOA-Pull"
$text = ""
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $log) {
        $text = Get-Content $log -Raw
        if ($text -match "PULL_OK|PULL_FAILED") { break }
    }
}
Unregister-ScheduledTask -TaskName "AOA-Pull" -Confirm:$false
$text
if ($text -match "PULL_OK") { exit 0 }
if (-not ($text -match "PULL_FAILED")) { "pull: no result within 90 s" }
exit 1
