# The AlwaysOnAgent API

This is what DeskAI (and anything else) talks to. **This file is the source of truth.** A copy lives in the
DeskAI repo at `docs/API.md`, updated whenever this one changes.

## Basics

| | |
|---|---|
| Base URL | **`https://mini.tailnet.example`** over Tailscale, at home or away (2026-10-27: `tailscale serve` on the Mini, tailnet only, Let's Encrypt certificate): use it first. Fallbacks: `http://192.168.x.x:8080` on the home Wi-Fi and `http://100.x.x.x:8080` over Tailscale, both plain HTTP (the token crosses the Wi-Fi readable) |
| Auth | `X-HUD-Token: <HUD_AUTH_TOKEN>` on **every** request (`Authorization: Bearer <token>` also works). Send it only to this host. |
| No auth | `GET /api/health` only. `GET /api/status` needs the token since 2026-10-28 (it told anyone on the network the version, model, load and tasks); DeskAI already sends it. |
| On the Mini itself | No token needed from the agent's own tools: a request from 127.0.0.1 naming `127.0.0.1` or `localhost` as its `Host`, with no `Origin` and no proxy headers. Any other `Host` (a web page whose name was re-pointed at 127.0.0.1) needs the token. |
| Token in a URL | **Deprecated** (2026-10-07). `?token=<token>` still works so older DeskAI builds keep loading media, but a token in a URL leaks into logs, chat history and shared links. Load media with the header (Coil/OkHttp headers). For a link that must leave the app (external player, share), ask for a signed link: `POST /api/media/link`. The server hides `token=`/`sig=` values in its logs. |
| Errors | `401`: missing or wrong token. Otherwise `{"detail": "..."}` with a normal HTTP code. |
| JSON | `snake_case`; timestamps are ISO 8601 in the server's local time |
| CORS | none on purpose (it would let any website read the owner's memory). Native HTTP clients are unaffected. A request carrying a browser's `Origin` header needs the token even from the Mini itself, so a web page open in a browser there can't use the loopback trust (until 2026-10-04 chat, videos and images answered wildcard preflights). `tests/test_api_contract.py` checks that every route is in this file and every route here exists. |
| Who started a task | optional `X-Task-Source: <name>` (up to 80 characters) on task-creating requests (`POST /api/tasks`, `/api/tasks/run`, `/api/trigger_media`, `/api/videos/{name}/publish`, `/api/agentwork/jobs`, `/api/github/work`) becomes the task's `source`. Hark sends `Hark` (through `scripts/mini_mcp.py`); without it each route names its DeskAI screen. |
| Retries | send `Idempotency-Key: <uuid>` on task-creating requests: one UUID per user action, reused on every retry or failover. A repeat within 24 h returns the first `task_id` plus `"duplicate": true`. Accepted by `POST /api/tasks`, `/api/tasks/run`, `/api/trigger_media`, `/api/agentwork/jobs` and `/api/github/work`. |

## Chat

**`POST /api/chat`** `{"message": "...", "model": "auto", "engine": "auto", "conversation_id": null,
"attachments": [], "turn_id": "<the app's id for this message>"}` →

```json
{"success": true, "reply": "...", "model": "...", "agent_status": "idle|working|waiting_for_approval|error",
 "task_id": null, "proposals": [], "question": null}
```

- **Timing:** replies take several seconds (a model with tool calls), so use a read timeout of 90 s or more.
- **`turn_id` makes a resend safe** (2026-10-27): the same `turn_id` again (the app retrying on its other address)
  waits for the first run and returns its answer; the message is stored and answered once. Answers are kept 10
  minutes; a repeat that waits more than 330 s gets `409`. Send a new `turn_id` for every new message.
- **Passwords never reach a model:** a message holding one ("the password is …", "pw: …", "pin = …" followed by
  something password-like) is answered right away with `"model": "system"` and a pointer to the Vault, and stored
  only as "(a message holding a password, hidden)". Google and Zoho sign-in pastes are caught the same way.
- **No CORS headers** on any chat or media route (until 2026-10-27 `/api/chat`, refine and the media lists sent
  `Access-Control-Allow-Origin: *`).
- **One conversation and one memory** for every surface.
- **`task_id`:** set when the message started a task, e.g. "make me a video" or `/task …`. Show a live task
  card for it.
- **Commands** the agent handles itself:
  - `/status`;
  - `/task <instruction>` (queues a task);
  - `/cancel` (the running task);
  - `/restart`;
  - `/image <prompt>`, `/imagine <prompt>`, `/draw <prompt>`, which replies with markdown `![...](/images/gen_xxxx.jpg)` (supports `--style <anime|photo|3d|painting|pixel|cyberpunk|fantasy|retro|cinematic>`, `--raw`, and Gemini prompt enrichment). The separate ImageGen tool makes the image; load it with the token. `/image help` returns the guide.
- **`proposals`:** suggested actions, each shown as a card:
  ```json
  [{"id": "a1b2c3d4e5", "instruction": "...", "reason": "...", "project": null, "kind": "task"}]
  ```
  - `kind` is `task`, `add_project` (register a repo; label the button **Add project**), or a public post the
    owner asked for in chat: `tiktok` ("Post vibe_check_….mp4 to TikTok") or `pins` ("Post 2 pins to Pinterest").
    The chat never posts; the owner's tap on Run does. Apps that don't know `tiktok`/`pins` show them as a task
    card, which works the same.
  - `computer` ("Computer task: find the hours on the county website"): Run queues a task (engine `computer`) that
    AgentComputerUse does on the Mini's screen with the real mouse and keyboard. Its steps arrive as the task's
    progress (`message`: "Step 2: type - …"); the result is the answer with quotes from the page, or where it
    stopped (it never submits, sends, pays or posts by itself). Shown as a task card by apps that don't know it.
  - `email` ("Send an email to sam@example.com\nSubject: Late\n\n<the whole text>", or "Reply in Gmail …" / "Save a
    Gmail draft …") and `calendar_event` ("Add to Google Calendar: Dentist\nWhen: …"): the `instruction` is the
    exact email or event, shown in full. Run sends it (or saves the draft) or adds the event right away and returns
    `{"status": "success", "message": "Sent: 'Late' to sam@example.com."}` with no `task_id`; `502` with the reason
    if Google refused (nothing was sent). Suggested headers: **✉️ Send email** and **📅 Add event** (apps that don't
    know them show "Suggested Mission", which works the same).
  - `text` ("Text Jake Rivera (+1 217-555-0199)

<the message>") and `call` ("Call Dentist (217-555-0123)"):
    the phone does these, not the agent. The proposal carries `"phone": {"name", "number", "body"}` (`body` for a
    text only). `number` is `""` when the person isn't in Google Contacts: look `name` up in the phone's own
    contacts (ask which number when there are several; without the permission, open Messages with no recipient).
    On tap, open Messages (`smsto:<number>` with the message filled in) or the dialer (`tel:<number>`);
    the owner sends or calls. Then `POST /api/proposals/{id}/run`, which only records that it was opened and returns
    `{"status": "success", "message": "Opened a text to Jake Rivera."}`. Suggested headers: **💬 Text** and
    **📞 Call**.
  - **Run:** `POST /api/proposals/{id}/run` returns `{"status": "success", "task_id": "..."}`. For
    `add_project` it returns `{"status": "success", "project": "...", "message": "..."}` with no `task_id`,
    or 400 with the reason.
  - **Dismiss:** `POST /api/proposals/{id}/dismiss` returns `{"status": "ok"}`, and the assistant remembers it.
  - **404:** the suggestion expired because the agent restarted.
- **`question`:** one multiple-choice question when a detail is missing:
  ```json
  {"id": "9f2c1a7b3d", "text": "Which project?", "options": ["alwaysonagent", "sandbox-site"], "allow_other": true}
  ```
  - Show the options as buttons.
  - A tap sends that option's text as the next ordinary chat message. **Other…** focuses the text box.
  - A reply never has both a `question` and `proposals`.
- **Stop:** `POST /api/chat/cancel` `{"turn_id": "..."}` returns `{"ok": true}`. Send it when the user stops a
  reply. The agent finishes the turn anyway but doesn't keep the reply or learn from it, and that `/api/chat`
  call returns `{"success": false, "reply": "(cancelled)"}`. Also abort the HTTP request.
- **Refine Prompt:** `POST /api/chat/refine` (or alias `POST /api/chat/enhance`) `{"text": "...", "instruction": "..."}` → `{"success": true, "refined": "...", "original": "...", "model": "..."}`. Uses CloudLLM (background routes, never Flash) to clarify, polish, and structure a rough user prompt before sending. A draft holding a Google sign-in address, Zoho details or a password comes back unchanged with `"model": "none"` (never sent to a model). Powers DeskAI's ✨ prompt enhance button in the chat input bar.

### Attaching documents

Upload each file first, then send its id with the message (up to 5 per message):

| Method & path | Body | Returns |
|---|---|---|
| `POST /api/chat/attachments` | `{"name": "notes.pdf", "data_b64": "<the file, base64>", "conversation_id"?}` | `{"status": "success", "attachment": {"id": "att-1a2b3c4d5e6f", "name", "kind": "pdf"\|"word"\|"text"\|"image"\|"video", "method": "text"\|"ocr"\|"mixed"\|"video", "chars", "pages"?, "confidence"?, "warnings": [...], "has_thumb": true, "thumb_url": "/api/chat/attachments/att-.../thumb", "thumb_b64": "data:image/jpeg;base64,...", "seconds", ...}, "preview": "first 300 characters"}`; `422` with the reason (unsupported type, empty, over 15 MB, can't be read); `400` bad base64 |
| `GET /api/chat/attachments?conversation_id=&limit=10` | | `{"attachments": [attachment]}`, newest first |
| `GET /api/chat/attachments/{id}/thumb` | | Visual thumbnail (`thumb.jpg`, JPEG) for images, videos (frame capture), PDFs (page 1 cover), and documents (badged card) |
| `GET /api/chat/attachments/{id}/file` | | The original uploaded attachment binary file |

- **Types:** PDF, Word `.docx`, text-like files (`.txt`, `.md`, `.csv`, `.json`, code…), images (`.png`, `.jpg`,
  `.webp`…), and videos (`.mp4`, `.mov`, `.webm`, `.avi`, `.mkv`, `.m4v`). Up to 15 MB.
- **Visual thumbnails & client perception:** Every uploaded attachment gets a 256×256 `thumb.jpg` and compact
  `thumb_b64` (data URI) in the response. Photos and videos generate frame thumbnails; PDFs extract page 1 covers;
  documents generate stylish badged preview cards. DeskAI renders thumbnails immediately without roundtrip delays.
- **Smart token-efficient AI vision downscaling:** Images attached in chat automatically generate an `ai_view.jpg`
  capped at 768px longest dimension (Lanczos downsampling, stripped of EXIF overhead, quality 85). This reduces
  raw 4MB–10MB phone camera uploads down to ~50KB, slashing vision token overhead and CPU memory consumption.
- **How the text is read:** documents directly; images and scanned PDF pages by OCR (Tesseract, on the agent's
  PC), which takes a few seconds and can misread words; videos by frame extraction. `method` says which; `warnings`
  flags an uncertain OCR reading or no text found. Show them on the file's chip.
- **In the chat:** `POST /api/chat` with `"attachments": ["att-…"]`. The message itself is required (send
  e.g. "What's in this file?" if the owner typed nothing). The history stores the message plus
  `[attached: name (id …)]`; the document's text goes to the model beside it, and the assistant can read further
  into it later in the conversation. `410` with `{"detail": "That attachment has expired … attach it again."}` if an id
  is unknown or expired (uploads are kept 30 days); a `404` from `/api/chat` would read as the route being missing.
  When every AI model fails, the reply is `success: false` with a plain "the AI services aren't answering" text in
  `reply`, or, if tools had already acted that turn, a list of what they did.
- **Images and videos also get a scene note** from a small vision model on the agent's PC (Ollama, `gemma3:4b`),
  in the background, about 40 s per item. A chat turn that carries an image waits up to 50 s for its note; without
  one the assistant says it can't see the image rather than guessing. Every 5 noted images, anything durable
  (things that repeat, or clearly matter) goes into long-term memory as facts with source `images`.

## Tasks

A **task object** (from `GET /api/tasks`, `GET /api/tasks/{id}`, and the stream's `tasks` / `active_task`):

| Field | Meaning |
|---|---|
| `id`, `title`, `prompt` | e.g. `task-041` |
| `phase` | `backlog`, `in_progress`, `completed`, `failed` |
| `cancelled` | `true` when the owner cancelled it (`phase` is then `failed`, `exit_code` 137) |
| `needs_input` | `true` when an AgentWork job ran but changed nothing, usually blocked or unsure (`phase` is then `completed`, `exit_code` 4). Its `output_summary` starts with "⚠️ Needs your input" and holds the job's explanation. Worth showing differently from a success |
| `status_text` | ready to show: `Waiting to start`, `Capturing frames: 42%, about 1 min 35 s left`, `Running`, `Completed`, `Needs your input`, `Cancelled`, `Failed: <reason>` |
| `progress` | while running, when the task reports it (videos do), otherwise `null` (show a spinner) |
| `result` | a finished video: `{"type": "video", "filename": "vibe_check_….mp4", "url": "/videos/vibe_check_….mp4"}`. Otherwise `null`: show `output_summary`. It's also `null` once the video has been pruned. |
| `engine` | `auto`, `antigravity`, `cloud`, `ollama`, `media_pipeline` (TiktokVideos), `agentwork` |
| `priority` | `low`, `medium`, `high`, `urgent` |
| `actions` | contextual 1-tap action chips: `[{"label", "action": "stream"|"post"|"chat"|"none", "url"|"command", "variant": "primary"|"secondary"|"danger"}]`. e.g. "Preview diff", "🚀 Push live", "⚡ Apply update", "Discuss Blockers", "Post to TikTok" |
| `source` | who or what started it, in words: `DeskAI quick dispatch`, `DeskAI chat`, `DeskAI chat (/task)`, `DeskAI suggestion`, `DeskAI Video Hub`, `DeskAI AgentWork`, `DeskAI Repos`, `DeskAI`, `Hark`, `schedule #1 'Twice Daily'` (or `… (run now)`), or `unknown` for tasks older than 2026-10-07 that didn't record one |
| `thumb_url` | `/api/tasks/{id}/thumb` for a task that made a video (a small JPEG, kept after the video is pruned), else `null` |
| `video_filename`, `video_url`, `video_kept` | a video task's file, its stream (`/videos/{name}`, the same one the Video Hub plays; `null` once pruned) and whether it's still kept (`null` for tasks without a video). When `video_kept` is false, show the thumbnail with "video no longer kept" and no play button |
| `activity_log` | a computer task's steps in plain words, oldest first, or `null`: `[{"t": "14:02:11", "step": 2, "text": "Opening Notepad", "level": "info"|"ok"|"warn"|"error"|"retask", "kind": "open"|"type"|"click"|"save"|"plan"|""}]` (at most 150; the last line is how it ended). Typed secrets are already hidden in the text. Show it as "What it did", warnings amber, errors red, retask lines violet; `kind` (since 2026-10-11; may be missing on older tasks) says what kind of step an `info` line is, for its color: opening something, typing, clicking or keys, saving a file, or the tool's housekeeping (plans, checks, waiting) |
| `created_at`, `started_at`, `completed_at`, `output_summary`, `last_error`, `exit_code`, `engine_used`, `retry_count`, `max_retries`, `worker_pid`, `metadata` | as named; ignore any others |

`progress`:

```json
{"stage": "capture", "label": "Capturing frames", "percent": 42, "detail": "Frame 480 of 1104",
 "eta_seconds": 95, "updated_at": "2026-09-28T20:06:02-05:00"}
```

- **Video stages:** `narration` → `capture` → `assemble`, then `post` ("Posting to TikTok") for TikTok jobs.
  `label` is display text; `detail` may be empty.
- **`percent`:** 0–99, based on time, so it can dip a point. Clamp it if you want it to only go up.
- **`eta_seconds`:** the time left as of `updated_at`. Count down locally between updates, which come about
  every 12 s while capturing and every 5 s otherwise, and show "almost done" rather than going negative.

### Action Chips (`actions`)
Each task object includes a dynamic `actions` array of contextual 1-tap buttons:
```json
[
  {"label": "🔍 Preview diff", "action": "chat", "command": "Show diff for task-052", "variant": "secondary"},
  {"label": "🚀 Push live", "action": "post", "url": "/api/tasks/task-052/apply", "variant": "primary"}
]
```
- **`action` types:**
  - `stream`: Opens media stream URL in video player (`url`: `/videos/...`).
  - `post`: Sends an authenticated HTTP POST request directly (`url`: `/api/tasks/{id}/apply`, `/api/videos/{name}/publish`, …). For anything but a TikTok publish, show the response's `message` (or, on an error, its `detail`) in the chat.
  - `chat`: Sends pre-formatted command/prompt into chat conversation (`command`: `Show diff for task-052`, etc.).
  - `none`: A status label with nothing to tap (`⏳ Going live…`, `✅ Live`).
- **`variant` styles:** `primary` (highlight/action), `secondary` (neutral/outline), `danger` (destructive/cancel).
- **Going live:** a finished `alwaysonagent` or `website` job shows `🔍 Preview diff` and its go-live chip (`⚡ Apply
  update` / `🚀 Push live`), which then becomes `⏳ Going live…`, then `✅ Live`, `↩️ Rolled back`, or `🔁 Try again`
  after a failed attempt that pushed nothing. The tap is the owner's go-ahead. A finished job on any other AgentWork
  project (e.g. one set up from the Repos screen) shows only `🔍 Preview diff`: its change stays on its branch.

| Method & path | Returns |
|---|---|
| `POST /api/tasks/{id}/apply` | `202 {"status": "applying", "task_id", "project", "message"}`: the go-live program has started; the outcome comes as a push notification. `409 {"detail": "<why not>"}` if it can't go live (not finished, changed nothing, already live, another go-live running, …). |

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/tasks?limit=50&phase=in_progress` | | array, newest first |
| `GET /api/tasks/{id}` | | one task, or 404. Cheap: polling every 2 s is fine (the fallback when `/api/events` is down). |
| `GET /api/tasks/{id}/thumb` | | the task's video thumbnail (JPEG, ~240 px wide, under 50 KB), made on the first ask while the video is still kept; **404** when the task has none. Load it with the token in a header. |
| `GET /api/events` | | **Live task events** (Server-Sent Events; see below). |
| `GET /api/activity` | | Background work now (the away shift, docs/archive/PROACTIVE_RESEARCH.md): `{"active": bool, "label", "step", "started_at", "finished_at" (epoch seconds or null), "found": int}`. DeskAI shows a pulsing icon while `active`; changes also arrive on `/api/events` as `event: activity` (below). |
| `POST /api/tasks` | `{"title", "prompt", "priority": "medium", "engine": "auto", "mode": "accept-edits", "timeout_seconds": 300, "source"?: "Hark"}` | `{"status", "task_id", "woke_daemon", "message"}`. Without `source` (or `X-Task-Source`) the task's source is `DeskAI quick dispatch` |
| `POST /api/tasks/run` | `{"command": "..."}` (new) or `{"task_id": "..."}` (re-run a failed one) | `{"status", "task_id", "message"}` |
| `POST /api/cancel/{id}` | | 200; 404 if unknown; **409** if it already finished |
| `POST /api/tasks/cancel` | `{}` (the running task) or `{"task_id": "..."}` | as above; `{"status": "idle"}` if nothing is running |
| `POST /api/retry/{id}` | | back to `backlog`; **409** while it's still running |

### Live task events (`GET /api/events`)

A Server-Sent Events stream (`text/event-stream`) that says the moment a task's status changes, so the app
doesn't have to poll. Same auth as everything else (`X-HUD-Token` or `Authorization: Bearer`). Each change is:

```
event: task
data: {"type":"started","task_id":"task-052","status":"started","phase":"in_progress","ts":1791432000.1}
```

| `type` / `status` | When | Extra fields |
|---|---|---|
| `created` (`status` is `queued`) | a task was added | `phase` |
| `queued` | a task went back to the queue (retry, restart recovery) | `phase` |
| `started` | the daemon picked it up | `phase` |
| `progress` | a running task reported progress | `percent`, `label`, `eta_seconds` (when known) |
| `done` | finished (`phase` `completed`; `exit_code` 4 = needs your input) | `phase`, `exit_code` |
| `failed` | failed or cancelled | `phase`, `exit_code`, `cancelled: true` when cancelled |

- The event is a nudge, not the whole task: fetch `GET /api/tasks/{id}` for the full task object.
- `: ping` comment lines come every 15 s so phones and routers keep a quiet connection open. The stream starts
  with `retry: 5000` and `: connected`.
- At most 20 streams at once (`503` beyond that). A client that stops reading (its 100-event backlog fills) is
  dropped and its stream ends: reconnect, then refresh what's on screen over plain HTTP.
- Only changes made inside the agent's own process are sent (everything the API and the daemon do). Missed
  events (while disconnected) are not replayed: after a reconnect, refresh the tasks on screen once.
- When the stream fails, fall back to polling `GET /api/tasks/{id}` and retry the stream with backoff. Push
  notifications (FCM) still cover the app being closed.

**Background work (`event: activity`).** The same stream also carries what the agent is doing in the background
(the away shift), for the app's pulsing icon. Older builds ignore it: they act only on `event: task`.

```
event: activity
data: {"state":"started","active":true,"label":"Researching in the background","step":"","started_at":1791432000.0,"finished_at":null,"found":0,"ts":1791432000.0}
```

`state` is `started`, `step` (a new `step` text) or `finished` (`active` false, `found` = what the session turned up).
Every event carries the whole state, the same as `GET /api/activity`.

**Presence.** While a stream is connected the agent counts the app as open (`app_last_seen` in `system_state` is
the last time one was). The away shift starts after 2 hours without one, so DeskAI should keep holding the stream
only while it's on screen, as it does.

## Videos (TiktokVideos)

| Method & path | Body | Returns |
|---|---|---|
| `POST /api/trigger_media` | `{"action": "video" or "tiktok", "quote": null, "scene": null}` | `{"status", "task_id", "woke_daemon", "message"}`. With no quote, a fresh one is written. `tiktok` renders then posts. `scene`: `"chest"`, `"bottle"` or `"card"` (long names too: `treasure_chest`, `message_bottle`, `card_flip`); omitted, TiktokVideos takes turns between the chest and the bottle. An unknown scene is `400` and nothing is queued. |
| `POST /api/videos/{filename}/publish` | | `{"status": "success", "task_id": "..."}`: posts an existing video to TikTok (about a minute). The task's result says whether it worked. |
| `GET /api/videos` | | `{"videos": [{"filename", "size_mb", "created_at", "url", "thumbnail_url"}]}`, newest first |
| `GET /api/videos/{filename}/thumbnail` | | JPEG thumbnail (360px wide, cached). Generated on-demand via FFmpeg if missing. |
| `GET /videos/{filename}` | | the MP4, with byte-range support. `/videos/{name}.jpg` is its cover. |
| `GET /images/{filename}` | | images made by `/image` |
| `POST /api/media/link` | `{"url": "/videos/x.mp4", "ttl_seconds": 86400}` | `{"url": "/videos/x.mp4?exp=...&sig=...", "expires_at", "signed": true}`: a short-lived link (1 minute to 7 days, default 24 h) to one media file (`/videos/…`, `/images/…`, `/api/videos/{filename}/thumbnail`, `/api/tasks/{id}/thumb`) that works without the token, for an external player or a shared link. HMAC-SHA256 with a key derived from `HUD_AUTH_TOKEN` (nothing new to set; changing the token voids every link). `400` for any other path. Without `HUD_AUTH_TOKEN`: the plain path, `"signed": false`. |

## Memory

| Method & path | Notes |
|---|---|
| `GET /api/memory` | stats, profile, facts, past, open_loops, summaries, categories; add `?query=` to search. `facts` is what the agent believes now (status `active`); `past` holds replaced (`superseded`, with `superseded_by`) and `faded` facts, newest first: show them apart, never as current. `open_loops`: current facts waiting on something, checked daily (`core/open_loops.py`): `[{"fact_id", "content", "waiting_on", "check_after", "checked_at", "asked_at"}]`. `profile` is a few paragraphs of plain prose about the owner, by name (show it in full); facts are written with the owner's name too. Each fact also says where it came from: `source` (`chat`, `hud` = added in the app, `images`), `evidence` (the owner's own words backing it, or null), and `source_message` (`{"id", "at", "excerpt"}`, when the message is known) |
| `GET /api/memory/search?q=...` | matching facts and archived messages |
| `GET /api/memory/conversation?limit=50&before_id=` | chat history, oldest first |
| `POST /api/memory/facts` | `{"content", "category": "fact", "importance": 5, "pinned": true}`. Categories: profile, preference, project, plan, person, instruction, fact. |
| `PATCH /api/memory/facts/{id}` | any of `content`, `category`, `importance` (1–5), `pinned`, `status` (`active`/`faded`) |
| `DELETE /api/memory/facts/{id}?erase=true` | forgets it everywhere, including old messages |
| `GET /api/memory/procedures?status=` | learned recipes ("when X, do Y"): `{"procedures": [{"id", "area", "when", "do", "source", "source_ref", "evidence", "status", "pinned", "uses", "helped", "failed", "created_at", "updated_at", "last_used_at"}]}`. `source`: `owner` (taught in chat or added in the app), `seed`, `tool` (a chat tool that failed then worked), `task` (a task that succeeded on retry). `status`: `active` or `retired` (a learned recipe that kept failing). Show `evidence` as where it came from, and `uses`/`helped`/`failed` as its track record |
| `POST /api/memory/procedures` | `{"area": "tiktok videos", "when": "...", "do": "...", "pinned": false}`: an owner recipe (trusted, never retired by itself) |
| `PATCH /api/memory/procedures/{id}` | any of `area`, `when`, `do`, `pinned`, `status` (`active`/`retired`) |
| `DELETE /api/memory/procedures/{id}` | deletes a recipe |
| `POST /api/memory/profile/rebuild` | rebuilds the owner profile now |
| `GET /api/memory/review` | the weekly memory check waiting for the owner (Saturdays; `core/memory_review.py`), or `{"pending": null}`: `{"pending": {"created", "message_id", "items": [{"n", "op": "add"\|"retire", "content", "category" (add), "quote" (add: the owner's words or the event it came from), "fact_id" (retire), "reason" (retire)}]}}`. It arrives as an inbox message of kind `memory`; show it as a checklist, every item ticked to start with |
| `POST /api/memory/review` | `{"keep": [1, 3]}`: saves the kept new facts, retires the kept out-of-date ones (they fade), drops the rest; `[]` keeps nothing. 400 when no check is waiting or a number isn't in it. Answering in chat ("keep 1 and 3") does the same |
| `GET /api/memory/usage` | AI token usage by purpose over the last day |
| `GET /api/memory/budget` | today's calls and tokens per model, with countdowns to Google (Pacific) and Cloudflare (UTC) daily resets; for each Gemini model, `since_reset` and `learned_daily_limit`; `chat_health`: the last 24 h of chat replies per model, the share answered by the best model, and failures per model and kind |

## AgentWork (project jobs)

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/agentwork/projects` | | `[{"name", "description", "repo"}]` |
| `POST /api/agentwork/projects` | `{"name": "recipe-app", "repo": "https://github.com/o/r.git", "description": "..."}` | `{"status", "project": {...}}`, or 400 (bad name, not https, a duplicate, unreachable). It checks the repo first, which takes a few seconds. |
| `POST /api/agentwork/jobs` | `{"project": "...", "instruction": "at least 8 characters"}` | `{"status": "success", "task_id": "..."}`; 400 for an unknown project |

### Repos (the GitHub repo browser)

The owner's GitHub repos (both accounts), browsed like a file explorer, read-only, through the Mini's own GitHub
sign-in. **Work on this** starts an AgentWork job on a repo; a repo that isn't an AgentWork project yet is
registered first, on its default branch, so the owner never adds projects by hand. Each call takes 1-3 s (it
asks GitHub); the repo list is cached for 2 minutes (`?refresh=true` skips that). GitHub errors come back as `502`
with the reason in `detail`.

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/github/repos` | | `{"repos": [{"repo": "owner/name", "description", "private", "updated": "2026-10-06T21:51", "default_branch", "url", "project": "website" or null}]}`, newest change first. `project` is the AgentWork project already set up for it. |
| `GET /api/github/tree?repo=owner/name&path=docs` | | `{"repo", "path", "entries": [{"name", "path", "type": "dir"\|"file", "size"}]}`, folders first, then files, A-Z. `path` empty = the repo's top. |
| `GET /api/github/file?repo=owner/name&path=docs/a.md` | | `{"repo", "path", "name", "size", "url" (on GitHub), "text": "...", "note": null}`; for a file over 300 KB or not text, `text` is null and `note` says why (show it with the GitHub link) |
| `GET /api/github/download?repo=owner/name&path=core&format=zip` | | **Share** (2026-10-10): the file itself, to hand to Android's share menu. A file path always comes back as the file; a folder (or `path` empty: the whole repo) as `format=zip` (the folder's name is the zip's top folder) or `format=text` (every text file in one `.txt`, each under a `===== path =====` line: for AI chats that can't open a zip). The name is in `Content-Disposition`; `X-Files` is how many went in and `X-Left-Out` how many were left out (over 5 MB each, 400 files or 40 MB in all; binaries in `text`), listed inside it. Can take a minute for a big folder |
| `POST /api/github/work` | `{"repo": "owner/name", "instruction": "at least 8 characters", "path"?: "the folder or file being viewed"}` | `{"status": "success", "task_id", "project", "registered": true if it was just set up}`; 400 (short instruction, or the repo couldn't be set up), 404 (not one of the owner's repos). Accepts `Idempotency-Key`. The task appears like any AgentWork job (its card, Preview diff, go-live). |

## Media and Gallery

Generated images are stored outside the agent repository in the dedicated gallery directory (`AGENT_GALLERY_DIR/images`), while 3D videos are rendered by TiktokVideos (`TIKTOK_VIDEOS_DIR/videos`, documented under [Videos](#videos-tiktokvideos)).

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/images` | | `{"images": [{"filename", "size_mb", "created_at", "mtime", "url"}]}`: lists all generated artwork |
| `DELETE /api/images/{filename}` | | `{"status": "success", "message": "Deleted <filename>"}`: removes an image from disk |
| `GET /images/{filename}` | | Static image file. Send the token in a header; `?token=` (deprecated) and a signed link (`POST /api/media/link`) also work |
| `POST /api/images/jobs` | `{"message": "/image <prompt> [flags]", "turn_id"?}` | Starts making the image in the background and answers at once: `{"job_id", "status": "running"\|"done"\|"failed", "reply", "model", "stage", "seconds"}`. `stage` is what ImageGen says it is doing right now, from the services' own live state (`"AI Horde: 3rd in line"`, `"AI Horde is drawing it now"`, `"Trying Pollinations (sana)"`); show it so a slow image doesn't look stuck. `reply` is what `/image` in the chat would answer (the image as markdown), `null` while running. `/image help` comes back `done`. The same `turn_id` sent again returns that job instead of making a second image (the id becomes `job_id`). `400` if the message isn't an image request; `429` when 3 images are already being made |
| `GET /api/images/jobs/{job_id}` | | The job, as above. Kept an hour after it finishes; `404` for an unknown job (the agent restarted). DeskAI asks every few seconds and shows the image in the chat when it's `done` (2026-10-27: a `/image` chat request held open ~3 minutes was dropped on the way) |

## Agent controls and maintenance

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/status` | | `{"status": "online", "version", "uptime_seconds", "cpu_percent", "memory_percent", "active_model", "active_tasks_count"}`, with the token (DeskAI's failover ping sends it; `active_model` is the default task engine) |
| `GET /api/health` | | `{"status": "online", ...}`, no auth |
| `POST /api/daemon/state` | `{"status": "wake"}` or `{"status": "standby"}` | `{"status", "daemon_status", "message"}` |
| `POST /api/engine` | `{"engine": "auto"}` (auto, antigravity, cloud, ollama) | `{"status", "default_engine"}` |
| `POST /api/backup` | | `{"ok", "message"}`: a memory backup now (a few seconds) |
| `POST /api/cleanup` | | `{"message"}`: TiktokVideos' temp files and old videos (beyond the newest `VIDEO_KEEP_COUNT`, default 50; task thumbnails are kept), stale logs (> 30 days, preserving `affirmations.log`), and abandoned task workspaces (> 7 days, via AgentWork worktree discard) |
| `POST /api/restart` | `?now=true` (optional) | `202 {"status": "restarting", "message"}`: back in about 3 s. Poll `/api/status`. While a task runs: `202 {"status": "draining", "waiting_for": [titles], "message"}`, and the agent restarts when the task finishes (at most 30 min), starting nothing new meanwhile. `now=true` restarts at once. |
| `GET /api/logs?limit=100` | | `[{"id", "timestamp", "level", "message"}]`, newest last |
| `GET /api/stream` | | Server-Sent Events, one `data: {json}` per second: `timestamp`, `stats`, `active_task`, `tasks` (30 newest), `blockers`, `new_logs`, `daemon_status`, `overall_phase`, `last_heartbeat`, `default_engine` |

## Schedules

Videos, Pinterest pins, the weekly report, phone reminders and background tasks that run by themselves (see AGENT.md, Schedules). Times are 5-field
cron in the server's local time.

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/schedules` | | `{"schedules": [schedule], "timezone": "Central Daylight Time", "kinds": [...], "min_gap_minutes": {"video": 60, "pins": 60, "report": 720, "task": 15, "reminder": 5}}` |
| `GET /api/schedules/preview?cron=0 9 * * 1-5&kind=video` | | `{"valid": true, "description": "weekdays at 09:00", "next_runs": [iso, iso, iso]}`, or `{"valid": false, "error": "..."}` |
| `POST /api/schedules` | `{"name", "kind": "video"\|"pins"\|"report"\|"reminder"\|"task", "cron", "enabled"?, "post_to_tiktok"?, "quote"?, "count"? (pins, 1-5), "text"? (reminder), "instruction"? + "project"? (task)}` | `{"status": "success", "schedule": schedule}`; `400` with the reason if the cron is invalid or too frequent. Accepts `Idempotency-Key`. |
| `PATCH /api/schedules/{id}` | any of the create fields except `kind` | `{"status": "success", "schedule": schedule}`. Changing `cron`, or turning it back on, re-plans the next run. |
| `DELETE /api/schedules/{id}` | | `{"status": "success", "id": id}` |
| `POST /api/schedules/{id}/run` | | Runs it once now, even in standby: `{"status": "success", "result": "started task-051", "task_id": "task-051", "ran": true}`. The next run doesn't change. |

A `schedule`: `id`, `name`, `kind`, `cron`, `description` (plain English), `enabled`, `next_run_at`, `last_run_at`,
`last_result`, `runs`, `post_to_tiktok`, `quote`, `text`, `instruction`, `project`, `count`, `created_at`, `source`.

A `pins` schedule posts the next `count` of the site's ready-made pins to Pinterest (SocialPostEngine), each run a
task with the usual live card (engine `social_posts`). Its `instruction` is filled in for display ("Post 2 pins to
Pinterest") and can't be set; the app can list it like a task until it has its own icon.

A `report` schedule puts together the weekly numbers (site visits by source, cards sent, daily-email subscribers,
TikTok views and followers, Pinterest impressions and clicks) as a task (engine `weekly_report`): the owner gets the
usual "task finished" push and reads the report as the task's result. Its `instruction` is also filled in for
display.

## Push notifications (Firebase Cloud Messaging)

| Method & path | Body | Returns |
|---|---|---|
| `POST /api/push/register` | `{"token": "<fcm token>", "device_name": "..."}` | `{"status": "ok", "push_ready": true}`. It's idempotent, so call it on every connect. |
| `POST /api/push/unregister` | `{"token": "..."}` | `{"status": "ok"}` |
| `POST /api/push/test` | | `{"status": "sent", "devices": 1}`, `{"status": "no_devices"}`, or `{"status": "not_configured", ...}` |

Messages are **data-only and high priority**, and the app builds the notification. The `data` map (all
strings):
- `type`: `task_completed`, `task_failed`, `alert`, `reminder`, `agent_message`, `needs_you` or `test`;
- `task_id`;
- `title` and `body`, both short. They pass through Google's servers, so they never carry memory or chat
  content. The one exception is a reminder's body: the text the owner wrote for that reminder.
- `channel`: `tasks`, `alerts`, `reminders`, `assistant` or `needs_you`. Apps older than 2.3.9 show reminders on
  the alerts channel; apps that don't know `needs_you` show it on the alerts channel.
- `agent_message` only: `message_id` and `kind` (see "Messages the agent starts").
- `needs_you` only: `request_id`, `request_kind` (`approve`, `choice`, `text` or `secret`), `choices` (a JSON
  list, as a string), `screenshot` (a path on this API, empty when there's none), `url` (the page's address), and
  `reminder` ("1", "2", …) on reminders. See "Computer tasks: Needs you".
- `needs_you_cancel` (channel `needs_you`): `request_id` (also `cancel_request_id`) and `reason`. The request was
  answered elsewhere, or its task ended: remove its notification.

**When pushes are sent:**
- `task_completed` for every finished task. One that needs the owner's input (`needs_input`) has the title
  "⚠️ Needs your input" instead of "✅ Task finished".
- `task_failed` for every failure except cancels.
- `alert` for backup failures, stuck tasks and safety-check failures.
- `reminder` when a reminder schedule runs.
- `agent_message` when the agent starts a message (below). The push has no text of its own, only what kind of
  message is waiting.
- `needs_you` when a computer task waits for the owner, then reminders (after 1, 2, 3, 4 and 5 minutes, then
  every 10) until it's answered; the task pauses safely after 30 minutes. It should be a loud notification.

Tokens that FCM rejects are removed automatically.

## Computer tasks: Needs you

A computer task (AgentComputerUse, proposal kind `computer`) never submits, sends, pays or posts by itself, and
some questions only the owner can answer. Then it **waits** and the owner gets a `needs_you` push. The task
card's progress says `Needs you: …` with the `request_id`.

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/computer/requests` | | `{"requests": [{"id", "kind": "approve"\|"choice"\|"text"\|"secret", "question", "choices": [...], "url", "screenshot", "created", "pending": true}]}`, newest first |
| `GET /api/computer/requests/{id}/screenshot` | | PNG: the window as it was, the spot in question outlined in red; `404` without one |
| `POST /api/computer/requests/{id}/answer` | `{"answer": "Approve"}` | `{"status": "ok", "id", "kind", "answer"}`. `approve`: `Approve` or `Deny`; `choice`: one of `choices` exactly; `text`: up to 1,000 characters; `secret`: up to 200, kept exactly as typed, and the response's `answer` is `(sent)`. `400` for an answer that doesn't fit, `409` when it was already answered or has closed |
| `GET /api/computer/retasks` | | `{"retasks": [{"id", "goal", "kind": "task"\|"info", "steps", "uses", "fails", "created", "updated", "last_used"}]}`, newest first. A retask is a clean computer run saved so the SAME task (same wording) asked again is repeated without the AI: answers are always read fresh (`info`: repeated up to the reading point, then one request) and written text is always written fresh. A finished task that was a repeat says "(Repeated an earlier run: N AI requests.)". `503` when the tool can't be reached |
| `POST /api/computer/retasks/{id}/run` | | `{"status": "ok", "task_id", "goal"}`: runs the retask now (DeskAI's `/retask`): a computer task with its exact wording is queued, no proposal card. `404` when there's no such retask, `503` when the tool can't be reached |
| `DELETE /api/computer/retasks/{id}` | | `{"status": "ok"}`; `404` when there's no such retask, `400` for a bad id |
| `GET /api/computer/credentials` | | `{"credentials": [{"domain": "google.com", "username": "alice@gmail.com"}]}`: credentials saved in the host's Windows Credential Manager vault via DPAPI. Passwords are never returned over the wire. `503` when the tool can't be reached |
| `POST /api/computer/credentials` | `{"domain": "google.com", "username": "alice@gmail.com", "password": "…"}` | `{"status": "ok", "saved": "google.com", "username": "alice@gmail.com"}`: securely stores the credential for automated sign-ins |
| `DELETE /api/computer/credentials?domain=google.com&username=alice@gmail.com` | | `{"status": "ok"}`; `404` when not found |
| `GET /api/files/m/{ref}/{name}` | | A file on the Mini that the chat's `mini_files` tool showed (2026-10-27): an image, video, audio file or PDF, with Range support. `ref` is the path, URL-safe base64, checked again here (inside the allowed folders, never secrets or app data). Replies show images as `![name](/api/files/m/…)` and video/audio/PDF as `[▶ name (12.3 MB)](/api/files/m/…)` (🔊 audio, 📄 PDF): the app plays or opens those. `404` for anything not allowed |
| `GET /api/files/m/{ref}/{name}/thumbnail` | | A JPEG still (360 px wide) of a video the chat showed, for its card (2026-10-27). A video in the TikTok folder shares the videos hub's cached thumbnail, so it's the same picture. `404` when it isn't a video or no frame could be taken |
| `GET /api/computer/runs/{run_id}/screenshots/{name}` | | One step's screenshot from a computer run (`step-NN.jpg` / `snap-NNN.jpg`, JPEG). The chat's `computer_screenshots` tool puts these as markdown images in a reply (2026-10-27); load them with the token header like any image. `404` for an unknown run or file |


- `approve`: a step that submits, sends, pays, buys, deletes, posts or signs up. Approve: it's clicked and the
  task carries on; Deny: the task stops with nothing done.
- `choice`: a pop-up without a harmless answer; the owner picks one of its own buttons.
- `text`: a question (a detail it doesn't know, or "I'm stuck, what should I do?"); the answer becomes part of
  the task.
- `secret`: a password or verification code for the box outlined on the screenshot. The tool types the answer
  straight into that box; it never goes to the AI, a log or memory. The app answers it only on its own screen,
  in a hidden (password) field, never in a notification reply box, and blocks screenshots while it's shown.
- On a sign-in page, a `choice` can offer `Type my password` next to the page's own options (`Try another way`
  and the like). Picking `Type my password` brings a `secret` request for the password box.
- Show the screenshot above the answer, and the page's address in words.

### Take over

The owner uses the Mini from the phone: a live picture of the screen, and their taps and typing replayed with
the real mouse and keyboard. Offered on every request (a CAPTCHA, anything unusual) and on its own. While the
owner is in control a running task waits; **Hand back** (`stop`) answers the request it was taken over for with
`__handed_back__`, and the task looks at the screen again. Control comes back by itself after 10 minutes with no
picture asked for and no input.

**Which computer:** the Mini by default, or the owner's laptop with `"machine": "laptop"` in a body (start,
input, stop) or `?machine=laptop` (status, frame). The laptop runs its own Take over from its login (AgentComputerUse
`takeover-serve`, Tailscale only) and the agent passes calls on to it; `503` when it isn't set up or isn't answering
(asleep, off, Tailscale off). Requests and hand-backs are Mini-only.

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/computer/takeover/machines` | | `{"machines": [{"id": "mini", "name": "Mini"}, {"id": "laptop", "name": "Laptop"}]}` (the laptop once it's set up) |
| `GET /api/computer/takeover` | | `{"active": bool, "since", "request_id"}` |
| `POST /api/computer/takeover/start` | `{"request_id": "…"}` or `{}` | `{"active": true, "request_id", "screen": [width, height]}`. `409` if that request already closed; `503` without the tool |
| `GET /api/computer/takeover/frame` | | JPEG of the screen (at most 1280 wide, the pointer ringed in yellow), header `X-Screen: 1920x1080`. `409` until started. Ask about every 0.6 s while the screen is shown. `?view=1` (DeskAI's "View only" box): the picture without starting, so nobody takes control and a running task carries on; don't call `start`, `input` or `stop` then |
| `GET /api/computer/takeover/activity` | `?machine=mini\|laptop` | What a computer-use run is doing, for the banner on Take over's screen (read-only, no control needed): `{"running": true, "goal", "step", "doing", "status", "log"}`, or `{"running": false}` when nothing runs. `log`: the run so far in plain words, the same shape as a task's `activity_log` (last 60 lines), for the narration on View only. A finished run shows for 2 minutes (`"running": false` with `status` and `doing`, e.g. "done: …"). Ask about every 1.5 s while the screen is shown |
| `POST /api/computer/takeover/input` | one input, below | `{"status": "ok", "done"}`. `409` until started, `400` for an input that doesn't fit |
| `POST /api/computer/takeover/stop` | `{"request_id": "…"}` or `{}` | `{"status": "ok", "active": false, "handed_back_to": "…" or null}` |

Inputs (`x`, `y`, `x2`, `y2` are 0 to 1 across and down the picture):
- `{"type": "tap", "x", "y"}`, `double_tap`, `long_press` (a right-click)
- `{"type": "drag", "x", "y", "x2", "y2"}`
- `{"type": "scroll", "dy": 5}` (positive is up; at most 15), with `x`, `y` to scroll at that spot
- `{"type": "text", "text": "…"}` (up to 500 characters)
- `{"type": "key", "key": "enter"}`: `enter`, `backspace`, `tab`, `esc`, `delete`, `space`, the arrows `up` `down`
  `left` `right`, `home`, `end`, `page_up`, `page_down`, and `ctrl+a/c/v/x/z/y/l/f/t/w/r`, `ctrl+shift+t`,
  `alt+tab`, `alt+left`, `alt+right`, `shift+tab`

## Messages the agent starts (proactive)

The agent reaches out by itself: a morning brief, notices, suggestions, and questions to get to know the owner
(design: [PROACTIVE.md](PROACTIVE.md)). Each one is announced by an `agent_message` push carrying `message_id`
and `kind`, but **not the text**. The app fetches the text here and shows it in the chat as an assistant
message, and the owner simply replies in the chat.

| Method & path | Returns |
|---|---|
| `GET /api/inbox?after=<id>&limit=50` | `{"messages": [{"id": 12, "kind": "brief"\|"notice"\|"suggestion"\|"question"\|"review"\|"memory", "text", "created_at", "why"?, "canvas"?}], "last_id": 12}`, oldest first, only ids greater than `after`. `why` (optional string): small text line explaining why the proactive message was sent (e.g. quote, signal). `canvas` (only when present) is a draft card, the same shape as a chat reply's `canvas` (`{"id", "title", "kind", "version"}`): show the card that opens it. The Sunday growth review comes as `kind: "review"` with one. `kind: "memory"` is the Saturday memory check: show a Review button that opens `GET /api/memory/review` |
| `POST /api/inbox/{id}/feedback` | `{"status": "ok", "id": 12, "reaction": "thumbs_up"\|"thumbs_down"\|"less_like_this"}`. Body: `{"reaction": "thumbs_up"\|"thumbs_down"\|"less_like_this", "reason"?}`. Records reactions (Pulse thumbs) to proactive messages, used in scoring and exclusions |
| `GET /api/proactive/candidates?status=&days=7&limit=50` | `{"candidates": [...], "counts": {"status", "source", "outcome"}}`. Inspects candidate pool items and statistics |

The app keeps the highest id it has shown and asks for newer ones when a push arrives and whenever it opens, so a
missed push loses nothing. Using `agent-<id>` as the message's id in the app's chat keeps a message from showing
twice. The settings (on/off, brief time, quiet hours, caps) are changed in chat; the agent uses its
`proactive_settings` tool.

## Today (editions)

DeskAI's Today screen (2026-10-28, `core/today.py`): an *edition* of a few cards put together from what the agent
already made. Interest findings with their page's title, site and preview image; new videos on followed YouTube
channels; what the open-loops check settled; tasks finished; and the best curious question still waiting. A new edition
is built when Today is opened at least 6 h after the last one and something is new; never on a clock, and no AI call.

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/today?edition=&refresh=` | | `{"header": {"date", "weather": {"temp_f", "summary", "place", "is_day"} or null, "brief": {"text", "at", "up_next": [{"time": "12:00", "what"}], "mail": [{"from", "subject"}]} or null, "location"}, "edition": {"id", "built_at", "date", "count", "next_after"} or null, "items": [item], "older": [{"id", "built_at"}], "recipe": recipe}`. Without `edition`: the latest, built first when it's due (`refresh=true` builds one now if anything is new). `edition: null` until there's something to show. The header is read at each call: the weather now at the town in the proactive settings (`location`, kept 30 min) and the day's brief (16 h), with the facts it was written from: what's still ahead today (`up_next`, past times dropped) and the important mail, for tiles that never depend on the brief's wording. The recipe's show/never/limit rules apply at each call too |
| `GET /api/today/saved` | | `{"items": [item]}`: saved cards, newest first |
| `POST /api/today/items/{id}/react` | `{"action": "seen"\|"save"\|"unsave"\|"less"\|"more"\|"dismiss"\|"answered"}` | `{"item": item}`. `seen` never undoes another state; `dismiss` hides the card; `answered` (a question card, after the answer was sent in the chat) marks the question answered. `400` for another action, `404` for an unknown card |
| `GET /api/today/items/{id}/image` | | The card's image (JPEG, at most 1080 px wide), kept on the agent's PC: send the token in a header. `404` when the card has none |
| `GET /api/today/recipe` | | The recipe: `{"text", "rules", "at"}` |
| `PUT /api/today/recipe` | `{"text": "More Illinois outdoors, no crypto, 8 cards max"}` | The recipe, compiled into rules by one AI call: `{"more": [topic], "less": [topic], "never": [topic], "channels": ["@handle"], "max_cards": 3-15 or null, "show": {"story", "video", "settled", "done", "question": bool}, "location": town or ""}`. Channels are followed (ones an earlier recipe added and this one dropped are unfollowed); a town sets the weather. `more` topics are searched while Alex is away, `never` ones are excluded there too. `503` when the AI didn't answer (nothing changes). The chat's `today_settings` tool edits the same recipe |

An item: `{"id", "card": "story"|"video"|"settled"|"done"|"question", "title", "body", "link", "site", "why",
"state": "new"|"seen"|"saved"|"less"|"more"|"dismissed"|"answered", "image_url": "/api/today/items/{id}/image" or
null, "created_at", "source_ref"}`. A story without an image (none, broken, or under 400 px wide) is a text card on
purpose. `done` lists one task title per line in `body`. Pages and images are other people's content: shown, never
followed as instructions.

The day's brief goes to Today's header by default (proactive setting `brief_to`: `today`, or `chat` for a
message as before): it isn't in `GET /api/inbox` and has no push.

## Canvas (drafts)

Drafts the owner and the agent refine together (design: [CANVAS.md](CANVAS.md)). When a chat reply wrote or changed
one, `/api/chat` returns `"canvas": {"id": 3, "title": "Grief cards guide", "kind": "document"|"page", "version": 2}`
and the app shows a card that opens the canvas screen.

| Method & path | Returns |
|---|---|
| `GET /api/canvas?limit=20` | `{"canvases": [{"id", "title", "kind", "version", "updated_at"}]}`, newest first |
| `GET /api/canvas/{id}?version=` | `{"id", "title", "kind", "version", "latest", "versions": [{"version", "note", "created_at"}], "created_at", "updated_at"}` (no content: that's the preview) |
| `GET /api/canvas/{id}/preview?version=` | the draft as a full HTML page: a document in a clean reading layout, a page with the website's own stylesheets. Sent with `Content-Security-Policy: default-src 'none'; script-src 'none'; … sandbox`, so nothing in it can run. Load it with the token in a header, not in the URL. |

## AI Research (Opportunity Radar)

Automated research worker that periodically scans the web for free AI credits, developer previews,
free tiers, and free AI hackathons fitting Alex's building stack, saving digests to Memory and Canvas.

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/research/opportunities` | | `{"latest": {"title", "doc_id", "at", "count", "summary"} or null, "last_run_at": "...", "total_digests": int, "history": [...]}` |
| `POST /api/research/run` | | `{"status": "success", "digest": {"doc": {"id", "title", "kind", "version"}, "summary", "count", "opportunities": {...}}}` |

## Connection check

`GET /` returns `{"status": "online", "message": "AlwaysOnAgent is running"}`, or `401` with a missing or wrong
token. DeskAI's connection test uses it. There are no web pages: the dashboard and the Memory page were removed
on 2026-09-29, since DeskAI shows everything they did.
