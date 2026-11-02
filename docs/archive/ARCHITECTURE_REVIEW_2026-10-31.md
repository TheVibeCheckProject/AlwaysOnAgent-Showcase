# AlwaysOnAgent architecture review, file by file (2026-10-31)

Read-only. No code changed, nothing committed. Branch `main` at `0605720`.

## How deep each file was read

So you can weigh each claim:

- **Read in depth (the key parts, line by line):** `core/llm.py` (all), `core/memory.py` (header, schema, search,
  `respond`: about 600 of 2,395 lines), `core/daemon.py` (1-210, 467-886), `core/database.py` (1-180, 400-510),
  `core/task_engines.py` (1-330), `core/sandbox.py` (all), `core/research_worker.py` (all), `core/llm_usage.py`
  (1-210), `core/observation_sanitizer.py` (1-135), `core/candidates.py` (1-130, 192-536), `core/open_loops.py`
  (1-70, 222-626), `core/away_shift.py` (1-75, 220-620), `core/proactive.py` (507-562), `core/assistant_tools.py`
  (380-470, 640-800, 1125-1284), `scripts/deploy_mini.py`, `scripts/apply_update.py`, `scripts/eval_shadow.py` (all),
  `scripts/eval_sycophancy.py` (all), `scripts/eval_models.py` (1-70).
- **Outline only (module docstring plus every class and function with its line number):** every other file in
  `core/` and `scripts/`. Claims about these files are about structure and stated purpose, not line-by-line behaviour.
- **Tests:** the test names in the five named files, plus `tests/test_docs_truth.py` in full.
- **Companion repos:** folder listing and `requirements.txt` only, except three read through the GitHub API:
  `laptop-bridge` (its whole script) and `Under-desk-labs` (README, `pyproject.toml`, plus parts of `sandbox.py`,
  `model.py` and `server.py`), both private under example-org, and `example-user/DeskAI` (build file only).

## Facts read from the code (not from the README)

- Third-party imports in `core/` and `scripts/`: only `numpy` and `psutil` at module level, plus optional `tiktoken`
  (memory.py L74-78). The rest is stdlib (`sqlite3`, `urllib.request`, `threading`, `concurrent.futures`,
  `subprocess`). **No LangChain, LangGraph, LiteLLM, Celery, Redis, SQLAlchemy, Pydantic models or APScheduler in
  `core/`.** FastAPI and Pydantic are in `requirements.txt` for `interfaces/hud/` (not reviewed here).
- `python-dotenv` is in `requirements.txt` but nothing in `core/` imports it; `core/config.py` L23 has its own `.env`
  parser.
- One process, one SQLite file in WAL mode, thread-local connections (database.py L60-76).
- Test functions: **647**, counted statically with `def test_` across `tests/` (this doesn't count parametrized
  cases). Test run time: not measured here (the handoff says ~1 min laptop / ~30 s Mini; I didn't verify that).

---

## Defects found while reading (real, verify before fixing)

Ranked by how much they matter against the "failures must be visible" rule.

| # | Where | What | Verdict |
|---|---|---|---|
| 1 | `scripts/deploy_mini.py` L66-68 | The pull's exit code is printed but never checked. A failed pull still runs the tests (on the old code, so they pass) and restarts, and the output looks like a successful deploy. | CONFIRMED (code path) |
| 2 | `scripts/deploy_mini.py` L92-105 | No automatic rollback: an unhealthy restart is only printed. `apply_update.py` L193-206 already has the rollback logic. | CONFIRMED (design gap) |
| 3 | `core/open_loops.py` L474-479 | `evidence_key` is saved **before** the AI call. If every model fails (returns None) or the second opinion "couldn't be had" (L511-516), the next run sees the same key and skips the loop until the evidence changes. `quiet_runs` also goes up (L579-583), so its back-off grows. New chat messages often change the key, which softens it in practice. | CONFIRMED mechanism, impact PLAUSIBLE |
| 4 | `core/away_shift.py` L390 | `f"{c.get('score', 0):.0f}"`: unscored pending candidates have `score` NULL (candidates.py L379 sorts `score IS NULL`), so `.get` returns None and the format raises TypeError. `details_report()` crashes while any pending candidate is unscored. Scores are 0-1, so `.0f` would only print 0 or 1 anyway. | CONFIRMED |
| 5 | `core/away_shift.py` L331, L389 | Reads `c.get("subject")`, but `proactive_candidates` has no `subject` column (it's `topic`, candidates.py L204). "Found in the background today: ..." never lists subjects. | CONFIRMED |
| 6 | `core/llm.py` L388-407 | `_pace` holds the **class-wide** `_PACE_LOCK` while it sleeps for up to `PACE_MAX_WAIT` (12 s). A background chore waiting for room on Gemma blocks a chat call's pacing on Flash-Lite in another thread. Affects Gemini routes only (L389-391), which is most chat traffic. | CONFIRMED (code path) |
| 7 | `core/away_shift.py` L483-501 + `core/proactive.py` L532-535 | The away shift swaps `open_loops.research` from its own thread while the proactive thread can run `open_loops.run` at the same moment. Two concurrent runs over the same loops could both settle one fact (two `supersede_fact` calls) or send two notices. No lock. | PLAUSIBLE (not observed) |
| 8 | `core/sandbox.py` L28-79 | `HostProcessSandbox` is a working folder plus a scrubbed environment with `shell=True`, not a sandbox. Only the tests use it (`tests/test_worker.py` L36, `tests/test_robustness.py` L93); production uses only the Docker path (task_engines.py L176-201), and the Mini has no Docker. The docstrings claim "Hardened" and "sub-50ms" (L4-6, L124), and nothing in the repo measures either. | CONFIRMED (dead code + overclaim) |
| 9 | `scripts/eval_sycophancy.py` L136-144 | `scratch_db()` copies the **live** memory database into `mkdtemp()` and never deletes it, so each run leaves a full copy of personal memory in `%TEMP%`. | CONFIRMED |
| 10 | `core/database.py` L451-471 | Crash recovery re-runs interrupted tasks, except `tiktok` and `computer` ones. Interrupted `pins` tasks (actions.py L97) are re-queued, so the "no double post" guarantee rests on SocialPostEngine's own board check. | CONFIRMED (worth knowing) |
| 11 | `core/observation_sanitizer.py` L7 | Claims "Reduces observation token consumption by 30%-60%". I found no measurement behind it. "RTC" in the title isn't a standard term. | Overclaim |
| 12 | `core/research_worker.py` L211-238 | `evaluate_fit` returns fixed sentences picked by keywords. It is a template, not an evaluation. Also, `away_shift.py` L576-582 turns structured results into text, and research_worker L132-167 regex-parses them back. | Accuracy/simplicity |
| 13 | `core/llm_usage.py` L117-118, L132-133 | `record`/`record_failure` swallow every exception with no log line, so lost telemetry is silent. | Minor |
| 14 | `scripts/eval_sycophancy.py` L11 | The docstring says "three conditions" and lists five. Each kind has 3-6 cases (25 in total, L54-129), and `summarise` (L256-294) reports hits/total with no uncertainty, so differences between conditions are only directional. | Minor |

---

## 1. File-by-file table

Verdict key: **KEEP** = keep it hand-rolled. **CONSIDER** = a free library would fit and is worth a look. **SWAP**
= replace it. Nothing reached SWAP under the constraints (single user, free only, small Windows box, tests gate
deploys).

### core/

| File | What it does (citations) | Standard equivalent | Verdict |
|---|---|---|---|
| `llm.py` | Multi-provider client: walks ordered `provider:model` lists (L87-119), benches failing routes (L260-287, L602-606), tells Gemini's per-day 429 from per-minute (L269-282, L586-590), no retry on Gemini 5xx because failed calls count against quota (L333-335), sliding-window pacing per minute (L366-407), skips requests too big for Groq before sending (L373-386), translates OpenAI tool calls to and from Google's format for Gemma (L410-535), tool loop with deadline, Stop and a nudge after an empty turn (L708-773). | LiteLLM Router (fallbacks, cooldowns, rpm/tpm); `tenacity` for retries | **KEEP.** The quota rules are specific to the free tiers and LiteLLM doesn't know them; on a RAM-limited box a big dependency buys nothing. Fix defect 6. |
| `memory.py` | Layered memory: verbatim window plus recursive summaries with roll-up (L1-23, L512-519); facts with validity dates and supersession (schema L676-739); hybrid search: FTS5 BM25 + trigram + dense vectors fused with reciprocal rank fusion, k=60 (L1361-1439); brute-force numpy cosine (L1420); extraction runs after the reply on a one-worker executor (L546, L1828); deletes need a second model's agreement (`second_opinion` L1085); shadow mode for a local model (L569-628). | Mem0, Letta/MemGPT, Zep/Graphiti (bitemporal), LangChain summary-buffer memory; sqlite-vec / Chroma for vectors | **KEEP.** Everything sits in the one SQLite file that's backed up nightly; Mem0/Zep want a vector DB, Neo4j or a hosted plan. Brute force is fine for one user (row counts unknown: I didn't open the DB). CONSIDER splitting the file (2,395 lines), not swapping it. |
| `assistant_tools.py` | Tool registry and dispatch (`call` L1132-1156), OpenAI-format schemas (L790-1130), router-model group picking (L654-702), falling back to regex plus embedding routing (L408-452, L725-746), a `more_tools` meta-tool (L766-786); guardrails: untrusted-content labels (L1146-1151), caps per reply (L454-455), destructive actions only on the user's own words (L1249-1256), no exfiltration after reading a PC file (L1258-1264), approval cards (L1179-1214). | LangChain tools; `semantic-router` (Aurelio); "tool RAG" | **KEEP.** The guardrails are the security model, and a framework would wrap them without supplying them. CONSIDER splitting by tool group (2,357 lines). |
| `proactive.py` | Agent-initiated messages: quiet hours, daily cap, an outbox held until the owner is around (L396-468), and a cycle of brief, outbox, open loops, signals into the pool, a curiosity question, then send from the pool (L507-562). | No library; the pattern is a notification engine | **KEEP.** Bespoke by nature. |
| `candidates.py` | Candidate store (L192-404) and scorer: embedding filters for repeats and topics already discussed (L39-41, L102-119), LLM puts the list in order (L421-495), score = 0.6 rank + 0.4 novelty - 0.15 same-source (L411-414, L531), reply-rate feedback moves the threshold (L310-347). "Answered" means any user message within 6 h (L291-298). | Two-stage recommender (candidate generation, then ranking); MMR-style diversity | **KEEP.** Small, calibrated, tested (test_candidates.py, 19 tests). |
| `open_loops.py` | Facts waiting on an outcome: LLM tagging (L278-325), cadence with back-off (L347-351), cheap triggers (L353-378), evidence from facts, messages, mail and web (L400-456), a verdict kept only if its quote is in the cited item (L493-495) and a second model agrees (L511-516), supersession with history (L517-529). | No off-the-shelf equivalent; "grounded claim verification" | **KEEP.** Fix defect 3 (and 7). |
| `away_shift.py` | Background session after 2 h with the app closed (L257-271), daily search budget of 15 (L51, L274-303), steps: upkeep, open loops, channel feeds, interests, patterns, curiosity, research (L433-612). | Idle-time batch job; Celery beat would be overkill | **KEEP.** Fix defects 4, 5, 7. |
| `research_worker.py` | 8 fixed web searches (L57-70), text parsing (L132-167), keyword-picked "fit" sentences (L211-238), optional LLM HTML digest with a deterministic fallback (L258-320), URL dedupe (L442-444). | A simple scheduled search digest | **KEEP**, simplified (pass structured results; call the fit lines what they are). The chat tool runs it synchronously with `force=True` and outside the away shift's search cap (assistant_tools.py L1159-1168). |
| `llm_usage.py` | Token and failure log per call, labelled by purpose through a thread-local context manager (L39-51), a learned daily quota per Gemini model (L143-166), a model-health line for the brief (L179-207). | Langfuse / Helicone / OpenTelemetry for LLMs | **KEEP.** Self-hosted Langfuse needs Postgres and ClickHouse: too heavy for the Mini. Add a log line to the swallowed exceptions (defect 13). |
| `observation_sanitizer.py` | Regex cleanup before text reaches a model: tracking parameters (L54-65), quoted email threads, disclaimers and footers (L78-110), web boilerplate lines (L113-130), compact task and log formatting (L133+). | `trafilatura` / readability for web text; `email-reply-parser` / talon for replies | **CONSIDER** `trafilatura` for `clean_web_text` only (free, pure Python plus lxml). Drop the unmeasured 30-60% claim. |
| `task_engines.py` | Engines: Antigravity CLI, cloud LLM, Ollama, AgentWork (L319-505); `run_streaming` = subprocess with a reader thread, timeout, process-tree kill, head/tail capture, quota detection (L214-316); files pulled out of markdown with regexes (L99-159); AI-written tests run only in Docker (L179-184). | Strategy pattern; aider-style edit formats | **KEEP.** The regex file extraction (L106-110) is the brittle part; asking for structured JSON output would be sturdier. |
| `daemon.py` | Tick loop every 30 s, woken by an event (L587-815); one worker thread for tasks (L538, L810); a thread per periodic job, guarded by `is_alive` (L663-726); a watchdog that abandons hung tasks and replaces the executor (L823-860); git-status and `.env` snapshots before and after each task (L112-173). | Celery / RQ / **Huey (has a SQLite backend)**; APScheduler; supervisord | **KEEP.** The queue is about 10 lines of SQL (database.py L409-419). Huey would bring neither the drain-before-restart, the after-task file guard, nor the TikTok no-replay rule. Smell: "live or test" is decided by comparing database paths (L497, L598, L671). |
| `database.py` | SQLite WAL, thread-local connections, busy timeout (L60-76); tasks table used as the queue; idempotency keys (L138-143); hand-written migrations (L146-172); crash recovery that knows each task's action (L435-501). | SQLAlchemy / SQLModel; Alembic | **KEEP** raw `sqlite3`. **CONSIDER** a single `PRAGMA user_version` migration step instead of try/except `ALTER`s spread over memory.py L722-739, candidates.py L219-221, open_loops.py L253-259 and llm_usage.py L89-91. Alembic would be overkill. |
| `sandbox.py` | Docker: `--network none`, 512 MB, 1 CPU, removed afterwards (L82-134). The "host sandbox" is no isolation at all (L28-79). | Docker; gVisor; E2B (paid: excluded) | **KEEP** the Docker class. Delete `HostProcessSandbox`/`get_sandbox` or rename them (defect 8). |
| `actions.py` | One place that queues work for both the API and the chat (doc L1-14, L39-157). | Service layer / command pattern | KEEP |
| `backup.py` | SQLite online backup API, integrity check, gzip, write to a temp name then rename, mirror folder, restore check (doc, L94-209). | Litestream (continuous replication) | KEEP. Litestream needs an object-store target; the Drive-folder mirror already works for free. |
| `canvas.py` | Drafts with every version kept, HTML cleaned of scripts (L55-63, L91-229). | Append-only revision history | KEEP |
| `computer_use.py` | Client for the AgentComputerUse process over JSON Lines; Needs-you requests on disk; Take over (L433-490, L64-200). | Sidecar process / plugin over stdio | KEEP |
| `config.py` | `.env` loader (L23-55), log redaction by name pattern (L92-145), scrubbed subprocess environment (L147-198), git lockdown environment (L232-251). | python-dotenv (already in requirements, unused), pydantic-settings | **CONSIDER** using python-dotenv's parser in `load_env_file`; keep the redaction and scrubbing. |
| `connectors.py`, `imagegen.py`, `social_posts.py`, `tiktok_videos.py` | Thin clients: start `python <tool>.py <cmd>`, read JSON Lines (`progress`, then `done` or `error`); secrets go on stdin (connectors.py L49-77, L97-103). | MCP stdio servers (JSON-RPC); plugin architecture | KEEP. The one-shot JSON Lines protocol is simpler than MCP for this. |
| `discover.py` | Wikipedia "on this day", NASA APOD, a trivia API; no AI (doc, L44-118). | Simple fetcher | KEEP |
| `documents.py` | Text from docx/PDF/text files, Tesseract OCR, thumbnails (L71-230, L237-430). | unstructured / docling | KEEP (docling is heavy) |
| `embedder.py` | bge-small ONNX in-process (L24-118); Qwen3-Embedding through Ollama (L121-220). | fastembed (ONNX bge-small, same idea), sentence-transformers | KEEP; fastembed would only save about 100 lines |
| `events.py` | In-process pub/sub, bounded queues, slow subscribers dropped (L48-102); presence from the SSE streams (L104-155); activity (L157-203). | Redis pub/sub; asyncio broadcast | KEEP (one process) |
| `feeds.py` | YouTube: Data API, then RSS feed, then channel page (L289-316). | Fallback chain, RSS polling | KEEP |
| `follow_up.py` | A background thread looks something up and answers later in the chat (L64-145). | Background job / async handoff | KEEP |
| `growth_review.py` | LLM writes the weekly review; any number not in the gathered data rejects it (L225-268). | Numeric hallucination check | KEEP |
| `heartbeat_ping.py` | healthchecks.io ping; `/fail` when the API is down (L102-140). | Dead man's switch (standard) | KEEP |
| `housekeeping.py`, `task_thumbs.py`, `recent_images.py`, `processes.py`, `weekly_report.py` | Retention pruning; ffmpeg frame grabs; a ring buffer; psutil tree kill; a three-tool aggregate. | Simple scripts | KEEP |
| `maintenance.py` | Holds new work, waits for the running task and chat, exits with code 3 so it's relaunched (L23-100). | Graceful drain | KEEP |
| `memory_review.py` | Weekly proposed fact changes, applied only on the owner's answer (L56-211). | Human-in-the-loop curation | KEEP |
| `mini_files.py` | Read-only file access: allowed roots, refuses dot-folders and `data/`/`secrets/` (L66-118). | Path allowlist, traversal protection | KEEP |
| `procedures.py` | "Recipes" (procedural memory) with a copy guard (L79-372, L374-395). | Skill library (Voyager), agent workflow memory | KEEP |
| `push.py` | FCM HTTP v1 with a service-account OAuth token (L93-150); a watcher thread (L159-204). | firebase-admin SDK | KEEP (firebase-admin pulls in gRPC: heavy) |
| `schedules.py` | Hand-written 5-field cron parser with plain-English descriptions and a minimum-gap check (L62-205); store; runner thread (L384-460). | croniter; APScheduler | **CONSIDER** croniter for the parsing only (pure Python, small). Low priority: the parser is tested and has extras croniter lacks. |
| `self_update.py` | Starts `apply_update.py` / `apply_website.py` as their own processes when the owner taps (doc). | Deployment trigger | KEEP |
| `today.py` | "Editions" of cards; an LLM compiles the owner's recipe into filter rules (L234-243); Open Graph previews (L93-110). | Content curation; LLM-compiled preferences | KEEP |
| `vision.py` | Local gemma3:4b through Ollama, one image at a time in the background (L94-220). | Background inference queue | KEEP |
| `watches.py` | Saved mail searches polled every N minutes, no AI (doc, L170-235). | Polling alerts | KEEP |
| `web_tools.py` | Search chain Tavily, Serper, Brave, DuckDuckGo Lite, Wikipedia (L262-410); SSRF defence: public-host check plus a re-check of the connected IP to stop DNS rebinding (L62-118). | `advocate` (SSRF-safe requests) | KEEP. The docstring says "all free, no API keys" (L1-3), but Tavily, Serper and Brave take keys: doc drift. |

### scripts/

| File | What it does | Standard equivalent | Verdict |
|---|---|---|---|
| `deploy_mini.py` | Runs on the Mini: refuses a dirty tree (L56-61), pulls in the signed-in session (L66), full pytest with its own exit code deciding (L72-77), restart via the API, waiting for a drain (L80-90), health and log check (L93-105). | GitHub Actions self-hosted runner; Ansible | **KEEP** (free, one host). Fix defects 1 and 2. |
| `apply_update.py` | Self-update: preconditions (L154-165), tests on the job's copy (L168), merge (L176-184), tests on the merged code (L185-190), restart, 60 s health watch and log scan (L126-150), automatic `git reset --hard` rollback (L193-206), push (L208-213), O_EXCL lock file with a 1 h stale timeout (L267-277). | Canary deploy with automated rollback; CI gate | **KEEP.** The strongest piece of the deploy story. |
| `eval_shadow.py` | Blind pairwise judging of local vs cloud answers: random order (L73-74), exact JSON match counts as a tie (L37-41), harm flags (L45-50). | promptfoo / DeepEval / inspect_ai; "LLM-as-judge" | KEEP |
| `eval_sycophancy.py` | 5 kinds, 25 cases (L54-129), up to 5 context conditions (L50), a judge from another model family (L49), positive controls. | inspect_ai (free), promptfoo | KEEP; fix defects 9 and 14 |
| `eval_models.py` | Benchmark: memory chores plus 22 tool-choice cases; its own Google client and pacer (L72-177) duplicate `CloudLLM._complete_google`/`_pace`. | Offline model-selection eval | KEEP; the duplication is a maintenance cost |
| `eval_injection.py`, `eval_memory.py` | Indirect prompt-injection eval (fake email and web pages); extraction and search eval on real cases kept out of git. | Red-team eval; retrieval eval | KEEP |
| `router_report.py`, `mini_diagnose.py` | Read-only analytics and health snapshot from logs and the DB. | Ops reports | KEEP |
| `mini.py`, `mini_pull_tools.py`, `restore_backup.py`, `cache_probe.py`, `apply_website.py` | One-connection SSH runner (`-EncodedCommand`); pull and test each tool; restore that refuses while the agent runs; Groq cache experiment; website go-live (rebase, `npm run build`, `npm test`, push). | Simple scripts | KEEP |
| `run_forever.ps1` (+ watchdog/install `.ps1`) | Relaunch loop, single instance (L2-19, L40-59). | NSSM / Windows service / supervisord | **KEEP.** A Windows service runs in session 0 with no desktop, which would break AgentComputerUse and the sign-in-session git pulls. |

### Companion repos (summary level, from folder listings and requirements files only)

| Repo | What the listing shows | Notable deps |
|---|---|---|
| TiktokVideos | 13 .py, 18 html/js (renderer, affirmations, performance) | playwright, edge-tts |
| AgentWork | 3 .py, `lockdown/`, `workspaces/` | none listed |
| Connectors | gmail, google_apps, google_auth, github, subscriptions | google-api-python-client, google-auth-oauthlib |
| SocialPostEngine | pins, pinmaker, pinterest_api | Pillow |
| ImageGen | generators, prompts | requests, pillow |
| AgentComputerUse | 49 .py incl. `brain.py`, `circuit_breaker.py`, `credentials.py` | uiautomation, pynput, mss, winrt OCR |
| VibeStats | `sources.py`, `GROWTH_PLAN.md` | none beyond pytest |
| DeskAI | Kotlin/Gradle Android app | n/a |
| AlwaysOnAgent-Lite | 24 .py, packaged (`pyproject.toml`) | fastapi, pywebview, pystray, duckduckgo-search |
| website | static HTML/CSS/JS | n/a |
| Sanctuary, MatrixRain | side projects | n/a |
| DeskAI (`example-user/DeskAI`, public; same as the local `DeskAI` clone) | 81 .kt files, Jetpack Compose + Material 3, Room, DataStore, Retrofit + OkHttp + Moshi, Firebase (FCM), CameraX, biometric (`app/build.gradle.kts`) | The standard modern Android stack: nothing hand-rolled at this level. **KEEP.** |
| laptop-bridge (`example-org/laptop-bridge`, private) | One script, `bridge/ziggy_bridge.py` (417 lines), plus `inbox.json`/`outbox.json` and 26 screenshots committed under `bridge/shots/`. Every 10 s it reads `bridge/inbox.json` through the GitHub API and runs the command: `shell` uses `subprocess.run(cmd, shell=True)` in the home folder with a 300 s timeout (L150-173); `shell_bg` runs in the background; `screenshot` grabs the screen and commits it. A non-empty `bridge/STOP` file stops it. Token: a fine-grained PAT in `~/.ziggy-bridge/token.txt`. Last commit 2026-10-30 ("bridge: clipboard"; that command kind isn't in the committed script, so the copy that runs may be newer). | **What a reviewer will call it:** a pull-based remote-execution agent using a git repo as a dead drop (the same shape as a C2 implant). **Risks:** whoever can write to that repo, or holds that token, gets an unrestricted shell as you on the laptop; there's no command allowlist; screenshots and output are committed, so they stay in git history even after deletion. Equivalents: a self-hosted GitHub Actions runner, or SSH over Tailscale (both free, both already in your setup). **Verdict: CONSIDER replacing it with SSH over Tailscale**, or at least an allowlist and keeping screenshots out of git. |
| Under-desk-labs (`example-org/Under-desk-labs`, private) | "The Table": three local-model characters run plan, build and test rounds, a scribe writes a shared memory file, and a viewer streams the session. 46 files; the only dependency is pytest (`pyproject.toml`). `table/sandbox.py` is a per-session folder plus a venv, path-guarded, and agents may run only pytest, with a scrubbed environment and a timeout. Its docstring is honest: "this is a venv, not a jail" (L3-5). `table/model.py` refuses hosted and paid API hosts (`BLOCKED_HOSTS` L20-50). `table/server.py` binds `0.0.0.0:8765` with GET-only routes (`/events`, `/poll`, `/files/`, `/sessions`, `/health`; L82-101) and no auth. Resume after a crash. Last commit 2026-10-08. | Equivalents: AutoGen GroupChat, CrewAI, ChatDev/MetaGPT (multi-agent orchestration). **KEEP:** it's small, has no dependencies and was deliberately built separate from the agent. Note: the feed server is unauthenticated on the LAN, so anyone on the network can read the session files. |

---

## 2. Jargon map (plain description to industry term)

| What it does, in plain words | Say this |
|---|---|
| Tries Gemini, then Groq, then Cloudflare... and skips one that's failing for a while | **LLM gateway with a fallback chain and circuit breakers** (cooldown = "open circuit") |
| Waits so it never sends more than N requests a minute | **Client-side rate limiting, sliding window** |
| Tells a per-day quota error from a per-minute one | **Quota-aware retry policy / error classification** |
| Translates OpenAI-style tool calls to Google's format | **Adapter pattern / schema translation layer** |
| Recent messages verbatim, older ones summarised, summaries of summaries | **Hierarchical (recursive) summarization, tiered context memory** |
| Facts keep when they became true and when they stopped | **Bitemporal fact store with supersession (soft deletes, history kept)** |
| Keyword search plus meaning search, results merged | **Hybrid retrieval (BM25 + dense vectors) with reciprocal rank fusion** |
| Learns facts after replying, in the background | **Asynchronous (write-behind) memory extraction** |
| A second model must agree before deleting | **Cross-model verification / LLM-as-judge for destructive operations** |
| Local model answers too, but its answers aren't used, only logged | **Shadow mode (dark launch)** |
| Only sends the tools a message needs | **Dynamic tool selection / tool retrieval / semantic routing** |
| Labels email and web text "not instructions" | **Indirect prompt-injection mitigation via provenance tagging ("spotlighting")** |
| Proposal card, the owner taps Run | **Human-in-the-loop approval gate** |
| Think of several messages, drop repeats, rank, send the best | **Candidate generation, filtering and ranking (two-stage recommender)** |
| Raises the bar when messages get ignored | **Implicit-feedback threshold tuning** |
| Agent messages first, quiet hours, daily cap, waits until he's around | **Proactive / mixed-initiative agent with presence-aware notification throttling** |
| Keeps a fact only if the quote is really in the source | **Evidence-grounded claim verification (attribution checking)** |
| Checks less often when nothing turns up | **Exponential backoff polling** |
| Skips the AI call when nothing changed | **Change detection via content hashing** |
| Tasks in a SQLite table, one at a time | **Persistent job queue with a single worker (SQLite-backed)** |
| Wakes every 30 s, or at once when work arrives | **Tick loop with event-driven wakeup** |
| Kills a task that runs far too long | **Watchdog timer** |
| Checks the agent's own files didn't change during a task | **Integrity tripwire / post-execution file-system audit** |
| Restarts only after the running task finishes | **Graceful drain before restart** |
| Never re-posts an interrupted TikTok post | **At-most-once delivery for side-effecting jobs** |
| A retried request can't create a second task | **Idempotency keys** |
| Interrupted tasks fixed up at startup | **Crash recovery / orphan reconciliation** |
| Tools are separate programs that print JSON lines | **Process-isolated plugins over a stdio JSON Lines protocol** ("orchestrator and worker processes") |
| Tests in a container with no network | **Ephemeral, network-isolated container sandbox** |
| Deploy: pull, test, restart, check health | **Push-based deployment with a test gate and post-deploy health check** |
| Self-update rolls back when unhealthy | **Automated rollback on failed health check (canary on a single host)** |
| An outside service emails when pings stop | **Dead man's switch / heartbeat monitoring** |
| Hides keys in logs | **Secret redaction in structured logging** |
| Pages can't make it fetch local addresses | **SSRF protection with DNS-rebinding defence** |
| Copies the live DB safely while running | **Hot backup via the SQLite online backup API, with restore verification** |
| Tokens and failures per call, per purpose | **LLM observability / token accounting** |
| Blind A/B judging of two models' answers | **Pairwise LLM-as-judge evaluation with position randomization** |
| Tests whether it caves when the user pushes back | **Sycophancy evaluation with positive controls** |
| Fake hostile emails to see if it obeys them | **Red-teaming for indirect prompt injection** |
| Live updates to the phone | **Server-Sent Events over an in-process pub/sub bus with backpressure** |
| How-to notes it learned from its own work | **Procedural memory / skill library** |

Interview honesty notes, so nothing is inflated:
- Say "single-process, single-user, SQLite-backed". Don't say "distributed" or "microservices": the tool repos are
  child processes started per call, not services.
- Don't call `HostProcessSandbox` a sandbox. AI-written code is isolated only when Docker exists, and the Mini has none.
- `research_worker` is a scheduled search digest. Its "fit" lines are templates.
- Quote model results with their sample sizes (the sycophancy cases are 3-6 per kind).

---

## 3. Top 5 swaps worth considering, and top 5 to keep hand-rolled

### Worth considering (none is urgent; all are free and small)

1. **`PRAGMA user_version` migrations (stdlib, no new dependency)** in place of the try/except `ALTER TABLE` calls
   spread over five modules. *Constraint:* reliability. A failed migration is visible and ordered instead of
   swallowed (database.py L148-151 is `except Exception: pass`).
2. **`trafilatura` for `clean_web_text`** (observation_sanitizer.py L113-130). *Constraint:* free, pure Python plus
   lxml, light on RAM. Better boilerplate removal means fewer tokens per web page, and the free quotas are the
   binding limit.
3. **python-dotenv's parser in `config.load_env_file`** (L23-55). *Constraint:* it's already in
   `requirements.txt`, so this costs no new dependency and removes a hand-written parser's edge cases (quotes,
   `export`, multi-line).
4. **croniter for cron parsing** (schedules.py L62-160), keeping `describe()` and the minimum-gap check.
   *Constraint:* pure Python and tiny; it trades bespoke parsing for a widely used one. Low priority.
5. **Reuse `apply_update.py`'s rollback in `deploy_mini.py`**: an internal reuse rather than an outside library,
   and the one with the most reliability payoff. *Constraint:* "failures must be visible, not silent". Today a
   failed pull or an unhealthy restart only prints.

Considered and rejected: **LiteLLM** (doesn't know the free-tier quota rules; heavy), **Celery/RQ** (need Redis),
**Huey** (SQLite backend works, but lacks the drain, the file guard and the no-replay rule), **LangGraph/LangChain**
(adds an abstraction over a loop that is about 70 lines, llm.py L708-773), **Mem0/Zep** (vector DB or Neo4j or
hosted), **Langfuse self-hosted** (Postgres + ClickHouse), **Windows service/NSSM** (session 0 breaks desktop work),
**Alembic/SQLAlchemy** (overkill for one file and one user).

### Keep hand-rolled

1. **`core/llm.py` fallback and quota logic.** Free tiers only: the per-day vs per-minute 429 split (L269-282) and
   "no retry on Gemini 5xx because failed calls count" (L333-335) are what keep the agent working at $0.
2. **`core/assistant_tools.py` guardrails** (L1146-1151, L1249-1264, approval cards). Single user with real
   accounts: these are the security boundary, and no framework provides them for you.
3. **`scripts/apply_update.py` test-merge-test-restart-rollback.** "Tests gate deploys": it already does what a
   CI/CD product would, on one box, for free.
4. **`core/memory.py` on SQLite FTS5 plus numpy.** Limited RAM, one file to back up: hybrid search with no extra
   server, and it degrades to keyword-only when the embedder is down (doc L16-19).
5. **`core/daemon.py` + `core/database.py` queue and crash recovery.** Reliability: action-aware recovery
   (TikTok at-most-once, computer tasks not replayed, database.py L451-471) and the post-task file guard
   (daemon.py L112-173) are rules a generic queue wouldn't know.
