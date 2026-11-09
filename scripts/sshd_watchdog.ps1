# Run every 5 minutes as SYSTEM (scheduled task "SSHD Watchdog", see install_sshd_watchdog.ps1): checks that
# the SSH server answers with its greeting, on this PC and on its Tailscale address, and restarts it if not.
# (2026-09-30: sshd stopped answering remote connections, "Connection reset", until it was restarted by hand.)
$log = Join-Path (Split-Path -Parent $PSScriptRoot) 'logs\sshd_watchdog.log'

function Test-SshGreeting([string]$address) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($address, 22, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(5000)) { return $false }
        $client.EndConnect($connect)
        $stream = $client.GetStream()
        $stream.ReadTimeout = 5000
        $buffer = New-Object byte[] 64
        $n = $stream.Read($buffer, 0, 64)
        return [Text.Encoding]::ASCII.GetString($buffer, 0, $n).StartsWith('SSH-')
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Write-Log([string]$message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message" | Add-Content -Path $log -Encoding UTF8
}

$addresses = @('127.0.0.1')
$tailscale = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like '100.*' } | Select-Object -First 1 -ExpandProperty IPAddress
if ($tailscale) { $addresses += $tailscale }

$service = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $service) { exit 0 }
if ($service.Status -ne 'Running') {
    Write-Log "sshd was $($service.Status); starting it"
    Start-Service sshd
    exit 0
}

# Two tries, a few seconds apart, so one slow moment doesn't restart it
$failed = @($addresses | Where-Object { -not (Test-SshGreeting $_) })
if ($failed.Count -gt 0) {
    Start-Sleep -Seconds 10
    $failed = @($addresses | Where-Object { -not (Test-SshGreeting $_) })
}
if ($failed.Count -gt 0) {
    Write-Log "no SSH greeting on $($failed -join ', '); restarting sshd"
    Restart-Service sshd -Force
    Start-Sleep -Seconds 3
    $ok = @($addresses | Where-Object { Test-SshGreeting $_ })
    Write-Log "after restart: answering on $($ok -join ', ')"
}
