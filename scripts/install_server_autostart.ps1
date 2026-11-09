# One-time setup on the always-on PC (run as administrator):
# - a scheduled task that starts run_forever.ps1 in a visible window (the live log) whenever this user logs on
# - a firewall rule opening the HUD / DeskAI port to the home network and Tailscale only
# Auto-login itself is set with Sysinternals Autologon, which stores the password encrypted.

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot 'run_forever.ps1'
$user = "$env:COMPUTERNAME\$env:USERNAME"
$port = if ($env:HUD_PORT) { [int]$env:HUD_PORT } else { 8080 }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`"" `
    -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
# Interactive: runs in the signed-in session, where the GPU and saved logins (gh, agy) are available.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'AlwaysOnAgent' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Scheduled task 'AlwaysOnAgent' registered for $user."

Get-NetFirewallRule -DisplayName 'AlwaysOnAgent HUD' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'AlwaysOnAgent HUD' -Direction Inbound -Protocol TCP -LocalPort $port `
    -RemoteAddress LocalSubnet, 100.64.0.0/10 -Action Allow -Profile Any | Out-Null
Write-Host "Firewall: port $port open to the home network and Tailscale only."
