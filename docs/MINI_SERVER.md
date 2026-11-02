# The mini PC: the always-on home server

Everything runs here: AlwaysOnAgent, TiktokVideos, SocialPostEngine, VibeStats and AgentWork. The laptop is only for development.

## The machine

| | |
|---|---|
| Hardware | HP EliteDesk 800 G6 Mini: Intel i5-10600 (6 cores / 12 threads), 20 GB RAM (16 + 4), Intel UHD 630 graphics, 256 GB NVMe SSD (C:). A second SSD is fitted but isn't detected. |
| OS | Windows 11 Pro 25H2, local account `owner` (not a Microsoft account) |
| Names | `agent-mini` · Tailscale `100.x.x.x` · home network `192.168.x.x` (no fixed reservation in the router yet) |
| Video speed | about 5 frames a second, so roughly 4–5 minutes per ~18 s video |

## Getting in

| How | From | Details |
|---|---|---|
| **SSH** | the laptop | `ssh agent-mini`: key `~/.ssh/agent-mini_ed25519`, alias in `~/.ssh/config`. It's trusted in `C:\ProgramData\ssh\administrators_authorized_keys`, and the default shell is Windows PowerShell 5.1. |
| **Remote Desktop** | the laptop | `100.x.x.x`, user `agent-mini\owner`. Close the window when done. **Don't sign out**: signing out stops the agent until the next reboot. |
| **Tailscale** | laptop, phone | "Run unattended" is on; the Mini's key expiry should be disabled in the admin console |

### The signed-in-session rule (important)
Some things only work in the **signed-in desktop session**, not over SSH:
- **The GPU:** headless Edge renders videos on it.
- **Saved logins:** `gh`, `git` over HTTPS and Antigravity (`agy`) keep them in Windows' password store,
  which SSH sessions can't open.

The agent runs in that session (see Auto-start), so it's fine. For admin work that needs a login (for example
`git pull`), run it as a one-off scheduled task in that session:

```powershell
$a = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c git pull --ff-only > C:\Users\owner\aoa-aside\pull.log 2>&1" -WorkingDirectory C:\Projects\AlwaysOnAgent
$p = New-ScheduledTaskPrincipal -UserId "AGENT-MINI\owner" -LogonType Interactive
Register-ScheduledTask -TaskName AOA-Pull -Action $a -Principal $p -Force | Out-Null
Start-ScheduledTask AOA-Pull; Start-Sleep 12; Unregister-ScheduledTask AOA-Pull -Confirm:$false
```

For anything with quotes or spaces in its arguments, put the command in a `.bat` file and point the action at
that; `cmd /c "..."` mangles nested quotes.

## Auto-start and staying up

1. **Power:** sleep and hibernate are off. In the BIOS (F10), *After Power Loss → Power On* would ideally
   restart the PC after an outage, but this is currently not possible because the BIOS has a setup password
   nobody remembers (so a manual power button press is required after a power loss). The **external
   heartbeat** is how the owner finds out: every 5 min the agent pings healthchecks.io (`HEALTHCHECK_URL`,
   `core/heartbeat_ping.py`), and when the pings stop for 15 min, healthchecks.io emails the owner.
2. **Sign-in:** Sysinternals **Autologon** signs `owner` in at boot (the password is stored encrypted, not in
   the registry in plain text).
3. **Start:** the scheduled task **AlwaysOnAgent** (at logon, in that session) runs `scripts\run_forever.ps1`
   in a visible window titled *AlwaysOnAgent*: the startup banner and the live log (seen on the Mini's screen or
   over Remote Desktop). **Minimize it, don't close it:** closing it stops the agent until the next sign-in or
   `Start-ScheduledTask AlwaysOnAgent`. Ctrl+C is fine (the launcher starts it again).
4. **Keep running:** `run_forever.ps1` restarts `supervisor.py` after a restart request (exit code 3) or a
   crash, and backs off up to 5 minutes if it keeps dying at startup. A mutex keeps it to a single copy. Its log
   is `logs\launcher.log`.
5. **Network:** the firewall rule **AlwaysOnAgent HUD** opens port 8080 to the home network and Tailscale
   (`100.64.0.0/10`) only.

`scripts\install_server_autostart.ps1` (admin PowerShell) creates steps 3 and 5. A reboot test brings the API
back about 3½ minutes after power-on.

**SSH watchdog:** `scripts\install_sshd_watchdog.ps1` (admin PowerShell) registers the task "SSHD Watchdog":
every 5 minutes, as SYSTEM, it checks that sshd answers with its greeting on 127.0.0.1 and the Tailscale
address, and restarts it if not (logged in `logs\sshd_watchdog.log`); Windows also restarts sshd if it crashes.
On 2026-09-30 sshd stopped answering remote connections ("Connection reset") after a burst of quick ssh/scp
calls, until `Restart-Service sshd`. Keep remote work to a few connections.

**Is it up?** `curl http://100.x.x.x:8080/api/health` (no token needed; `/api/status` needs the token since
2026-10-28), or the connection dot in DeskAI.

## Deploying updates

Develop and test on the laptop, push to GitHub, then run the matching script **from the laptop** (both are
commented step by step; [AI_HANDOFF.md](AI_HANDOFF.md) section 6 is the source of truth):

- the agent: `python scripts/mini.py py < scripts/deploy_mini.py` (clean-repo check, pull in the session, full
  tests on the Mini, restart only if they pass, health check, recent errors);
- the tools: `python scripts/mini.py py < scripts/mini_pull_tools.py` (pull each in the session, run its tests).

What those scripts do, per part:

| What | How |
|---|---|
| **AlwaysOnAgent** | A restart waits for a running task to finish and holds new work meanwhile, so the queue needn't be idle. `git pull` on the Mini in the session (`scripts\pull_task.ps1` does it from SSH through a one-off scheduled task), run `python -m pytest` there, and only if it passes, `POST /api/restart` with the token. Then watch `/api/status` for a minute and `logs\worker.log` for errors. The agent is back in a few seconds. |
| **TiktokVideos** | `git pull` in `C:\Projects\TiktokVideos` (in the session). No restart is needed: the agent starts it fresh for every video. |
| **SocialPostEngine** | `git pull` in `C:\Projects\SocialPostEngine` (in the session). No restart is needed. |
| **VibeStats** | `git pull` in `C:\Projects\VibeStats` (in the session). No restart is needed. |
| **Connectors** | `git pull` in `C:\Projects\Connectors` (in the session; branch `master`). No restart is needed. |
| **AgentComputerUse** | `git pull` in `C:\Projects\AgentComputerUse` (in the session). No restart is needed for computer tasks. |
| **Website jobs** | AgentWork project `website` (rules in `C:\Projects\AgentWork\projects\website\rules.md`, from the site's CLAUDE.md). Tapping `🚀 Push live` on the job's card runs `scripts/apply_website.py`: catch up with the live `main`, `npm ci` if needed, `npm run build`, `npm test`, then push `main` (that deploys the site). Any failure pushes nothing. |
| **Self-updates** | A change the agent made to itself through AgentWork: preview the diff first via the `🔍 Preview diff` action chip or "show diff for task-NNN", then tap `⚡ Apply update` on the card (in chat, "apply it" makes the agent show that card). `scripts/apply_update.py` runs dual test gating (isolated basetemp `tempfile.mkdtemp(prefix="aoa_update_test_")`), merges, restarts, checks health for 60s, and rolls back or pushes by itself (outcome: push notification, `data\updates\<job>.json` / `.log`). The live repo must have no uncommitted edits of its own. |
| **AgentWork** | `git pull` in `C:\Projects\AgentWork` (in the session). No restart is needed: the agent runs it fresh for every job. |
| **DeskAI** | Built and signed on the laptop, then published to GitHub; phones update through the in-app updater. See DeskAI's `BUILD.md` and [.agents/rules/deskai_and_build_rules.md](../.agents/rules/deskai_and_build_rules.md). |

## What lives only on this machine

It isn't in git and must be carried by hand to a new machine. What each secret and data file is (moved here
from the old SYSTEM.md on 2026-10-07):

| Secret | File | Used by |
|---|---|---|
| AI keys (Gemini, Groq, Cloudflare, Mistral, Tavily, Serper, Brave), the HUD token, backup settings, the healthchecks.io ping URL (`HEALTHCHECK_URL`) | `AlwaysOnAgent\.env` | the agent |
| Memory eval cases and results, built from real conversations | `AlwaysOnAgent\data\evals\` (gitignored) | `scripts/eval_memory.py` |
| Files shared in the chat (30 days) | `AlwaysOnAgent\data\attachments\` (gitignored) | the agent |
| Firebase key for push notifications | `AlwaysOnAgent\data\secrets\fcm-service-account.json` | the agent |
| Memory and task database | `AlwaysOnAgent\data\state.db` (+ nightly backups) | the agent |
| Gemini key (quotes), ElevenLabs voices, scene settings | `TiktokVideos\.env` | TiktokVideos |
| TikTok login | `TiktokVideos\secrets\tiktok_session.json` | TiktokVideos |
| Quote history | `TiktokVideos\data\used_quotes.json` | TiktokVideos |
| Pinterest app ID and secret | `SocialPostEngine\.env` | SocialPostEngine |
| Pinterest sign-in (refreshes itself; keep it on one machine) | `SocialPostEngine\secrets\pinterest_<env>.json` | SocialPostEngine |
| What's been posted | `SocialPostEngine\data\posted_<env>.json` (`sync` rebuilds it from the boards) | SocialPostEngine |
| Google OAuth client and sign-in (Gmail, Calendar, Contacts, Drive; renewed from the phone with "connect Google") | `Connectors\secrets\google_client.json`, `google_token.json` | Connectors |
| Zoho Mail Self Client and sign-in (renewed with "connect Zoho") | `Connectors\secrets\zoho.json` | Connectors |
| Subscription database and the mail sync position | `Connectors\data\subscriptions.db` (a fresh first sync rebuilds it, ~30 min) | Connectors |
| Clarity and MailerLite keys | `VibeStats\.env` | VibeStats |
| The nightly numbers | `VibeStats\data\collections.jsonl` (can't be rebuilt: Clarity keeps 3 days) | VibeStats |
| TikTok readings | `TiktokVideos\data\stats_history.jsonl` | TiktokVideos |
| GitHub and Antigravity logins | Windows' password store (only in the signed-in session) | `gh`, `git`, `agy` |
| DeskAI signing key and Firebase app config | laptop only: `DeskAI-source\debug.keystore`, `Downloads\google-services.json` | building DeskAI |

Where each lives, per folder:

- `C:\Projects\AlwaysOnAgent\.env`, `data\` (the memory database, backups and `secrets\fcm-service-account.json`)
- `C:\Projects\AgentGallery\images\` (external directory for all generated AI artwork, kept clean of repository git tracking)
- `C:\Projects\TiktokVideos\.env`, `secrets\tiktok_session.json`, `data\used_quotes.json`, `videos\`
- `C:\Projects\SocialPostEngine\.env`, `secrets\` (the Pinterest sign-in: move it, don't copy it, because a
  refresh retires the old token), `data\` (or rebuild it with `python socialpostengine.py --env production sync`)
- `C:\Projects\VibeStats\.env`, `data\` (the nightly numbers: Clarity keeps only 3 days, so they can't be refetched)
- `C:\Projects\Connectors\secrets\` (the Google client and Gmail sign-in; the same token works on both machines),
  `data\subscriptions.db` (or let a first sync rebuild it, ~30 minutes)
- `C:\Projects\AlwaysOnAgent\data\attachments\` (chat uploads, kept 30 days)
- `C:\Projects\MatrixRain\` (not part of the agent: the Matrix-rain screen show, repo `example-org/MatrixRain`;
  `run_matrix.bat` opens it full screen in the "MatrixRain" Windows Terminal profile (font 10), q returns
  to the agent's window). At login the scheduled task **MatrixRain** (`install_login_task.ps1`) waits for the
  agent to save this boot's specs box (`data\startup_specs.txt`, written by supervisor.py each start, gitignored)
  and opens the rain, which types those specs out and melts them into it. Turn it off:
  `Unregister-ScheduledTask -TaskName MatrixRain -Confirm:$false`
- Installed: **Tesseract OCR** (`winget install UB-Mannheim.TesseractOCR`) for chat attachments, and **Ollama**
  (`winget install Ollama.Ollama`, per user, starts with the session on port 11434) with
  `ollama pull qwen3-embedding:0.6b` (**memory search**: without it, search is keyword-only) and
  `ollama pull gemma3:4b` (image notes) and `ollama pull qwen3:4b-instruct` (background memory chores, 2.5 GB;
  unused since 2026-10-10, `MEMORY_LOCAL_MODE` defaults to off: it lost the shadow test, IDEAS #10). `MEMORY_EMBED_MODEL`, `VISION_MODEL`, `MEMORY_LOCAL_MODEL`,
  `MEMORY_LOCAL_MODE` and `OLLAMA_URL` in the agent's .env change them. The agent never starts Ollama itself. A new search model re-embeds all memory by itself
  (a few minutes).
- `C:\Projects\AgentWork\workspaces\` and `repos\` (job copies and clones; the code, project registry and rules
  are in the private repo `example-org/AgentWork`)
- Logins made in the session: `gh auth login` (accounts **example-org**, the active one, and
  **example-user**), and `agy` (the owner's Google account)

Copy `.env` files with a tool that doesn't add a byte-order mark. PowerShell 5.1's
`Set-Content -Encoding UTF8` adds one, which hides the first setting. Use
`[IO.File]::WriteAllText(path, text, (New-Object Text.UTF8Encoding $false))`.

## Backups

- **Memory:** every night at 3:00 (`BACKUP_HOUR`), with a catch-up the next time it runs if the PC was off,
  and on demand from DeskAI's Backup button. A copy goes to `data\backups\` (the newest 14 are kept) and
  another to `G:\My Drive\AlwaysOnAgent Backups`. Google Drive for desktop is signed in with no folder sync;
  it's only the `G:` drive. After each copy to Drive, that copy is restored to a scratch file and its messages
  counted; a copy that doesn't restore makes the backup count as failed and the owner is alerted. Restore with
  `python scripts\restore_backup.py`.
- **Code:** every project is on GitHub, private (AlwaysOnAgent, TiktokVideos, ImageGen, VibeStats,
  SocialPostEngine, AgentWork, Connectors, MatrixRain, DeskAI, the website). Secrets and per-machine data
  are never in them; they're listed above.

## Rebuilding it from scratch

1. Windows 11 Pro, all updates, a local account `owner`, no OneDrive (it redirects Documents and brings the
   laptop's PowerShell profile along).
2. In PowerShell:
   - `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
   - `winget install` Python.Python.3.13, Git.Git, OpenJS.NodeJS.LTS, GitHub.cli, Gyan.FFmpeg,
     tailscale.tailscale, Google.AntigravityCLI, Google.GoogleDrive and Microsoft.Sysinternals.Autologon.
3. Tailscale: sign in, turn on Run unattended and disable key expiry. Turn on Remote Desktop (reboot if the
   `rdp-tcp` listener doesn't show in `qwinsta`). Install OpenSSH Server and add the laptop's key.
4. In the session: `gh auth login` (both accounts, example-org active) and `gh auth setup-git`.
   Clone AlwaysOnAgent and TiktokVideos into `C:\Projects`, run `pip install -r requirements.txt` in each
   and copy AgentWork over. Sign in to `agy` once, and to Google Drive.
5. Copy the machine-only files listed above. For the TikTok login, either copy `tiktok_session.json` or run
   `TiktokVideos\login_tiktok.bat`.
6. Admin PowerShell: `scripts\install_server_autostart.ps1`. Then run Autologon and enable it.
7. Reboot. The API should answer within about 4 minutes. Test with a video from DeskAI, and a TikTok dry run:
   `python tiktokvideos.py post --video latest --dry-run`.
