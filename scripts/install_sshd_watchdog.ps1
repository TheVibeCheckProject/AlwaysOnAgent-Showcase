# One-time setup on the always-on PC (run as administrator): the "SSHD Watchdog" scheduled task runs
# sshd_watchdog.ps1 as SYSTEM every 5 minutes, and Windows is told to restart sshd if the service itself crashes.
# Remove it with:  Unregister-ScheduledTask -TaskName 'SSHD Watchdog' -Confirm:$false

$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'sshd_watchdog.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'SSHD Watchdog' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Scheduled task 'SSHD Watchdog' registered (every 5 minutes, as SYSTEM)."

# If sshd crashes outright, the service manager restarts it (after 10 s, 30 s, then every 60 s)
sc.exe failure sshd reset= 86400 actions= restart/10000/restart/30000/restart/60000 | Out-Null
Set-Service sshd -StartupType Automatic
Write-Host "sshd: automatic start, restarted by Windows if it crashes."
