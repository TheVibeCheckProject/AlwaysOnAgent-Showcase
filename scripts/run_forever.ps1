# Keeps the agent running on an always-on PC: relaunches supervisor.py after a
# /restart (exit code 3), a crash, or anything else. Only one copy runs at a time.
# Started at logon by the "AlwaysOnAgent" scheduled task (see install_server_autostart.ps1), in a visible
# window that shows the supervisor's startup banner and live log. Closing that window stops the agent.

$ErrorActionPreference = 'Continue'
$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
$log = Join-Path $project 'logs\launcher.log'

function Write-Log($text) {
    Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text)
}

$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\AlwaysOnAgentLauncher', [ref]$created)
if (-not $created) {
    Write-Log 'Another launcher is already running; exiting.'
    exit 0
}

try { $Host.UI.RawUI.WindowTitle = 'AlwaysOnAgent  (minimize, don''t close: closing stops the agent)' } catch {}

# In the classic console, a click in the window starts a selection (QuickEdit) that pauses every write to it,
# which would freeze the agent at its next log line until a key is pressed. Turn QuickEdit off for this window.
try {
    Add-Type -Namespace AOA -Name Console -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern System.IntPtr GetStdHandle(int n);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(System.IntPtr h, out uint m);
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(System.IntPtr h, uint m);
'@
    $stdin = [AOA.Console]::GetStdHandle(-10)
    $mode = 0
    if ([AOA.Console]::GetConsoleMode($stdin, [ref]$mode)) {
        [void][AOA.Console]::SetConsoleMode($stdin, ($mode -band (-bnot 0x40)) -bor 0x80)   # QuickEdit off
    }
} catch {}

$quickCrashes = 0
while ($true) {
    Write-Log 'Starting supervisor.py'
    Write-Host ("`n=== {0}  starting supervisor.py  (minimize this window; closing it stops the agent) ===`n" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -ForegroundColor DarkGray
    $started = Get-Date
    # Its banner and live log show in this window (the log is also kept in logs\worker.log)
    & python supervisor.py
    $code = $LASTEXITCODE
    $ranFor = ((Get-Date) - $started).TotalSeconds

    if ($code -eq 3) {
        Write-Log 'Restart requested; relaunching.'
        $quickCrashes = 0
        Start-Sleep -Seconds 2
        continue
    }

    # Back off if it keeps dying right after starting (bad config, no network), up to 5 minutes.
    if ($ranFor -lt 60) { $quickCrashes++ } else { $quickCrashes = 0 }
    $wait = [Math]::Min(300, 10 * [Math]::Pow(2, [Math]::Min($quickCrashes, 5)))
    Write-Log ("supervisor.py exited with code {0} after {1:N0}s; relaunching in {2}s." -f $code, $ranFor, $wait)
    Start-Sleep -Seconds $wait
}
