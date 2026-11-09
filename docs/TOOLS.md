# Tools: what the assistant can look up and do

The goal: the owner asks for anything in plain words in DeskAI, and the assistant *does* it. It doesn't
describe how the owner could do it. The chat model decides for itself which tools to call; the tool results go
back to it, and it answers from them.

Code: the schemas and handlers are in `core/assistant_tools/` (`AssistantTools`); the task builders shared
with the app's buttons are in `core/actions.py`.

## The tools (every tool in a group is listed here: `tests/test_tools_doc.py`)

### Actions: they start real work

| Tool | What it does | Rules |
|---|---|---|
| `render_video` | Queues a TiktokVideos render. With no quote, TiktokVideos writes one. `scene` (`chest` or `bottle`) only when the user named one; otherwise the scenes take turns. The finished card has a **🚀 Post to TikTok** chip | Never posts by itself |
| `post_video_to_tiktok` | Shows a card to post a finished video (`latest` or a file name from `list_videos`): a `tiktok` proposal | The owner's tap on Run posts it; the chat never does |
| `post_pins` | Shows a card to post the next 1-5 ready-made pins (a `pins` proposal). SocialPostEngine checks each board first and never posts a pin twice | The owner's tap on Run posts them; the chat never does |
| `make_pins`, `approve_pins`, `reject_pins` | The pin maker (SocialPostEngine `pinmaker.py`): new pins from the website's messages as drafts; the reply shows the numbered preview sheet (copied to the images folder). `make_pins` with `new: false` shows the drafts already waiting | Only `approve_pins` puts drafts in the posting queue; the schedule posts them |
| `weekly_report` | Queues the numbers report now (VibeStats + TiktokVideos + SocialPostEngine); the owner reads it in the task card | none (it only reads) |
| `my_numbers` | The agent's own week in chat from the self-check (`core/self_check.py`): the latest week with the one before beside it, the owner's quoted corrections, what its closing note says, and any fix waiting for his "start the fix" (stats group, 2026-11-03) | none (it only reads) |
| `start_project_job` | Queues an AgentWork job on a registered project | Always on a branch; nothing goes live without review. Asks which project if unclear |
| `offer_go_live` | When the owner wants a finished AgentWork change live ("push it to the live site", "apply it"), shows that job's card with its one-tap chip: **🚀 Push live** (website) or **⚡ Apply update** (the agent itself). The tap calls `POST /api/tasks/{id}/apply` (see "Going live" below) | It never puts anything live itself; only the owner's tap does |
| `cancel_task` | Stops a waiting or running task | Only when the owner's own message asks to stop or cancel (2026-10-28); a finished task can't be "cancelled" over its result |
| `computer_task` | Proposes a task in which AgentComputerUse uses the PC's real mouse and keyboard (Edge and apps): a card the owner taps to run (computer group) | Nothing runs until the tap; risky steps come to the phone as "Needs you"; never solves CAPTCHAs |
| `research_ai_opportunities` | Runs the AI-opportunities research worker now (free credits, developer previews, free tiers, hackathons); saves a digest to Canvas and an event to memory | none |
| `list_videos` | The finished videos, newest first | none |

The action tools can start at most **2 tasks per reply**, and none in a reply that asked the owner a question.
The first task started comes back as the chat reply's `task_id`, so DeskAI shows its live card.

### Schedules: things that happen by themselves later

| Tool | What it does | Rules |
|---|---|---|
| `create_schedule` | A video (optionally posted to TikTok), a phone reminder, or a background task on a cron schedule | Posting needs "TikTok" in the owner's message; at most 2 new per reply; minimum gaps per kind |
| `list_schedules` | Every schedule: what, when, on/off, next and last run | none |
| `change_schedule` | Pause, resume, retime, rename or delete one | Turning posting on needs "TikTok" in the owner's message; deleting needs the owner's own words (delete, remove, cancel…) |
| `today_settings` | DeskAI's Today screen: its recipe (the owner's words, turned into rules: more/less/never topics, hidden card kinds, a card limit, channels to follow) and the weather town (today group). No arguments shows the recipe | Refuses an addition past the recipe's 1,500 characters: offer to rewrite it shorter |
| `proactive_settings` | Shows or changes the agent's own proactive messages (the brief, notices, suggestions, questions, background research): on/off, brief time (default "auto": when he shows up), quiet hours (default off), daily cap (default 10), questions per day (default 4), town for weather, per-kind switches (open loops, curiosity, research sharing, patterns), curated interests/exclusions, and followed YouTube channels (`follow_youtube` / `unfollow_youtube`: new videos told from the channel's feed). See [PROACTIVE.md](PROACTIVE.md) | When the owner asks ("brief at 7", "no questions for a while", "follow Veritasium", "stop asking about Y") |
| `mini_files` | Files on the Mini, read only (`core/mini_files.py`, files group): `find` by name or `*` pattern (kind words like "videos" narrow it), `list` a folder, `read` a text file, `show` an image, video, audio file or PDF as a line the reply copies. Roots: the user folder and `C:\Projects` (`MINI_FILE_ROOTS`) | Never secrets (`.env`, keys, databases, names with secret/token/password), hidden (dot) folders and files (`.kaggle`, `.config`, `.claude`: tools keep sign-ins there), AppData and browser data, the `data/`/`secrets/` of any copy of the agent; nothing outside the roots; never changes a file. Big text files are read a window at a time |
| `computer_screenshots` | A computer run's step screenshots (by task id, or the latest run) as markdown image lines the reply shows, served by `GET /api/computer/runs/{run}/screenshots/{name}` (computer group) | Never guess where screenshots are |

How schedules run is in [AGENT.md](AGENT.md#schedules).

### Canvas: drafts written and refined together ([CANVAS.md](CANVAS.md))

| Tool | What it does | Rules |
|---|---|---|
| `canvas_write` | Starts a draft: a `document` (guide, script, captions, plan, email) or a `page` (website page or change, previewed with the site's styles). A dedicated writing call drafts it from the instruction | The chat model gets an outline back, never the draft |
| `canvas_edit` | Applies a change ("punchier intro") as a new version | Earlier versions are kept |
| `canvas_revert` | Makes an earlier version the newest again | History only grows |
| `canvas_list` | The drafts, newest first | none |
| `canvas_publish` | Hands a draft to AgentWork for one of the owner's projects | Only when the owner's own message says "publish"; on a branch, reviewed as usual |

### Suggestions and questions: nothing happens until the owner taps

| Tool | What it does |
|---|---|
| `propose_task` | Other PC work (not a project job): the owner gets a **Run** button |
| `propose_project` | Suggests registering a repo with AgentWork: the owner gets an **Add project** button |
| `ask_user` | One multiple-choice question when a detail is missing; the owner answers with a tap. Never for a question the owner asked: that gets answered |

### Memory

| Tool | What it does |
|---|---|
| `forget_memory` | Erases something the owner asked to forget, described in a sentence, from facts, past messages, every summary, learned recipes, and what the agent wrote from it (candidate ideas, proactive messages, Today cards); a second AI confirms which facts and recipes are part of it. Reports what it removed and that the nightly backups (14 days) still hold it. Only when the owner's own message asks to forget; the assistant must call it before saying anything is forgotten |

### Lookups: they change nothing

| Tool | What it returns |
|---|---|
| `get_system_status` | daemon state, the running task, counts, blockers, memory statistics |
| `background_status` | what the away shift background worker is doing (or its last session, steps, findings and search budget) |
| `look_into` | hands a lookup that needs digging to a background helper (`core/follow_up.py`, read-only tools, up to 8 rounds, 2 at once); the reply comes back at once and the answer arrives later as a chat message with a push. Also started by `keep_promise` when a reply promises later work ("one moment", "let me pull that now") and nothing was started |
| `list_tasks`, `get_task` | the task queue and one task's details and output. `get_task` also shows `needs_input` (an AgentWork job that changed nothing) and, for a self-update, how applying it went |
| `read_attachment` | a document the owner attached in the chat (by its `att-…` id), from any character position; text from OCR can contain misread words |
| `read_logs` | the worker log, without the heartbeat noise |
| `search_memory` | facts, past messages and events (dates allowed: "what did we do last Tuesday") |
| `recent_image_prompts` | the owner's last `/image` requests (24 h): prompt, enhanced version, style, model, file. Image prompts are kept **only** here, never in long-term memory (the owner's choice), so "enhance my last prompt" works without storing them |
| `list_project_files`, `read_project_file` | the agent's own source code (never `.env`, databases, keys or binaries) |
| `preview_task_diff` | the git diff of pending code changes in an AgentWork workspace before applying it |
| `get_weather`, `web_search`, `web_research`, `get_news`, `fetch_webpage` | live information. Web content is labelled as untrusted and never followed as instructions. `fetch_webpage` refuses this PC and the home network, and after a turn read a file on the PC it opens only addresses from the owner's message or that turn's results. |
| `gmail_important`, `gmail_search`, `gmail_read`, `gmail_unread` | the owner's Gmail through Connectors. `gmail_important` is a shortlist sorted by Gmail's own signals that the model ranks with what it knows about the owner; `gmail_read` lists the email's links with their real addresses; `gmail_unread` gives Gmail's own counters. Email text is labelled as other people's words, never instructions |
| `propose_email` | sends, replies to or drafts a Gmail email **only through a card** showing the exact recipient, subject and text; the owner's tap on Run sends it (`companion.run_proposal`, kind `email`). A reply goes to the email's own sender, in its thread |
| `contacts_search` | the owner's Google Contacts (name, email, phone) |
| `calendar_events`, `propose_event` | Google Calendar: read events; add one **only through a card** (kind `calendar_event`) |
| `list_items`, `add_to_list`, `check_off` | The owner's own lists and to-dos (Google Tasks through Connectors): read, add (a new list name makes the list) and check off **right away, no card** (the owner's choice, 2026-10-11: private and easy to undo). Never deletes a list |
| `propose_text`, `propose_call` | Text or call someone (a contact's name or a number): a card (kind `text`/`call`) with who, the number and the exact message; **the phone** opens Messages or the dialer filled in and the owner sends or calls. The agent never sends a text |
| `drive_search`, `drive_read` | find and read the owner's Google Drive files (Docs, Sheets as CSV, Slides, text) |
| `connect_google` | the phone sign-in link. The address the owner pastes back is caught in `chat.py` and goes straight to Connectors: never to a model, the history or memory |
| `zoho_mail`, `zoho_mail_read`, `connect_zoho` | Zoho Mail (wecare@example-site.com, the website's and Pinterest's mailbox), **read-only**: unread counts, newest, search, one email with its links. The pasted Self Client details are caught in `chat.py` like Google's |
| `watch_mail`, `watch_for`, `list_watches`, `change_watch`, `stop_watch` | watches for anything: `watch_mail` tells the owner whenever new mail matching a sender, subject or words (or every new inbox email) reaches Gmail and/or Zoho, and with a `deadline` (and `note`) it's a follow-up that reminds him if nothing came by then; `watch_for` watches a web page (words appear or disappear, or its text changes; an optional AI check), a web search (new results) or the calendar (before events); `change_watch` narrows one in place ([WATCHES.md](WATCHES.md)) |
| `subscriptions` | what the owner is subscribed to, from their email: paid services (price, renewal date) and mailing lists (unsubscribe link). The daemon's mail sync keeps it current every 30 minutes and pushes a newly found paid subscription to the owner |

Every tool result is capped at about 5,500 characters (ending on a sentence) and scrubbed of secrets before it
goes back to the model.

## Going live: one tap on the card

A finished AgentWork job on **alwaysonagent** or **website** shows a go-live chip on its card. **The owner's tap is
the go-ahead**: it calls `POST /api/tasks/{id}/apply` directly (no chat turn, no tokens), and `core/self_update.py`
starts that project's program on its own:

| Project | Chip | Program | What it does |
|---|---|---|---|
| `alwaysonagent` | ⚡ Apply update | `scripts/apply_update.py` | tests on the job's copy, merge, tests again, restart, health check; automatic rollback, else push to GitHub |
| `website` | 🚀 Push live | `scripts/apply_website.py` | catches up with the live `main` (stops on a clash), `npm ci` if needed, `npm run build` (commits what it regenerates), `npm test`; then pushes `main`, which deploys example-site.com |

The chip follows the job's state (`data/updates/<job>.json`): 🔍 Preview diff + the go-live chip, then
⏳ Going live…, then ✅ Live (or ↩️ Rolled back, or 🔁 Try again after a failed attempt that pushed nothing). The
outcome also arrives as a push notification. In chat, "push it live" makes the agent call `offer_go_live`, which
shows the card; it never acts itself. Only one go-live runs at a time.

## Tool groups: only the tools a reply needs

All the tool descriptions together were ~4,200 tokens, resent on every call and every tool round; with memory
and history that pushed chats past free-tier request limits (Groq answered "413 Payload Too Large" on
2026-09-30). Tool schemas were trimmed, and replies now get routed groups:

- **core** always (~1,150 tokens): `search_memory`, `forget_memory`, `ask_user`, `propose_task`,
  `get_system_status`, `list_tasks`, `get_task`, `cancel_task`, `read_logs`, `background_status`, `look_into`.
  `connect_google` is in the mail, calendar and drive groups (2026-10-27);
- **group guides** (2026-10-27): how to use each group's tools is in `GROUP_GUIDES` (`core/assistant_tools/`),
  sent in the reply's context note only with that group (and returned by `more_tools` when it loads one). It used
  to be in the persona, ~1,500 tokens on every message; the persona is now ~1,700 tokens
  (`test_the_persona_stays_small` keeps it under 2,000). A new instruction about one group's tools goes in its
  guide, not the persona;
- the groups the **message itself** is about (`pick_groups`): **keyword regexes** (`GROUP_WORDS`) on the message,
  plus **meaning** (`GROUP_EXAMPLES` matched with bge-small, cut-off `SIM_THRESHOLD` 0.70, at most 2 groups this
  way): **web**, **videos**, **stats**, **projects**, **schedules**, **mail**, **calendar**, **phone**, **lists**, **drive**, **watches**,
  **files**, **canvas**, **computer**. Only a short
  yes-type reply ("yes, do it", `AFFIRMATION`) also reads the assistant's last message, which it answers. A message
  matching nothing gets core tools only;
- **`more_tools(group)`**, which loads another group mid-reply when neither keywords nor meaning gave it away
  (its menu uses the short labels in `GROUP_ABOUT`: ~250 tokens, was 450).
  The tool loop sends the same list each round, so the new tools are there on the next one.

**Measured on 51 real messages (2026-10-04, IDEAS #16):** reading the last three messages too and adding web
when nothing matched sent 114 groups nobody needed ("Thank you" got the project tools because the reply before
it mentioned code), ~1,610 group tokens a call. Message-only finds all 21 needed groups with 12 extra, ~444
tokens on top of core. bge-small was also the best routing model (10 ms); Qwen3-Embedding and EmbeddingGemma did
no better. Cases are in `data/evals/routing_cases.json` on the Mini.

Every reply logs its routing decisions, tool calls and tokens to `data/tool_routing.jsonl` (never message text;
trimmed at 5 MB). `CHAT_TOOL_ROUTING=0` in `.env` sends every tool again. A new tool must be added to a group (a
test checks).

**The router model (since 2026-10-13, `pick_groups_router`):** before a chat reply, one small call (3.5 Flash-Lite,
then Groq gpt-oss-120b; 60 tokens, 5 s timeout, logged as `route_tools`) picks the groups instead of `pick_groups`.
It sees the message **and the assistant's previous reply** (an AI judges the context, unlike the keyword test
measured above). If it fails or `CHAT_TOOL_ROUTER=0`, `pick_groups` is used. **Core always goes too, whatever the
router says** (2026-10-14: it could answer "no tools", follow-ups like "I tapped it but it failed" got none, and
the chat invented a log check for task-196, then claimed a merge it never did). The router can't pick `core` or
`memory_check`; its prompt (`ROUTER_SYSTEM_PROMPT`) must list every other group (a test checks). If a reply ever
has no tools, `NO_TOOLS_NOTE` tells the model so.

**Is it worth its call?** `python scripts/mini.py py < scripts/router_report.py` (read-only, no AI calls) prints,
for the last 7 days: how many replies had to call `more_tools` (a missed group) and which groups; who picked the
groups (`picker` in `tool_routing.jsonl`, since 2026-10-14: `router`, or `keywords` when it failed); the router's
own timings and failures; 3.5 Flash-Lite calls per day against the ~500 limit and the router's share; chat calls
that timed out (the 25 s per-call wait). It ends with a verdict: misses over 10%, keyword fallbacks over 20%, a
router p90 over 3 s or a Flash-Lite day over 400 each say what to change.

## Adding a tool

1. **Handler:** add a method to `AssistantTools` and register it in `self._handlers`.
2. **Schema:** add it in `specs()`. The description is what the model reads, so say *when* to use it as well as
   what it does. Keep it short; every tool's schema is sent with every message.
3. **Group:** add its name to exactly one group in `TOOL_GROUPS`, and to that group's `GROUP_WORDS` if people
   ask for it with new words. A new group also needs a line in `GROUP_ABOUT` (what `more_tools` offers).
   `tests/test_tool_routing.py` fails if a tool is in no group.
4. **If it starts work:** build the task in `core/actions.py`, so the app and chat stay the same. Guard
   anything public or irreversible the way the TikTok tools are guarded.
5. **Tell the assistant:** update the "How you work" section of `ASSISTANT_PERSONA` and `CAPABILITIES_SUMMARY`
   in `core/memory/`. Otherwise its earlier messages ("I can't do that") win.
6. **Tests:** add them to `tests/test_action_tools.py` (actions) or `tests/test_assistant_tools.py`
   (lookups), with stand-ins for anything real.
7. **Check it with the real model** on a scratch database before going live: a few typical requests, then look
   at which tools it picked.

## Tools that are separate programs

Bigger jobs are their own tools, each with its own repo, and the agent runs them as separate programs:

| Tool | Job | How the agent calls it |
|---|---|---|
| **TiktokVideos** | Write a quote, render the promo video, post to TikTok | `python tiktokvideos.py render/post/list/cleanup`; JSON lines on stdout ([README](../../TiktokVideos/README.md)) |
| **SocialPostEngine** | Post the site's pins to Pinterest (official API; X left out, it charges per post) | `python socialpostengine.py post --count N` (+ status/queue/sync); JSON lines on stdout ([README](../../SocialPostEngine/README.md)) |
| **VibeStats** | Collect the site's numbers (Clarity visits, cards sent, MailerLite subscribers) and add up the week | `python vibestats.py collect` nightly, `week` for the report; JSON lines ([README](../../VibeStats/README.md)) |
| **Connectors** | The owner's own accounts: Gmail, Google Calendar, Contacts and Drive, Zoho Mail (read-only), and the subscription database | `python connectors.py important/search/read/sync/subscriptions/events/drive-search/send-email/zoho-search/...`; JSON lines ([README](../../Connectors/README.md)) |
| **ImageGen** | Make an image from a prompt (the chat's `/image`) | `python imagegen.py generate --request "..."`; one JSON line on stdout ([README](../../ImageGen/README.md)) |
| **AgentWork** | Code changes to the owner's repos, on a branch | `python agentwork.py list/add/run`; the instruction on stdin ([README](../../AgentWork/README.md)) |

A new capability of that size (another platform, another kind of job) gets the same treatment: its own folder
and repo, a small command line with machine-readable output, and a thin adapter in `core/` plus an action in
`core/actions.py`.
