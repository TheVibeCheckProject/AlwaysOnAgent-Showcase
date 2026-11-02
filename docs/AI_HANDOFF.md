# AlwaysOnAgent: the single source of truth

This is **the** map of the system and the rules for working on it. Every AI (Claude, Gemini/Antigravity,
Copilot, Codex, Hark, anything else) and every human starts here. `README.md`, `CLAUDE.md`, `AGENTS.md`,
`GEMINI.md`, `.github/copilot-instructions.md` and `.agents/rules/start_here.md` only point to this file.

Last checked against the code: **2026-10-16** (tool router on Cloudflare Llama 3.3; `chat_with_tools` timeout 20 s; DeskAI build 81; earlier: router timeout bench fix deployed; `scripts/router_report.py` and `scripts/mini_diagnose.py`; tool router: core tools always go; refine keeps sign-ins from models; `POST /api/chat/refine` shipped; ImageGen face optimization; DeskAI build 80).

---

## Rules for every AI (read first)

1. **Read this file first**, in full, before changing anything in this repo or touching the Mini.
2. **Code is the truth.** When this file (or any doc) disagrees with the code, the code wins. Fix the doc.
3. **Change behaviour, update this file in the same commit.** If a fact here changes (model order, deploy
   steps, a thread, a rule), the commit that changes the code also changes this file.
4. **No new top-level docs.** Add a section here, or a file under `docs/` that this file links to. Never a new
   `*_HANDOFF.md`, `START_HERE.md`, `NOTES.md` or per-AI file with its own facts.
5. **One changelog:** [CHANGELOG.md](CHANGELOG.md). Add a dated entry (newest first)
   for anything another AI must know or must not undo. Don't start another log.
6. **Ideas and roadmap live in [IDEAS.md](IDEAS.md)**; propose in its Inbox, signed and dated.
7. **Unsure? Write it as an open question** (bottom of this file) instead of guessing.
8. `tests/test_docs_truth.py` guards some of this: the first chat model named below must match `core/llm.py`,
   every relative link in the docs must resolve, and every entry file must point here.

### Working rules (the ones that break things when ignored)

1. **Verify, don't assume.** Read the code, the logs or the live state before saying how something works or
   whether it worked. Never report a result you didn't see (failed tests included).
2. **Tests before deploy; read pytest's own exit code.** Deploy only with `scripts/deploy_mini.py` (below).
3. **Edit files with a file-editing tool**, not shell heredocs, `sed` or inline escapes (they once turned `\n`
   into real line breaks and broke the live agent).
4. **Nothing public or on the owner's accounts without the owner's tap.** Posts, emails, calendar events,
   go-lives: the agent shows a proposal card and the owner taps Run. Never weaken this.
5. **Secrets never reach a model, a log, the chat history or git.**
6. **Free tiers only.** No billing, no card on file, no paid APIs (the owner once got a ~$2,000 bill).
7. **Be gentle with the Mini's SSH** (a few bundled connections, never dozens).
8. **Ask before anything hard to undo**, and wait for an explicit "go" before using the Mini's real mouse and
   keyboard.
9. **Fix causes, not symptoms.** Comment code with *why*, dated when it came from an incident.

---

## 1. What the system is

The owner is **Alex**. AlwaysOnAgent is his always-on personal AI assistant: chat with long-term memory and
tools, a background task queue, schedules, proactive messages and mail watches. It runs 24/7 on a home mini PC
(**the Mini**, `agent-mini`); Alex talks to it from his phone through the **DeskAI** Android app (the only user
interface; Telegram and the web dashboard are gone). He develops on a Windows laptop. His public project is
**Example Site** (example-site.com), promoted with TikTok videos and Pinterest pins that the
agent makes and posts on his tap.

**Brain and hands:** this repo is the brain. Heavy or specialised work runs in separate tool repos, started as
separate programs (`python <tool>.py <command>`) that print JSON lines on stdout (`progress` events, then one
`done` or `error`). Anything sensitive goes to a tool on **stdin as JSON**, never on its command line. A new
capability becomes its own tool repo plus a thin client in `core/<tool>.py` and chat tools in
`core/assistant_tools.py`.

## 2. Architecture

One process, `supervisor.py`, started at login on the Mini by `scripts/run_forever.ps1` (relaunches after a
crash, or a restart = exit code 3). It runs four threads sharing one SQLite database, `data/state.db` (WAL):

| Thread | Code | Job |
|---|---|---|
| **API server** | `interfaces/hud/` (FastAPI + uvicorn, `0.0.0.0:8080`, `HUD_HOST`/`HUD_PORT`) | Everything DeskAI uses. Auth: `HUD_AUTH_TOKEN` in a header; `?token=` is deprecated (still accepted for old app builds); signed media links via `POST /api/media/link`. Contract: [API.md](API.md) |
| **Task daemon** | `core/daemon.py`, engines in `core/task_engines.py` | Heartbeat every 30 s (`DAEMON_INTERVAL`), woken at once when a task is queued or the running task finishes (`core/events.py` `DAEMON_WAKE`); runs tasks, upkeep, nightly backup, proactive cycle, watches, AI research worker |
| **Push watcher** | `core/push.py` | Firebase (FCM) pushes to DeskAI when tasks finish or fail |
| **Schedule runner** | `core/schedules.py` | Cron-style schedules (videos, pins, reports, reminders, tasks) |

- **Memory:** `core/memory.py` + `core/database.py`. Every message kept, older stretches summarised, facts
  about Alex (written with his name), a prose profile, and procedures ("recipes", `core/procedures.py`).
  Search: Qwen3-Embedding 0.6B through Ollama plus SQLite FTS5 (`core/embedder.py`). Deletes need a second
  AI's agreement. Details: [AGENT.md](AGENT.md).
- **Live events (IDEAS #26):** `core/events.py` is a small in-process bus. `core/database.py` publishes a task
  event wherever a task's status is written (`create_task`, `update_task` with a phase, `set_task_progress`);
  `GET /api/events` (`interfaces/hud/events_api.py`) streams them to DeskAI as SSE with a 15 s heartbeat, bounded
  per-client queues (slow clients are dropped) and at most 20 streams. Only same-process writes are announced;
  the 30 s heartbeat and DeskAI's polling fallback cover the rest. FCM pushes still cover a closed app.
- **Task details (2026-10-07):** every task says who started it (`source` in the task JSON, from
  `metadata.source`; stamped on every creation path, `X-Task-Source` header for outside callers; `scripts/mini_mcp.py`, now
  removed from the Mini, sent `Hark`). Video tasks get a ~240 px JPEG (`core/task_thumbs.py`, in
  `data/task_thumbs`, newest `TASK_THUMBS_KEEP`=200) made right after the render and backfilled during upkeep; it
  outlives the video and is served by `GET /api/tasks/{id}/thumb`. `video_url` is set while the video is kept.
- **Video retention:** TiktokVideos keeps the newest **`VIDEO_KEEP_COUNT`** finished videos (agent `.env`,
  default **50**, Alex's choice 2026-10-07; the Mini has room). `core/tiktok_videos.py` passes it on as
  `MAX_SAVED_VIDEOS` (the tool prunes after each render) and as `cleanup --keep`. Before this the agent stripped
  its environment before starting the tool, so the tool's own default of **3** applied. A `MAX_SAVED_VIDEOS` line
  in TiktokVideos' own `.env` still wins: keep it out of there.
- **Chat:** `interfaces/hud/chat.py` → the chat model with tools from `core/assistant_tools.py`; the core tools
  always go, plus the groups a message needs (picked by a small router model call, else keywords + bge-small
  ONNX). A router verdict never removes the core tools (2026-10-14); one naming an unknown group hands over to the
  keywords (2026-10-16). A lookup that needs digging goes to a background helper (`look_into`,
  `core/follow_up.py`, read-only tools) that answers later as a chat message; a reply that promises later work
  and started nothing starts it (`keep_promise`, 2026-10-27). [TOOLS.md](TOOLS.md).
- **LLM routing:** `core/llm.py` `CloudLLM` (section 3).
- **Approval cards:** `_propose_post` in `core/assistant_tools.py` → `run_proposal` in
  `interfaces/hud/companion.py` when Alex taps Run.
- **Proactive messages, watches, canvas:** [PROACTIVE.md](PROACTIVE.md), [WATCHES.md](WATCHES.md),
  [CANVAS.md](CANVAS.md).

**Tool repos** (all `example-org/*`, private, cloned under `C:\Projects\` on the laptop and the Mini):

| Repo | What it does |
|---|---|
| **TiktokVideos** | Writes the affirmation, narrates (six voice casts, varied), renders the video (the treasure chest and the message in a bottle take turns), posts to TikTok |
| **SocialPostEngine** | Pinterest pins through the official API (sandbox until Standard access) |
| **Connectors** | Gmail (read; send/draft via cards), Google Calendar/Contacts/Drive, Zoho Mail (read-only), GitHub (read-only, DeskAI's Repos screen), the subscriptions DB |
| **VibeStats** | The site's numbers for the Sunday report; keeps `GROWTH_PLAN.md` |
| **ImageGen** | Images from prompts (free services only) |
| **AgentWork** | Code changes to registered repos on a branch, using Antigravity (`agy`) |
| **AgentComputerUse** | Real screen, mouse and keyboard (UI Automation + OCR) for computer tasks; Take over |
| **DeskAI** | The Android app (`example-user/DeskAI`); built on the laptop only |
| Website, MatrixRain, Sanctuary | The site (GitHub Pages from `main`); a log screen show; a 3D side project. Not part of the agent |

## 3. Models (from `core/llm.py`; the code wins)

First chat model: `gemini:gemini-3.5-flash-lite`

`CloudLLM` walks a list of `provider:model` routes and moves on when one is rate limited, overloaded, timed out
or failing. Providers without a key in `.env` are skipped. The lists, as of this commit:

- **`CHAT_MODELS`** (chat with tools): 3.5 Flash-Lite → 3.8 Flash → 3.6 Flash → 3.1 Flash-Lite → Gemma 4 26B
  (Google's native API) → Groq gpt-oss-120b → Cloudflare gpt-oss-120b → Groq qwen3.8-27b → Groq gpt-oss-20b →
  Mistral ministral-14b. 3.5 Flash-Lite first is Alex's choice (2026-10-06, from measurements in the comment
  above `CHAT_MODELS`). Don't move the Flash models back on top without measuring real replies first.
- **Only current models** (2026-10-10, Alex's call): Google's deprecations page routes 3.7 Flash to 3.8 and
  3.5 Flash to 3.6, and `gemini-2.5-flash-lite` answers 404 though still listed. Before adding any model, check
  <https://ai.google.dev/gemini-api/docs/deprecations> and `GET /v1beta/models`;
  `test_no_retired_or_redirected_model_is_listed` guards the lists. A request bigger than a route can take
  (Groq: 8K tokens) is skipped before sending (`REQUEST_TOKEN_CAPS`), and a rejected request logs the
  provider's reason.
- **`BACKGROUND_MODELS`** (memory chores): 3.5 Flash-Lite → Gemma 4 26B → 3.1 Flash-Lite → Groq → Cloudflare.
  No Flash (its ~20 a day are for chat). A local `ollama:qwen3:4b-instruct` route exists
  (`MEMORY_LOCAL_MODEL` / `MEMORY_LOCAL_MODE` in `core/memory.py`) but is **off** since 2026-10-10: judged blind
  on its shadow answers it lost 26-5 to the cloud and did harm 16 times to 1 (IDEAS #10). Re-test a new small
  model with `MEMORY_LOCAL_MODE=shadow` and `scripts/eval_shadow.py` before trusting it.
- **`TASK_MODELS`** (the `cloud` engine's coding tasks): 3.5 Flash-Lite → 3.8 Flash → Codestral → Groq.
- **`ROUTER_MODELS`** (`core/assistant_tools.py`, the chat's tool router): Cloudflare Llama 3.3 70B → Groq
  gpt-oss-120b, then the keyword picker. Never the chat's first model (2026-10-16; measurements in CHANGELOG.md).
  Llama 3.3 is for routing only: it failed as a chat model with tools.
- Pacing per model per minute (`_pace`, `MINUTE_LIMITS`), cooldowns (a timeout under `BENCH_MIN_TIMEOUT`, 15 s,
  like the tool router's 5 s, never benches a model: 2026-10-14), and "after 2 Flash failures in one reply
  skip the other Flash models" (`TURN_FLASH_FAILURES`).
- **Local (Ollama, on the Mini, CPU only):** `qwen3-embedding:0.6b` (memory search; a 639 MB file
  but **~2.4 GB of RAM loaded**, measured on the Mini 2026-10-07: Ollama `/api/ps` 2.37 GB, `llama-server` ~2.9 GB
  private, kept loaded 24 h), `gemma3:4b` (image notes, `core/vision.py`), `qwen3:4b-instruct` (memory chores, off: lost the shadow test). In the
  agent: bge-small ONNX (tool routing), Tesseract (OCR).

The supervisor's startup banner reads the first chat model from `CHAT_MODELS` (no hard-coded name). Free-tier
limits and the benchmark: [GEMINI_FREE_TIER.md](GEMINI_FREE_TIER.md). Providers, local models, usage logging:
[AI_MODELS.md](AI_MODELS.md).

## 4. Free-only rule

Every AI call and service is on a free plan with **no billing account and no card on file**. Never suggest
enabling billing or a paid API (Gemini/Imagen images need billing, so images stay on free services; X/Twitter
posting is skipped because it's paid; Ziggy/Muse needs a card: no). The Gemini free tier is tight (~20 a day
per Flash model, failed calls count; 500 for Flash-Lite; Gemma 4 14,400 a day but 16K tokens a minute), shared
by everything using the key, so never run evals or loops against it casually. ElevenLabs credits are limited:
don't render full videos to check something.

## 5. Hardware: the Mini

**HP EliteDesk 800 G6 Mini, Windows 11 Pro, 10th-gen Intel, 20 GB RAM, no GPU** (confirmed by Alex
2026-10-07; [MINI_SERVER.md](MINI_SERVER.md) lists the i5-10600, 6 cores / 12 threads, 256 GB NVMe). Local
account `owner`, autologon. Tailscale `agent-mini` / `100.x.x.x`; LAN `192.168.x.x`. Port 8080 open to
the home network and Tailscale only. HTTPS for the tailnet: `tailscale serve --bg 8080` gives
`https://mini.tailnet.example` (2026-10-27; tailnet only, never Funnel; `tailscale serve reset` undoes it).
Requests it relays arrive from 127.0.0.1 with Tailscale's headers, so `auth.FORWARDING_HEADERS` makes them need the
token like any other device. BIOS setup password unknown (no power-on after power loss); the
healthchecks.io heartbeat emails Alex when the Mini goes quiet.

**Reaching it** (details in [MINI_SERVER.md](MINI_SERVER.md)):
- `ssh agent-mini`; the remote shell is **Windows PowerShell 5.1**. Run scripts through `scripts/mini.py`
  (one connection, UTF-8, no re-quoting): `python scripts/mini.py ps < x.ps1` or `python scripts/mini.py py < x.py`.
- Few connections: bursts of ssh/scp have wedged sshd (a watchdog restarts it within 5 minutes).
- SSH sessions can't reach the credential store or the screen: git, `gh` and anything on the desktop run in the
  signed-in session via `scripts/run_in_session.ps1`. Long jobs must be started detached
  (`Invoke-CimMethod Win32_Process Create`) or they die with the SSH session.
- Is it up? `curl http://100.x.x.x:8080/api/health` (no token; `/api/status` needs the token since 2026-10-28,
  except from the Mini itself as `http://127.0.0.1:8080`). Calls failing? `python scripts/mini.py py <
  scripts/mini_diagnose.py > diagnose.txt` (read-only: status, failed and answered AI calls, Gemini use today, the
  log's warnings and errors for the last 24 h).
- **Hark has no access to the Mini (removed by Alex, 2026-10-08).** The Mini MCP server (`scripts/mini_mcp.py`,
  full PowerShell over a public Tailscale Funnel URL) is off: the `MiniMCP` scheduled task is unregistered,
  `tailscale funnel reset` was run (no serve config), no `mini_mcp.py` process runs and `MINI_MCP_TOKEN` is gone
  from `.env`. Its scripts (`mini_mcp.py`, `install_mini_mcp.ps1`) were removed from the repo on 2026-10-28 (git
  history has them; audit 1.8). **Don't bring it back or turn Funnel back on** unless Alex asks for it in his own
  words. To stop the agent starting at logon: `Unregister-ScheduledTask AlwaysOnAgent` (the old
  `uninstall_windows_startup.bat` only removed a Startup shortcut the install no longer makes, and is gone).
- Never edit code on the Mini; the live repo must stay clean.
- **If the Mini had to be rebuilt** (the nightly backup covers only `data/state.db`: restore it with
  `scripts/restore_backup.py`): re-create `.env` (key names: `AI_HORDE_API_KEY`, `BACKUP_*`, `CLOUDFLARE_ACCOUNT_ID`,
  `CLOUDFLARE_API_TOKEN`, `DEFAULT_ENGINE`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `HEALTHCHECK_URL`, `HUD_AUTH_TOKEN`
  (DeskAI must get the same value), `LAPTOP_TAKEOVER_TOKEN`/`_URL`, `MISTRAL_API_KEY`, `SERPER_API_KEY`,
  `TAVILY_API_KEY`, `YOUTUBE_API_KEY` (followed channels' uploads, free Data API), `SYCOPHANCY_DISPUTE=on` (the
  dispute note, core/memory.py, on since 2026-10-29 after `scripts/eval_sycophancy.py`); optional `NASA_API_KEY`
  (Today's picture of the day; DEMO_KEY otherwise) and `SYCOPHANCY_GUARD` (measured, didn't help: leave off)), redo the sign-ins (`connect google` in chat; Zoho's Self Client steps; `gh auth login`),
  and expect an empty vault (saved site passwords live in Windows Credential Manager). Secrets never go in the
  backup mirror.

## 6. Deploying (what the code supports)

From the laptop's `C:\Projects\AlwaysOnAgent`, after tests pass locally, commit and push:

| What | How |
|---|---|
| **AlwaysOnAgent** | `python scripts/mini.py py < scripts/deploy_mini.py`. It refuses a dirty live repo, pulls in the signed-in session (`scripts/pull_task.ps1`; a failed pull stops it), runs the full suite **on the Mini** and stops if it fails, restarts via `POST /api/restart` (waits for a running task, up to ~30 min), then prints the health check and recent errors. Unhealthy (not back in time, restarted by itself, or a Traceback in worker.log since the restart) rolls the live repo back to the commit from before the pull and restarts again (2026-10-31). Read all of it. |
| **Tool repos** | Push the tool, then `python scripts/mini.py py < scripts/mini_pull_tools.py` (set `ONLY` inside it for one). Pulls in the session and runs each tool's tests. No restart: tools start fresh per call. |
| **Self-updates** (changes the agent made via AgentWork) | Alex taps ⚡ Apply update; `scripts/apply_update.py` tests, merges, restarts, health-checks, rolls back or pushes. Don't bypass it. |
| **Website** | Push to `main` (GitHub Pages), or 🚀 Push live (`scripts/apply_website.py`). |
| **DeskAI** | Laptop only (never Gradle/Android SDK on the Mini); follow DeskAI's `BUILD.md` and [deskai_and_build_rules.md](../.agents/rules/deskai_and_build_rules.md). `web_dist/version.json` only in the same commit as the new `DeskAI.apk`. |

`POST /api/restart?now=true` cuts a running task short: avoid it around the 08:00 and 20:00 TikTok posts. After
deploying, check it really works the way Alex will use it, and say what you checked.

## 7. Tests

```
pip install -r requirements-dev.txt  # once per machine: pytest, pytest-xdist, pytest-testmon
python -m pytest -q --testmon      # while working: only the tests that run the code you changed (seconds)
python -m pytest -q -n auto        # the full suite, in parallel (~1 min on the laptop, ~30 s on the Mini)
```

from the repo root; read pytest's exit code (no hard-coded count: it changes every week). Alex's choice
(2026-10-28): no test is cut, but they don't all run on every change. While working, run `--testmon`
(pytest-testmon: it records which code each test runs, in `.testmondata`, not in git; the first run on a machine
is a full run that builds that map). When one test fails, rerun just it (`pytest tests/x.py::test_y`), not
everything. Run the full suite once per batch before committing; `scripts/deploy_mini.py` runs it again on the Mini
(`-n auto`, pytest-xdist) and won't restart if anything fails. testmon doesn't see data files or `.env`: after
changing only those, run the full suite. Tool repos: see `scripts/mini_pull_tools.py`.

- **Tests never touch real services.** `tests/conftest.py` blocks every call to an AI model host
  (`no_real_model_calls`), uses bge-small instead of Ollama, a scratch database, and empty tool folders. Mock
  `core.connectors.run`, `CloudLLM`, `urlopen` instead.
- Every new behaviour gets a test; a bug fix gets a test that fails without the fix.
- `tests/test_docs_truth.py` is the docs drift guard (stdlib + pytest, parses `core/llm.py` with `ast`).
- Memory quality is measured outside pytest on real conversations: `scripts/eval_memory.py` on the Mini
  (cases in `data/evals/`, never in git). Model benchmarks: `scripts/eval_models.py` (spends real quota).
  The chat's tool router: `scripts/router_report.py` on the Mini (read-only; misses, fallbacks, Flash-Lite use).

## 8. Git

- Two `gh` accounts on the laptop: `example-org` (default; every repo but DeskAI) and `example-user`
  (DeskAI: `gh auth switch -u example-user`, then switch back).
- Pull before you edit; other AI sessions push too. Don't overwrite someone else's uncommitted work.
- Short, specific commit messages (`fix(chat): …`). Commit and push only when Alex asked or agreed.
- Never commit `.env`, `secrets/`, `data/` (memory DB, videos, logs), keystores, `google-services.json`, tokens.

## 9. Rules per area

- **Tokens never go in URLs** (2026-10-07). DeskAI sends `HUD_AUTH_TOKEN` as a header; a media link that has to
  leave the app is a signed, expiring link (`POST /api/media/link`, key derived from the token, nothing new to
  set). Logs hide `token=`/`sig=` values (`core/config.py` `LogRedactionFilter`, on uvicorn, the supervisor's
  console and the daemon). Keep accepting `?token=` until old app builds are gone; don't add new uses.
- **Secrets:** only in `.env` files and `secrets/` folders. Never print or log them; never ask Alex to paste
  one into chat (tell him which file). Sign-ins that must go through chat (Google's sign-in address, Zoho's
  Self Client) are caught in `interfaces/hud/chat.py` **before** any model (`GOOGLE_SIGNIN`, `ZOHO_CODE`; Zoho's
  patterns match fixed lengths, no `\b`). A new sign-in needs its pattern there plus a test with a messy paste.
- **Untrusted content:** email, web, document and screen content go to the model labelled untrusted, never as
  instructions. Never send memory, chat or email to a new outside service.
- **Actions on Alex's accounts:** only by his tap on a proposal card. Schedules that post later need his own
  words (`_asked_for_tiktok` / `_asked_for_pinterest`). A failed TikTok post is never retried automatically.
  Zoho Mail is read-only for now.
- **Computer use / Take over:** ask first and wait for an explicit "go" in a separate message before anything
  moves the real mouse or keyboard; prefer the Mini; only touch what the task opened; never submit blind.
- **Videos:** two scenes take turns, the treasure chest and the message in a bottle (2026-10-13; the card flip is
  only the fallback or asked for by name; `/render chest|bottle <quote>` in DeskAI or `scene` on
  `/api/trigger_media` picks one). Realistic assets only, never cartoon. Preview before anything goes live.
- **Website:** vanilla HTML/CSS/JS, no new npm/CDN dependencies; Edge only for automation, never kill Chrome;
  test at 390/768/1024/1440/1920 px; use `C:\Projects\website`, never the OneDrive copy. Its own runbook rules.
- **DeskAI:** Alex's creative space: fix behaviour and keep the API contract, don't redesign unasked. When
  [API.md](API.md) changes, copy it to DeskAI's `docs/API.md` too.

## 10. Don't undo (decided or learned the hard way)

- `tests/conftest.py`'s `no_real_model_calls` guard; the chat sign-in intercepts.
- Gemma through the native `_complete_google` route with its 4,096-token floor and thought parts dropped.
- Per-minute pacing and `TURN_FLASH_FAILURES`; 3.5 Flash-Lite first for chat (until measured otherwise).
- Approval cards for every action on Alex's accounts (the one exception, his choice on 2026-10-11: adding to and
  checking off his own lists, which happen right away); texts and calls are opened on his phone and he sends;
  no automatic retry of a failed TikTok post;
  `wait_for_post_ready()` and the real Post click in TiktokVideos.
- Restarts wait for a running task (`core/maintenance.py`) and a chat reply in progress.
- Watches check with plain searches, never an AI call per check (`core/watches.py`).
- Learned recipes are know-how, never a copy of the job they came from: `copy_problem()` refuses one that repeats
  the job's file names, quoted text or long numbers, or scripts more than 3 steps (`core/procedures.py`).
- Retasks (AgentComputerUse `retasks.py`/`replay.py`) keep Alex's hard lines: only the SAME task wording; answers
  never replayed (a question repeats up to the reading point, then the model reads the screen now); written text
  never replayed; only clean runs saved; no reading moves stored; every step through the normal checks.
- Computer runs minimize every window first, use only their own windows, and treat a minimized window as not on
  the screen; they run with the monitor on or off.
- From the 2026-10-28 audit ([AUDIT_2026-10-28.md](archive/AUDIT_2026-10-28.md); each has a test):
  - `mini_files` refuses hidden (dot) folders and files and any copy of the agent's `data/`/`secrets/`.
  - Redaction covers any name with KEY/TOKEN/SECRET/PASS/WEBHOOK/CREDENTIAL, not a fixed list.
  - Tests run with every real-service key removed and httpx/requests blocked from model hosts (`conftest.py`).
  - Loopback trust needs `Host` 127.0.0.1 or localhost; `/api/status` needs the token, `/api/health` doesn't.
  - Without Docker, AI-written tests are never run on the host; a sandbox error is "not verified", not a pass.
  - Deleting, forgetting and stopping (`forget_memory`, schedule delete, `cancel_task`, `stop_watch`) need the
    user's own words in that message; Stop stops the tool loop; after a turn reads a PC file, `fetch_webpage` opens
    only addresses from the user's message or that turn's results.
  - No second, tool-less model answers when every model failed: the reply says so, or lists what the tools did.
    A chat turn has a 150 s deadline.
  - Background events stay out of the chat's verbatim window and the summaries ("While you were away" block).
  - Crash recovery never re-runs an interrupted computer task; standby survives restarts and finished tasks.
- Anything a changelog entry marks "don't undo".

**Decided (don't bring up again):** no 2FA suggestions; no budget/quota tracker panel; Telegram gone; no
X/Twitter; Gemini free-tier training terms accepted; Ziggy/Muse not a front end; NVIDIA's free API set aside; no
"soul file" for now; no mini apps in the canvas; memories use Alex's name ("Alex…", learned, not hard-coded);
the profile is prose, rewritten automatically, not editable; requests the agent carries out are never saved as
facts; the chat never posts or sends by itself.

## 11. How Alex likes to work

Preview before live (show the diff or a still). Plain English, short answers, a recommendation rather than a
list. Report honestly. KISS but sophisticated; save tokens; local models for chores where good enough. When
another AI reviewed something, check each claim and say which are real. When he's away from the laptop, send
questions and "done" notes to DeskAI (a proactive message with a push). "Make a document" means a markdown file
under `docs/`, linked from here.

## 12. Status and roadmap

**Roadmap, ranked ideas and the Done log: [IDEAS.md](IDEAS.md)** (#14 connect anything, #28 watches, #12 model
benchmark, #26 events, #27 computer use, #10 local memory model; Oct 11 checkpoints for chat-model health and
the first Sunday review). What changed recently and what not to undo:
[CHANGELOG.md](CHANGELOG.md).

**Live (as of 2026-10-13):** chat with long-term memory, recipes (with the copy guard) and the weekly memory check;
proactive candidate pool and away shift (`core/candidates.py`, `core/away_shift.py`, `core/proactive.py`: pooled candidates,
embedding-based novelty and repeat checks, AI ranking, 10-minute lull, freshness checks, 6-hour silence check-in with
exponential backoff, 2-hour idle away shift background worker emitting activity events, transparent `why` lines and
reaction feedback endpoints); open loops (`core/open_loops.py`: facts waiting on external outcomes, researched on cadence
and grounded with verified quotes); curious questions; web, video, Pinterest (live, 2 pins a day, and the pin maker
with drafts approved in chat), AgentWork, schedule, Gmail + Google Calendar/Contacts/Drive (sending via cards),
Zoho Mail (read) tools; mail watches; DeskAI's Repos screen (share and save files); canvas drafts; proactive messages
(the brief, written when Alex shows up and shown in Today's header by default (`brief_to`); notices; questions;
what isn't urgent waits in the outbox until he's around); DeskAI's **Today** screen (`core/today.py`,
`/api/today*`: editions of findings, followed YouTube channels, settled loops, finished tasks, with his recipe and
the weather); the chat's read-only look at the Mini's files (`core/mini_files.py`, `/api/files/m/*`); background
image jobs (`/api/images/jobs`); the Sunday growth review with proactive candidate metrics; automated AI research
worker (scans for free AI credits, developer previews, free tiers, and hackathons fitting Alex's building work,
saving digests to memory and canvas); nightly backups; live events and mission details; computer use with Needs you,
sign-ins from the app, Take over (Mini and laptop), plain-language narration, file actions and retasks.

**Live events (#26) and mission details** are merged and live (`GET /api/events`; task `source`, thumbnails,
`video_url`), and DeskAI shows a mission's details when tapped.

**Computer use, honestly:** AgentComputerUse commits of 2026-10-07 (`8d1504a`…`753f960`) add Bézier mouse
movement, frame settling, self-healing anchors, a domain-locked Credential Manager, a profile store and
single-pass forms, recipe replay and the Phase 8 fail-safes. A 2026-10-08 review found and fixed real bugs in them
(the owner's hand no longer stopped a move, scrolling ended runs, a vanished "Follow" became "Unfollow", buttons
got a second Enter, pauses closed the tab, Needs you gave up after 3 minutes); details in
[CHANGELOG.md](CHANGELOG.md). **Works live (2026-10-08, AgentComputerUse `f42b7f8` on the Mini):** the Notepad
test (open, type a paragraph, delete the second half, close without saving) finished **done in 28 s with 3 model
requests**, checked against what Notepad really held, after thirteen live runs that each fixed something.
How it works now, and what not to undo:
- **Models:** Flash-Lite first, then Gemma 4 26B and other free routes; never Flash (`brain.py`). Screen reading is
  Windows' own (accessibility list + Windows OCR); models only decide. The local gemma3:4b "second look" is off
  unless `ACU_LOCAL_LOOK=1` (too slow on the Mini's CPU).
- **Fewer round trips:** the first reply carries the plan; a reply may chain the next actions (`then`, desktop apps
  up to 6, the web 1 and never after a page load), each still checked; desktop apps type fast, the browser at human
  pace.
- **Checks:** a person at the computer means real mouse input over 50 px (Windows' injected flag), not cursor drift;
  text is edited by retyping it whole (selection/delete keys are refused) and read back (Notepad through the Text
  pattern); a task's "done" is double-checked from a step trail with real field readings, judging the result, not
  the method; an app is never reopened to "check"; two refused "done"s go to Alex.
- **Screen:** runs work with the monitor on or off by default (Windows' 1024×768 no-monitor screen when it's off;
  `--need-monitor` to stop instead); a size change mid-run makes it look again instead of stopping.
- **Take over:** "View only" in DeskAI (build 57+) watches without taking control; a banner shows the running
  task and step (build 58, `activity.py`, `GET /api/computer/takeover/activity`).
- **Testing from the laptop:** `run --goal-file` (no quoting through shells), started via
  `scripts/run_in_session.ps1`, which now runs hidden and ends its whole tree on timeout.
- **Windows 11 Notepad** keeps unsaved text when its window closes and brings it back: tasks work on a page of their
  own, and "close without saving" means Ctrl+W on the tab, Don't save.

Since tried from DeskAI itself (2026-10-09): the Notepad task finished in about 10 s, watched in View only with the
banner; a web task (the weather in Springfield in Edge) ran quickly but answered 60°F, a number on the page that was
probably tonight's low. Answers must now be the exact reading asked for and say what and where it is, and the
double-check refuses a forecast, high, low, hourly or "feels like" number for "now" (ACU 2a93912). The profile store and form filler aren't wired into the loop. The computer-use docs below describe
plans and research; they are not proof that a feature works.

**2026-10-11:** every run minimizes all windows first and opens its own (the root cause of the stuck runs was
Windows refusing a background program the foreground; see CHANGELOG). The tool does file work whole: `open_file`
(Ctrl+O, a web page's file picker, or the file's app; Notepad for text with no app) and `save_as` (checked on
disk). Fresh, untuned batches (batches 3-10, every failure traced from its run log and fixed in general) ended
with 11/11 clean, monitor off. `loop.py` is split into mixins: `files.py`, `verify.py`, `owner.py`, `chain.py`,
`replay.py` (+ `goals.py`, `rules.py`, `prompt.py`). **Retasks:** a clean run is saved and the same task asked
again is repeated without the AI (DeskAI's Retasks screen and `/retask`). Narration lines carry a `level`
(retask lines violet) and a `kind` (open, type, click, save, plan) for DeskAI's colored "What it did".

## 13. Where the details are

| Doc | What |
|---|---|
| [API.md](API.md) | The HTTP API DeskAI uses (its own source of truth, mirrored in DeskAI) |
| [AGENT.md](AGENT.md) | Brain internals: tasks, memory layers, upkeep, `.env` settings, code map, schedules |
| [TOOLS.md](TOOLS.md) | Chat tools, tool groups and routing, how to add a tool |
| [AI_MODELS.md](AI_MODELS.md), [GEMINI_FREE_TIER.md](GEMINI_FREE_TIER.md) | Providers, local models, usage logging; Gemini limits and the benchmark |
| [MINI_SERVER.md](MINI_SERVER.md) | The Mini: access, auto-start, deploy scripts, secrets and data map, backups, rebuild |
| [PROACTIVE.md](PROACTIVE.md), [WATCHES.md](WATCHES.md), [CANVAS.md](CANVAS.md) | Proactive messages; mail watches; drafts |
| [CONNECTIONS_PLAN.md](archive/CONNECTIONS_PLAN.md) | Plan for "connect to anything" (#14) |
| [IDEAS.md](IDEAS.md) | Roadmap |
| [CHANGELOG.md](CHANGELOG.md) | The changelog |
| [deskai_and_build_rules.md](../.agents/rules/deskai_and_build_rules.md) | DeskAI build and update rules |
| [AUDIT_2026-10-27.md](archive/AUDIT_2026-10-27.md) | Whole-system audit: chat token budget, security, reliability; ranked fixes (applied the same day; see its Status) |

**Reference only, not source of truth** (plans and research; may describe things not built):
[COMPUTER_USE_PLAN.md](archive/COMPUTER_USE_PLAN.md),
[COMPUTER_USE_ARCHITECTURE_RESEARCH.md](archive/COMPUTER_USE_ARCHITECTURE_RESEARCH.md),
[COMPUTER_USE_FAILSAFES.md](archive/COMPUTER_USE_FAILSAFES.md),
[CHAT_TOKEN_OPTIMIZATION_PLAN.md](archive/CHAT_TOKEN_OPTIMIZATION_PLAN.md) (its Option B, the router, is built; see TOOLS.md).

Removed on 2026-10-14 (in git history): `DEPLOY_2026-10-14_ROUTER_FIX.md` (router timeout bench fix merged and deployed to Mini; see CHANGELOG.md).

Removed on 2026-10-11 (in git history): `COMPUTER_USE_ENHANCEMENTS_PLAN.md` (built and deployed Oct 8),
`WHAT_IS_GOING_TO_HAPPEN.md` (an early guide that no longer matched), `HARK_REFERENCE_INTEL.md` (the ideas
worth keeping are in IDEAS.md), and `scripts/deskai_patches/` (all five patches are in DeskAI).

Removed on 2026-10-07 (in git history): `SYSTEM.md` (folded into this file and MINI_SERVER.md),
`TOKEN_EFFICIENT_ARCHITECTURE_ROADMAP.md`, `TOOL_ROUTING_NEXT.md`, `SESSION_HANDOFF_GALLERY.md`,
`deskai/REVIEW_LOG.md` (all historical and superseded).

## 14. Open questions for Alex

None right now.

Resolved 2026-10-11: computer use on the web was verified on fresh tasks (Wikipedia, nps.gov, weather.gov,
example.edu, MDN and W3Schools file pickers), all clean (section 12).

Resolved 2026-10-08: the reviewed computer-use fixes were deployed and the Notepad test passes live (section 12);
Hark's Mini access was removed (section 5).

Resolved 2026-10-07 (by Hark): the Telegram-era `scripts/install_windows_startup.bat` and
`scripts/start_sentinel.vbs` were deleted (nothing on the Mini used them: its Startup folders, Run keys and
scheduled tasks only start the agent via `run_forever.ps1`); `run_agent.bat` stays as the plain manual launcher.
The supervisor banner already reads `CLOUDFLARE_API_TOKEN` the way `core/llm.py` does. Embedder RAM measured
(section 3). The changelog is now [CHANGELOG.md](CHANGELOG.md).
