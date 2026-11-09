# AlwaysOnAgent: how the brain works

The agent is one Python program, `supervisor.py`, run by `scripts\run_forever.ps1` on the mini PC
([MINI_SERVER.md](MINI_SERVER.md)). The supervisor runs four threads that share one SQLite database,
`data\state.db`:

| Thread | Code | Job |
|---|---|---|
| **Daemon** | `core/daemon/` | Every 30 s (`DAEMON_INTERVAL`): picks the next task, runs it, records the outcome; upkeep and backups |
| **API** | `interfaces/hud/` (FastAPI, uvicorn on `0.0.0.0:8080`) | The API DeskAI uses ([API.md](API.md)). There's no web dashboard: DeskAI shows everything |
| **Push notifier** | `core/push.py` `TaskPushWatcher` | Sends a push through Firebase to the registered phones when a task finishes or fails |
| **Schedules** | `core/schedules.py` `ScheduleRunner` | Every 20 s: fires due schedules (a video, a phone reminder, or a background task). See Schedules below |

`supervisor.py` exits with code 3 when a restart is requested (`POST /api/restart`, or `/restart` in chat), and
the launcher starts it again. A restart first waits for a running task to finish (`core/maintenance.py`, up to 30
minutes). Meanwhile no queued task starts and no schedule fires except reminders; schedules that came due fire
after the restart. `POST /api/restart?now=true` skips the wait.

## Tasks

A task is a row in the `tasks` table (`core/database/`). Its lifecycle:

```
backlog ──► in_progress ──► completed
                 │
                 └────────► failed   (cancelled = failed with exit code 137, reported as "cancelled": true)
```

- **Created by:** DeskAI buttons and chat actions, through `core/actions.py`, so a button and a chat request
  make the same task. Also by `/task` in chat, suggestions the owner runs, and `POST /api/tasks`.
- **Who started it:** every creation path stamps `metadata.source` ("DeskAI quick dispatch", "DeskAI chat",
  "Hark", "schedule #1 '…'", …); the API shows it as the task's `source` ("unknown" for older tasks without one).
  Callers can name themselves with `X-Task-Source` (`scripts/mini_mcp.py` sent "Hark"; it's off the Mini since
  2026-10-08).
- **Picked:** one at a time, by priority (urgent, high, medium, low), then the oldest first. New work wakes a
  daemon in standby.
- **Engines** (`core/task_engines/`, routing in `EngineDispatcher.execute`):

| Engine | What runs it |
|---|---|
| `media_pipeline` | **TiktokVideos**, run as a separate program (`core/tiktok_videos.py`): `render`, then `post` for TikTok jobs. Its JSON progress lines become the task's `progress`, and the quote's AI calls are logged in the free budget. |
| `agentwork` | **AgentWork**, run as a separate program: a change to one of the owner's repos, on a branch |
| `antigravity` | the Antigravity CLI (`agy`) in the task's own folder |
| `cloud` | the free cloud models write the files; a sandbox runs any tests they wrote |
| `ollama` | a local Ollama model (offline fallback) |
| `auto` | Antigravity first, then `cloud` if it fails |

- **Folders:** each task works in `AgentWork\workspaces\<task id>\` (AgentWork makes its own). After a run,
  the dispatcher checks that nothing outside the task's folder changed, and the agent's own files and
  `.env` above all. If something did, the task fails and the owner is alerted.
- **Cancel:** `POST /api/cancel/{id}` marks the task cancelled and ends its process tree. A video task
  cancelled while waiting never starts, and a finished render isn't posted after a cancel.
- **Watchdog:** a task with no result after twice its time limit (at least 30 minutes) is failed, the queue is
  freed, and the owner is alerted.
- **Progress & Live Cards:** `progress_json` holds the live stage, percent and time left while a task runs. The API adds
  `status_text`, `cancelled`, `result`, and dynamic `actions` chips ([API.md](API.md)).
- **Action Chips (`interfaces/hud/action_chips.py`):** Live cards display contextual 1-tap buttons:
  `⏹ Cancel` while running; `▶ Play Preview` and `🚀 Post to TikTok` for rendered videos; for a finished
  `alwaysonagent` or `website` job, `🔍 Preview diff` and the go-live chip (`⚡ Apply update` / `🚀 Push live`, a direct
  `POST /api/tasks/{id}/apply`: the owner's tap is the go-ahead, see [TOOLS.md](TOOLS.md#going-live-one-tap-on-the-card));
  `💬 Discuss Blockers` when input is needed; and `🔄 Retry Task` on failure.

## Memory (`core/memory/`)

Memory is the owner's top priority for the assistant. It's layered like a person's memory:
1. **Recent conversation:** the latest messages, word for word.
2. **Summaries:** older conversation, compressed into ever-coarser levels.
3. **Profile:** a short always-present brief about the owner, rebuilt from the facts in quiet time. It's
   written as two to four paragraphs of plain prose (the owner reads it in DeskAI), strictly from the facts.
4. **Facts:** bi-temporal facts (`valid_from`, `invalid_from`, `superseded_by`), extracted after every exchange and updated by superseding rather than overwriting.
5. **Archive:** every message, kept forever and searchable.

- **The owner's name:** once a profile fact gives it, `owner_name()` keeps it in `system_state`, and facts,
  summaries and the profile are written with it ("Alex prefers…", not "The user…"). Nothing is hard-coded.
- **What becomes a fact:** only what the owner said about themselves, with their own words as evidence.
  Requests the assistant carries out (schedules, videos, lookups, tasks) aren't facts: the schedule or task
  keeps them. Thanks, greetings and emoji-only messages skip the extraction call.
- **Search** is hybrid: SQLite FTS5 keyword ranking plus meaning, on Qwen3-Embedding 0.6B through the Mini's
  Ollama (`OllamaEmbedder` in `core/embedder.py`; `MEMORY_EMBED_MODEL`, "bge" for the in-process
  bge-small). The model brings its own similarity cut-offs, and is told the owner's name so "I" in a
  question finds facts written with the name. Stored vectors are tagged with the model; changing it
  re-embeds all memory in the background. Point-in-time queries (`as_of=...`) work on facts. If Ollama isn't
  answering, search uses keywords and logs it; the agent never starts Ollama itself.
- **Forgetting:** `forget_memory` in chat (or Forget in DeskAI) removes the thing from facts, past messages
  and summaries. Rewrites go in small batches and are refused if they come back unchanged, longer, or still
  about the forgotten thing.
- **Procedural memory** (`core/procedures.py`, table `mem_procedures`): recipes, "when X, do Y", about the agent's
  own work, by area (`tool:<name>`, `project:<name>`, `engine:<name>`, or a job like `tiktok videos`). Learned
  from how-tos the owner teaches in chat (the fact extraction call picks them up, grounded in the owner's words),
  from chat tool calls that returned an error and then worked, and from tasks that succeeded only on retry; or
  added in DeskAI. A learned one is saved only if the second opinion agrees the evidence shows it. The best few
  go into each chat turn and task (`PROCEDURES_PER_PROMPT`, `PROCEDURES_BUDGET_TOKENS`; AgentWork jobs get only
  these, no personal memory). Each use is scored (helped / failed); a learned recipe that keeps failing is
  retired; the owner's own and pinned ones never are. `forget_memory` reaches recipes too.
- **Which model does memory chores:** fact and recipe extraction, summaries and the profile run on the cloud's
  background models. A local `qwen3:4b-instruct` route exists but is off (`MEMORY_LOCAL_MODE`, default `off`
  since 2026-10-10: it lost the shadow test, docs/AI_MODELS.md, IDEAS #10). Erasing never runs locally.
- **Weekly memory check** (`core/memory_review.py`, since 2026-10-10): Saturdays an AI reads the week's owner
  messages and events beside the facts and proposes what's missing (up to 6) and what's out of date (up to 4).
  Only proposals the code can check reach the owner (a new fact's quote must be in the week's messages or events;
  a retire must name an active fact). Nothing changes until the owner answers: DeskAI's checklist
  (`GET/POST /api/memory/review`) or chat (`memory_check`, offered only while a check waits). Kept facts are
  saved with source `review`; retired ones fade.
- **Second opinion before deleting:** a delete the memory decides on by itself (an outdated fact, a fact picked
  for a forget, a near-duplicate merged or not saved again) goes ahead only if a second AI agrees
  (`MemoryManager.VERIFY_MODELS`: Groq, Cloudflare, Mistral; not the Gemini models that proposed it). If it
  disagrees, or doesn't answer, the memory is kept, and the owner is told when a delete or a requested forget
  was held. The owner's own deletes in DeskAI aren't checked.
- **Quality** is measured on real conversations with `scripts/eval_memory.py` (cases in `data\evals\`, not
  in git): fact extraction and search.
- **Observation RTC (`core/observation_sanitizer.py`):** tool outputs (emails, web pages, task summaries, logs)
  are compressed before turn insertion, stripping boilerplate and query noise to save 50%–70% tokens.
- **What's sent to the model:** only the memories relevant to each message.
- **Secrets** are redacted before anything is stored.
- **One conversation** is shared by DeskAI, the HUD and background tasks. Tasks (AgentWork jobs too) get the
  facts Alex stated that relate to them, any kind, labelled as context and not a conclusion to reach; not the prose
  profile or the conversation (`task_context`, 2026-11-03: "a personal assistant for me and no one else"; the line
  is bias, not privacy).
- **Prompt layout:** built so the provider can cache most of it (the persona, then the tools, then the
  history, then a context note), which keeps the free-tier token use down. The persona
  (`ASSISTANT_PERSONA`) is the assistant's picture of itself. Update it when abilities change, or the
  assistant will describe itself wrongly.

## The assistant's tools

In chat the model calls tools itself: lookups (status, tasks, diff preview, logs, memory, its own code, the web) and actions
(videos, TikTok posts, AgentWork jobs, self-updates, cancelling). See [TOOLS.md](TOOLS.md).

## Upkeep (in the daemon)

| When | What |
|---|---|
| Every 20 heartbeats (~10 min), when no task is running | SQLite checkpoint; TiktokVideos cleanup (temp files older than an hour, task thumbnails backfilled first (`core/task_thumbs.py`), then old videos beyond `VIDEO_KEEP_COUNT`); log housekeeping (logs > 30 days removed, preserving `affirmations.log`); workspace housekeeping (`prune_old_workspaces` cleanly discards AgentWork task worktrees > 7 days); memory upkeep (embed, dedupe, fade, rebuild the profile) |
| Daily at `BACKUP_HOUR` (3:00), or on the next heartbeat if the PC was off | Memory backup: `data\backups\` plus `BACKUP_MIRROR_DIR` (Google Drive). The Drive copy is then restored to a scratch file and its messages counted; a problem sends an alert. |
| Every 5 min (`core/heartbeat_ping.py`), live agent only | Checks the local API, then pings `HEALTHCHECK_URL` (healthchecks.io), or `<url>/fail` if the API doesn't answer. When the pings stop, healthchecks.io emails the owner. |
| Every 30 min (`MAIL_SYNC_MINUTES`) | Gmail into the subscription database (Connectors); a newly found paid subscription is pushed |
| Every 30 min (`PROACTIVE_EVERY_MINUTES`), outside quiet hours | The agent reaching out first: the morning brief, notices, suggestions and questions to get to know the owner, under a daily cap ([PROACTIVE.md](PROACTIVE.md)) |

**Alerts** (`notify_owner` in `core/config.py`) are push notifications: failed backups, stuck tasks, and
safety-check failures.

## Settings (`.env`)

| Key | Meaning |
|---|---|
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `GROQ_API_KEY`, `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN`, `MISTRAL_API_KEY` | the free AI providers ([AI_MODELS.md](AI_MODELS.md)) |
| `TAVILY_API_KEY`, `SERPER_API_KEY`, `BRAVE_SEARCH_API_KEY` | web search providers, tried in that order before DuckDuckGo and Wikipedia |
| `HEALTHCHECK_URL` | the healthchecks.io ping URL (secret; empty = no external heartbeat) |
| `MEMORY_EMBED_MODEL` | memory search model (default `qwen3-embedding:0.6b` through Ollama; `bge` = in-process bge-small) |
| `HUD_AUTH_TOKEN` | the API password. Without it, only the PC itself may connect. |
| `HUD_HOST`, `HUD_PORT` | default `0.0.0.0`, `8080` |
| `BACKUP_HOUR`, `BACKUP_KEEP`, `BACKUP_MIRROR_DIR` | nightly backups (3, 14, `G:\My Drive\AlwaysOnAgent Backups`) |
| `FCM_SERVICE_ACCOUNT` | optional path to the Firebase key (default `data\secrets\fcm-service-account.json`) |
| `AGENT_WORK_DIR`, `TIKTOK_VIDEOS_DIR`, `AGENT_GALLERY_DIR`, `IMAGEGEN_DIR`, `SOCIALPOSTENGINE_DIR`, `VIBESTATS_DIR`, `CONNECTORS_DIR` | companion tool folders (default: under `C:\Projects\`) |
| `AGY_PATH`, `FFMPEG_PATH`, `OLLAMA_HOST`, `OLLAMA_MODEL`, `OLLAMA_URL`, `VISION_MODEL` | optional overrides for media and local models |
| `DAEMON_INTERVAL`, `CHAT_TOOL_ROUTING`, `CHAT_TOOL_ROUTER` | heartbeat seconds (30); tool groups on (1, `0` sends every tool); the router model picking the groups (1, `0` uses keywords + meaning only; core tools always go, [TOOLS.md](TOOLS.md)) |
| `VIDEO_KEEP_COUNT` | finished videos TiktokVideos keeps (default 50); passed to it as `MAX_SAVED_VIDEOS` and `cleanup --keep` |
| `TASK_THUMBS_DIR`, `TASK_THUMBS_KEEP` | task thumbnails that outlive pruned videos (default `data\task_thumbs`, newest 200) |

The other video settings (voices, scene) are in TiktokVideos' own `.env`. A `MAX_SAVED_VIDEOS` line there still
overrides `VIDEO_KEEP_COUNT` (the tool reads its own `.env` first), so leave it out.

## Code map

```
supervisor.py            starts the four threads; start-up banner with the LAN/Tailscale addresses
core/
  daemon.py              the heartbeat, dispatcher, media engine, watchdog, safety check, upkeep, backups
  task_engines.py        Antigravity, cloud, Ollama and AgentWork engines
  tiktok_videos.py       runs TiktokVideos and reads its JSON events
  imagegen.py            runs ImageGen (the /image requests) and reads its result
  actions.py             the task builders shared by the API and the assistant's action tools
  assistant_tools.py     the 40 chat tools in groups, and the router that picks groups per message
  memory.py, embedder.py long-term memory; search models (Qwen through Ollama for memory, bge-small ONNX for routing)
  documents.py, vision.py chat attachments (text, OCR, thumbnails) and image scene notes (gemma3 in Ollama)
  connectors.py, weekly_report.py, social_posts.py  Gmail via Connectors; the weekly numbers; Pinterest via SocialPostEngine
  heartbeat_ping.py      the external heartbeat (healthchecks.io)
  observation_sanitizer.py observation RTC context compression for tool results
  self_update.py         autonomous self-update engine with task diff preview, dual test gating and rollback
  housekeeping.py        log pruning (LOG_KEEP) and safe task workspace worktree cleanup
  llm.py, llm_usage.py   the model routing and failover; per-call token log
  database.py            SQLite: tasks, state, logs, request keys
  push.py                Firebase push sender, device list, task watcher
  proactive.py           the agent reaching out first: brief, notices, suggestions, questions; the inbox
  canvas.py              drafts refined together: versions, the writer call, document/page previews
  schedules.py           schedules: the cron reader, the store and the runner
  backup.py, processes.py, sandbox.py, web_tools.py, config.py
interfaces/hud/          the API, one module per area:
  server.py              builds the app: media folders, access control, every module's routes
  state.py               shared state: database, memory, AgentWork, folders, token (tests swap these)
  auth.py                access control (loopback, or HUD_AUTH_TOKEN)
  models.py, common.py   request/response bodies; shared helpers (task cards, suggestions, restart)
  action_chips.py        contextual 1-tap interactive card actions (Diff, Apply, Play, Publish, Discuss)
  companion.py           health/status, suggestions, AgentWork, push, backup, cleanup, restart, the inbox
  canvas_api.py          the drafts' list, details and sandboxed previews
  tasks.py               the task queue, video jobs, daemon state, live stream, log
  media.py               videos and images for the DeskAI gallery
  memory_api.py          the assistant's memory
  schedules_api.py       the Schedules screen's endpoints
  chat.py                the chat: quick commands, /image via ImageGen, the assistant
scripts/                 run_forever.ps1, install_server_autostart.ps1, apply_update.py, apply_website.py,
                         restore_backup.py, eval_memory.py, eval_models.py, eval_injection.py, mini.py,
                         deploy_mini.py (deploy the agent), mini_pull_tools.py (update the tools),
                         run_in_session.ps1 + pull_task.ps1 (git in the Mini's desktop session), cache_probe.py
tests/                   self-contained tests (python -m pytest -q); conftest.py makes them use bge-small, never Ollama, and
                         blocks any real AI model call
```

## Schedules

Things the agent does by itself at set times, created in chat ("post a TikTok every day at 6pm", "remind me on
weekdays at 8:30") or in DeskAI's Schedules screen. Stored in the `schedules` table.

| Kind | What a run does |
|---|---|
| `video` | Queues a TiktokVideos render with a fresh quote; with `post_to_tiktok` it's posted when done |
| `pins` | Queues a SocialPostEngine run that posts the next `count` (1-5) ready-made pins to Pinterest |
| `report` | Queues the weekly numbers report (`core/weekly_report.py`); the week's row goes into the tracker in VibeStats' `GROWTH_PLAN.md` (pushed to GitHub). Then the **growth review** (`core/growth_review.py`): the numbers, the last 4 weeks, the growth plan and last week's suggestions become a short review (every number checked against the data), saved as a canvas draft and sent as one chat message with its card; at most 3 suggestions, never acted on by themselves. If the review fails, the plain report goes out as before |
| `reminder` | Pushes the reminder text to the owner's phone (channel `reminders`) |
| `task` | Queues a background instruction (the default engine, or an AgentWork job on its `project`) |

- **Times:** standard 5-field cron in the Mini's local time (`0 9 * * *` daily at 09:00, `30 8 * * 1-5`
  weekdays). `describe()` turns the common shapes into plain English.
- **Limits:**
  - At least 60 min between video or pin runs, 12 h for reports, 15 min for tasks, 5 min for reminders.
  - At most 50 schedules.
  - The chat assistant can create at most 2 per reply, and a posting schedule needs "TikTok" (or "Pinterest" /
    "pins") in the owner's own message.
- **Agent off at run time:** a missed run is made up if the agent is back within 2 hours; otherwise it's skipped,
  logged, and the owner gets one alert listing what was skipped (since 2026-11-01).
- **Standby:** video, pin and task runs are skipped; reminders still go out. **Run now** works even in standby.
- **Nightly stats:** separate from the schedules, the runner starts VibeStats' `collect` once a night from 23:50
  (Clarity exports only the last 1-3 days, so the week is built from nightly collections).
- **Memory:** every run and every change made in the app is recorded as an event, so the assistant knows what
  its schedules did.

## Tests

Run the full test suite with `python -m pytest -q` (read its exit code; the count changes, so it isn't written
here), or run focused suites like
`python -m pytest tests/test_bitemporal_memory.py tests/test_observation_sanitizer.py tests/test_action_tools.py`.
- **No real side effects:** tests use temporary databases and stand-ins for the AI and the tools.
- **The tool guards:** every test file points `TIKTOK_VIDEOS_DIR` and `IMAGEGEN_DIR` at folders where those
  tools aren't installed, so no test can render or post a real video or make a real image.
- **Changing a code path a test stubs out:** update the stub first. A stale stub once let a test run a real
  render.
