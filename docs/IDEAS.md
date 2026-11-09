# Ideas Board

What's worth doing next for AlwaysOnAgent and its tools, in order, and what's already been done.
**Other AIs:** read [How this board works](#how-this-board-works-for-ais) at the bottom before you add anything.
The system's current state, architecture and rules are in [AI_HANDOFF.md](AI_HANDOFF.md) and are not repeated
here; this board is only the roadmap.

---

## At a glance (2026-10-11)

**Next up**
1. **#27 Computer use, applications:** reliable on fresh tasks now; next is filling a real, low-stakes form with
   the profile store and the form filler, stopping before Submit.
2. **#14 Connect to anything:** Google and Zoho Mail (read) are live. Zoho sending is declined for now (the owner,
   2026-10-10); next the general system, then social.
3. **#12:** settled: chat stays on 3.5 Flash-Lite first (checked Oct 11: all 12 replies in the last 24 h came from
   it). Open: maybe Gemma 4 26B first for the chores.

**Waiting on something**
- **#24 Sunday review:** the first real one is **Oct 11, 18:00**.
- **#25 Recipes (procedural memory):** live with the copy guard; see what it learns over a week.
- **Pinterest:** live, 2 pins a day (schedule #6); the pin maker tops up the queue daily, with drafts approved in
  chat.
- **#4 Website go-live:** happens with the next real website change.

**Just finished (Oct 11):** computer use made reliable on fresh tasks (11/11 clean, monitor on or off), file
actions, retasks (a clean run repeated without the AI, `/retask` in DeskAI), a copy guard on learned recipes, and
the DeskAI redesign (Circuit look, tasks folded in chat, slash commands as you type).

**Finished earlier (Oct 4):** recipes the agent learns about its own work (#25), a second AI checking before
memory deletes anything (#11), restarts that wait for a running task (#23), model-health warnings (#12),
TikTok quotes that can't be near-copies, and tests that can no longer touch the live database.

---

## Ranked ideas

| # | Idea | Value | Effort | Status |
|---|---|---|---|---|
| 27 | AgentComputerUse: browses, researches, fills in applications, uses apps | High | Large | **live and reliable (Oct 11: fresh batches 11/11 clean); file actions; retasks; saved passwords and applications not yet used for real** |
| 14 | Connect to anything | High | Large | **Google (Calendar, Contacts, Drive, sending) and Zoho Mail live; Zoho sending, the general system and social next ([CONNECTIONS_PLAN.md](CONNECTIONS_PLAN.md))** |
| 28 | Watches: tell me when something happens | High | Medium | **watches for anything live 2026-11-03: mail, web pages, web searches, calendar ([WATCHES.md](WATCHES.md)); the owner reversed his Oct 10 "no" on web pages; DeskAI's Watches tab** |
| 25 | Recipes: how things got done | High | Large | live; copy guard added Oct 11; watching |
| 12 | Keep chat on its best model | High | Small-Medium | **Oct 6: benchmark; Gemma 4 for the chores next** |
| 4 | First real website go-live | High | Small-Medium | waiting on a real change |

Value = how much it helps the owner. Effort: Small = under an hour, Medium = a session, Large = several.

### #27 AgentComputerUse
- **What:** a separate tool (the hands) that uses the computer like a person, mainly the browser: find
  things out, fill in applications (stops before Submit), and use apps. Plans & Research:
  [docs/COMPUTER_USE_PLAN.md](COMPUTER_USE_PLAN.md), [docs/COMPUTER_USE_ARCHITECTURE_RESEARCH.md](COMPUTER_USE_ARCHITECTURE_RESEARCH.md), and [docs/COMPUTER_USE_FAILSAFES.md](COMPUTER_USE_FAILSAFES.md).
- **Oct 11, reliable on fresh tasks:** every run minimizes all windows first and works only in its own; file
  actions done whole by the tool (`open_file`, `save_as`); fresh untuned batches, each failure traced from its run
  log and fixed in general, ended 11/11 clean with the monitor off (it works on or off). **Retasks:** a clean run
  is saved and the same task asked again is repeated without the AI; answers and written text are always fresh
  (DeskAI's Retasks screen, `/retask`). `loop.py` split into mixins. Next: applications (the profile store and
  form filler, stop before Submit) on a real, low-stakes form; re-check before Submit: if the form or a total
  changed after the owner approved it, ask again (from the Hark notes).
- **Shape:** one loop for browser and apps together. It acts only with the real mouse and keyboard on a
  normal Edge, never an automated browser (owner's rule; sites block those). It reads everything, web pages
  included, through Windows' accessibility system plus OCR.
- **Oct 6, sign-ins from the app (tool c87dd42, agent 76814e1, DeskAI build 40):** on a sign-in page the
  owner picks "Type my password" or the page's own options ("Try another way"). A password or code is typed
  in DeskAI's hidden field and goes straight into the box; the AI never sees it. Not yet tried for real (the
  owner will run a Gmail sign-in when they can watch). DeskAI build 41 shows finished tasks' results (answers
  and reports were hidden behind "Completed").
- **Oct 6, Take over (tool d99bf69, agent 3a3dae5, DeskAI build 43):** the Mini's screen live on the phone; taps,
  typing and keys replayed with the real mouse and keyboard; a running task waits and looks again after Hand back.
  The owner tried it first on the laptop from their phone: fast and good. From DeskAI: the menu's Take over card, or
  any Needs you request.
- **Oct 6, the laptop too (tool dcf49cf/bfb833a, agent 1813a27, DeskAI build 44):** the laptop runs its own Take
  over from login (Tailscale only, kept awake while plugged in); the agent passes calls on; a Mini / Laptop switch in
  DeskAI. For reaching Claude Code on the laptop when away from it.
- **Oct 7, enhancements committed, not yet verified live:** AgentComputerUse `8d1504a`…`753f960` add Bézier
  mouse movement, frame settling, self-healing anchors, saved passwords (Credential Manager), a profile store
  with single-pass forms, recipe replay and the Phase 8 fail-safes (the plan for them is in git history).
  **Deployed Oct 8 (7a088fe, after review fixes) and used in every live run since.** Not yet used for real: saved passwords (none saved) and recipe replay. (The Jackson form is closed: the owner finished it themselves, 2026-10-10.)
- **Phase 3 part 2 live Oct 5 (agent eecc879, DeskAI build 39):** Needs you. The task waits for the owner;
  a loud alert with the screenshot is answered in the notification or the Needs you panel.
- **Phase 3 part 1 live Oct 5 (agent 43da4cf):** ask in chat, get a Computer task card, tap Run; the steps show
  on the live task card, and the answer comes with quotes from the page. Next: Needs-you alerts with screenshots,
  then Take over, then the Passwords screen.
- **Phase 2 (the AI loop) works, Oct 5:** "Find the current temperature in Springfield, Illinois" on the Mini gave
  71°F from Google's "Springfield, IL" panel, checked against the screenshot (4 steps, 6 free requests). The first
  run had answered with Chicago's temperature; that's now refused in code: evidence must name the subject,
  typed text doesn't count, and a second-opinion request checks every answer.
- **Phase 1b done Oct 5:** on the Mini (code in C:\Projects\AgentComputerUse, the agent's Python). It runs
  inside the desktop session through mini/run_in_session.ps1 (a one-off Interactive scheduled task), wakes the
  monitor, and waits for renders. The demo passed on the Mini on the first try, and opened a new Notepad tab
  itself. The monitor must stay switched on; sleep is fine (waking from sleep confirmed by the owner).
- **Phase 1 milestone reached Oct 5:** on the laptop, with no AI, it opened Edge, searched, read the
  results page, opened Notepad from the Start button on a new page of its own, typed, saved a new file via
  the Save dialog, and closed only its own windows. That took 7 live runs. Each failure became a rule:
  pop-ups before typing, rebuilt elements, no Windows key (ASUS lock), only touching what it made,
  tabs tracked by id, and dialogs owned by the window. Repo: example-org/AgentComputerUse
  (private), 15 tests.

### #25 Recipes: how things got done (procedural memory)
- **What:** the agent remembers how its own work goes, as "when X, do Y" recipes. It learns them from:
  - how-tos the owner teaches in chat ("from now on, keep the caption under ten words");
  - tool calls that failed and then worked;
  - tasks that only succeeded on a retry;
  - recipes the owner adds in DeskAI.
- **Fail-safes:** a learned recipe is saved only if a second AI agrees; every use is scored, and a learned
  recipe that keeps failing is retired (the owner's own and pinned ones never are). Only the best few go into
  each chat reply or task. DeskAI's Memory screen shows them (build 35).
- **Measured:** 18/19. Teaching 8/8, picking the right recipe 7/7, learning from tools 3/4 (the miss was a
  model not answering mid-test).
- **Oct 11, copy guard:** a learned recipe is know-how, never a copy of the job (the owner: no "repeat Notepad call
  wanting to read a grocery list"). `copy_problem()` refuses one that repeats the job's file names, quoted text or
  long numbers, or scripts more than 3 steps. Repeating a job exactly is the computer tool's retasks instead.
- **Next:** see what it learns over a week (`GET /api/memory/procedures`), and whether more than 4 recipes a
  reply would help.
- *(ChatGPT, Oct 4. The curator wanted to wait for task history; the owner chose to build it in full now.)*

### #12 Keep chat on its best model
- **What:** chat often answered from the backup model (47 of 56 replies, Oct 1-4), mostly because Google's
  best model (3.8 Flash) was briefly overloaded.
- **Done:** a quick retry before giving up on it; every failed call is logged with the reason; the owner gets
  one message when a model nears its daily limit and one when it runs out; the morning brief mentions it when
  most of yesterday's chat came from the backup. Oct 5: 3.7 Flash and 3.6 Flash (free, 1M context) now come
  before the lite backup, so an overloaded 3.8 Flash falls back to a near-equal model.
- **Oct 6 evening:** check how many of the day's chat replies came from a Flash model vs Flash-Lite. This is
  the first full day of the new order (3.7 Flash first), no Gemini retries, and lite-first proactive messages.
  Each Flash model is free for 20 requests a day (docs/GEMINI_FREE_TIER.md); chat averages about 27. Everything
  must stay free (the owner can't pay). If Flash still runs out, cut the requests per message.
- **Oct 6:** AI Studio showed Flash going past 20 a day (failed calls count) and Flash-Lite past 15 a minute
  (chores and chat at once). After two failed models a reply now skips the other Flash models (replies took over
  a minute when Google was overloaded). `scripts/eval_models.py` benchmarks free models on the real chores and on
  tool choice: **Gemma 4 26B** (free, 14,400 a day) beat 3.1 Flash-Lite on everything at 2-5 s; details in
  [GEMINI_FREE_TIER.md](GEMINI_FREE_TIER.md). Building now: a Google-native connection for Gemma, pacing for the
  background calls, and the tool-choice misses it found. After the Oct 7 reset: the baseline (3.5 Flash-Lite) and
  the Flash models, then move the chores to Gemma if it holds up.
- **Oct 11:** read the numbers: all 12 chat replies in the last 24 h came from 3.5 Flash-Lite (6 calls turned away
  and retried). Nothing more needed.

### #4 First real website go-live
- **What:** the one-tap website go-live (build, tests, push) has never run for real. Do it with the next real
  website change and watch it.

---

## Brainstorm (not ranked yet)

- **Inferences as their own tier of memory.** The owner wants the agent to gather inferences ("I think you prefer
  evening check-ins", "you skip AI-news cards"), but: "inferences should always be positioned as not facts... if
  it's gathered via inference, then it stays inference unless otherwise stated." So: a separate, labelled tier,
  never presented or acted on as fact, never promoted by the agent itself; only the owner stating or confirming it
  makes it a fact. His rules for it: **only background processes see inferences** (the watcher, memory upkeep,
  proactive), **never the front-end chat model**, so a guess can't bias its answers or make it agreeable; and when
  the **same inference is reached 3 separate times**, proactive asks him (through its existing get-to-know-you
  questions): yes makes it a fact, no retires it for good. Today chat extraction discards
  inferences entirely (only stated facts are saved) and the weekly review needs his approval, so this is new.
  Ties to the research in DeskAI's `docs/RESEARCH_2026-10-29_CHAT.md` (the watcher, sycophancy). *(owner,
  2026-10-29)*

- **A persona changer in DeskAI's chat.** The same agent and memory, showing up differently: tone (Buddy, Straight,
  Coach), focus (Builder for TikTok/Pinterest/website, Outdoors for trail cams, Legal for paperwork; each brings its
  tools and memories forward), and maybe a tint so you know who you're talking to. Could sit as a small chip where
  the sessions menu was. Owner's idea, 2026-10-29, not sure yet what it would do. Context: chat stays one continuous
  conversation (the app's "sessions" were phone-only; the agent always had one conversation), and the owner said no
  to a "fresh start" button ("I'd feel like I have to reteach everything"). *(owner, 2026-10-29)*

- **Group same-subject facts.** "Checks the weather for Pine Lake" and "owns a fishing cabin near Pine Lake"
  are separate facts; grouping by place, person or project would let them be used together. People could get a
  space of their own, so a relative's dates and doctors never blur into the owner's. *(Claude; Hark notes)*
- **Is fact saving too strict?** Only 9 facts after two weeks. Check with the memory eval whether real
  preferences and plans are missed. The test to use: would a later conversation be worse off without it? One
  pizza lunch is not "loves pizza". *(Claude; Hark notes)*
- **Confirm before overwriting a stable fact.** A birthday or an address that changes gets a "June 2, not May
  7?" before it's replaced; a changeable fact (what's for dinner) updates quietly. *(Hark notes)*
- **A "less" dial for proactive messages.** "Less" once, and fewer messages from then on, until "more".
  *(Hark notes)*
- **Show memory saves in chat.** A small "remembered: ..." line under a reply, with where it came from and a
  tap to undo, so a wrong memory is caught as it lands. Memory is the owner's top priority. *(Hark notes)*
  Not urgent (the owner, 2026-10-11). Memories are decided in the background after the reply (a second AI
  checks each), so the line would land later: measured Oct 11 on 17 saved facts, a median of 9 s after the
  message (the last 8: about 50 s), up to about 2 min 20 s when the free models are busy. Each fact already
  records its `source_msg_id`; the app would need that tied to its chat bubble. Don't run memory in parallel
  with the reply to make it faster: the owner doesn't want race conditions. Turned-down memories aren't logged
  yet (a log line would measure them too).
- **Steer a running computer task in plain words** from chat ("skip that site", "use the other address"),
  without restarting it. *(Hark notes)*
- **Backup chat models make things up.** In a test, two backup models wrote confident tables of golf courses
  a (fake) web page never mentioned. Measure honesty per model before reordering them. *(Claude)*
- **Same package versions on the laptop and the Mini.** They differ (FastAPI 0.115 vs 0.141), so a test once
  passed on the laptop and failed on the Mini. A shared lock file would fix it. *(Claude)*
- **Search phrases stored with facts.** Only if the eval finds misses caused by wording, not the model.
  *(Claude)*
- **Test recordings of each tool's output**, so a changed field fails a test instead of the live agent. Each
  tool needs a safe dry-run first. *(GPT-5.6 Luna)*
- **Short-lived media links**, so the API key isn't in video and image URLs. Low risk today (home network and
  Tailscale only). *(GPT-5.6 Luna)*
- **Disk budget:** a warning threshold and a size line per folder in the weekly report. 172 GB free.
  *(GPT-5.6 Luna)*
- **Go-live history in DeskAI:** the last few updates and how they went (the records already exist).
  *(GPT-5.6 Luna)*
- **TikTok upload time:** the upload log shows how long TikTok takes to accept a video; if it's long, the
  Mini's upload speed is the bottleneck. *(Claude)*
- **A test copy of the agent with some autonomy**, to see what it does: its own Windows account and keys, a
  budget cap, a kill switch, away from the live memory. *(Owner)*
- **Housekeeping:** delete the old Google Drive backup folder on the Mini (`DriveFS.bak-20260930`); Drive has
  worked since the repair. *(Claude)*

---

## Parked or decided against

- **From the Hark notes (Ziggy's probing of Hark, 2026-10-07; curated 2026-10-11, the notes file retired, in git
  history):** not adopted: never volunteering anything noticed about the owner (the owner wants get-to-know-you
  questions); a browser on cloud machines (the owner's rule: the real local computer); an email carve-out that
  skips review (sending always goes through cards); a structured memory export (one owner, nightly backups).
  Already built: faded instead of deleted memories, recipes, mission details and live progress, saving a routine
  after a repeat (retasks). The ideas worth keeping are in the Brainstorm above.
- **NWS severe-weather alerts as a watch** (Ziggy, 2026-10-09): declined by Alex 2026-10-10: they already have weather alerts; no value. Don't propose again.

- **#10 Local model for memory chores** (decided 2026-10-10, owner went with Claude's recommendation): off.
  Claude judged the 64 differing shadow pairs blind (qwen3:4b-instruct vs the cloud; 23 more were identical):
  cloud better 26, local better 5; local did harm 16 times, cloud once. Local deleted a true fact on "I cancelled
  the tasks", saved one-off requests as recipes, saved existing memories with made-up evidence, and garbled the
  profile ("located in Google's Antigravity"); it also missed durable facts. Gemma 4 26B's partial run agreed.
  Qwen3-8B not tried: about twice as slow on the Mini's CPU and the misses are judgement, not speed. Re-open only
  for a clearly better small model: `MEMORY_LOCAL_MODE=shadow` a few days, then `scripts/eval_shadow.py`.
- **Voice mode** (Claude's proposal, 2026-10-10): skipped by the owner.
- **Kaggle GPUs as overflow for batch chores** (Ziggy, 2026-10-09; curator, 2026-10-10): not now. The chores run on
  Gemma 4 26B's free allowance (14,400 a day) and haven't run short; a Kaggle kernel would add moving parts for a
  problem we don't have. Reopen if background work starts failing for quota.
- **One-tap macros from computer-use recipes** (Ziggy, 2026-10-09; curator, 2026-10-10): later, once a computer task
  has actually been repeated (none has yet, so there's nothing to put on a chip).
- **Web-page watches and scheduled computer errands** (2026-10-10): declined by the owner. They have nothing to
  watch online, and when they want something checked they ask the agent in chat. Don't propose any "check a
  website on a schedule" feature again.
- **A bigger local chat model, flashy UI:** not now; small specialised models are doing their jobs well.
- **Mini apps in the canvas:** the owner said no. Drafts and website previews only.
- **A web dashboard, Telegram:** removed; DeskAI is the only way in.
- **X (Twitter) posting:** the API is paid.
- **Power-on after a power cut, checking the second SSD:** blocked by the forgotten BIOS password (only the
  HP password-clear jumper would fix it).
- **Pinterest out of sandbox:** waiting on the owner's Standard-access upgrade.
- **moondream / qwen3-vl:2b for image notes:** tested; they invent details or return nothing.
- **Already in place (proposed by other AIs, checked):** backups are integrity-checked before the Drive copy;
  proactive questions survive restarts and never pile up unanswered.

---

## Done

**Oct 10**
- **Weekly memory check** (the owner's ask): Saturdays the agent proposes missing and out-of-date facts; nothing
  changes until the owner ticks them (DeskAI build 62) or answers in chat. The memory was cleaned up by hand first.
- **Computer tasks narrate in plain words** (the owner's ask): View only shows "Opening Notepad", "Pressing Enter"…
  as it happens, and the task card's details keep the whole log with errors (DeskAI build 63).
- **#26 Events, not just polling** (confirmed done 2026-10-10; the board had said "in progress"): the agent's
  `GET /api/events` stream and instant daemon wake (e67a220, on main and live: the Mini answered the stream) and
  DeskAI's `TaskEvents.kt` (5c75705, in current builds), with polling kept as the fallback. The planned
  part 2 (one stream for every "something happened" signal) is dropped: watches and notices already arrive as
  push notifications and chat messages.
- **Chat off retired models** (Ziggy's inbox note, checked against Google's deprecations page): 3.7 and 3.5 Flash dropped (Google routes them to 3.8 and 3.6; 3.6 itself is NOT retiring, contrary to the note); TiktokVideos off 2.5 Flash-Lite (404); Groq skipped for requests over its cap. See CHANGELOG.md.

**Oct 7**
- **Tappable mission details** (branch `feat/task-details`): task `source` on every creation path, task
  thumbnails that outlive pruned videos (`GET /api/tasks/{id}/thumb`), `video_url` for playing from the details;
  videos kept raised from 3 to 50 (`VIDEO_KEEP_COUNT`). DeskAI shows them when a mission is tapped
  (built since).

**Oct 6 (evening)**
- **#14 part 1, more Google:** Calendar, Contacts, Drive; sending, replying and drafting Gmail through an approval
  card; phone sign-in by pasting the address (never seen by a model). The owner connected it.
- **#14 part 2, Zoho Mail** (wecare@), read-only. A first paste slipped past the check to a model (fixed and
  removed from memory; see CONNECTIONS_PLAN.md).
- **#28 Mail watches:** "let me know whenever Zoho gets a Pinterest email", checked every 5 minutes without AI.
- **Chat on 3.5 Flash-Lite first** (owner's choice from the numbers; see GEMINI_FREE_TIER.md).
- **#29 Repos screen** (DeskAI build 48): the owner's GitHub repos like a file explorer; "Work on this" starts an
  AgentWork job and registers the repo by itself (no more adding projects by hand).

**Oct 4 (evening)**
- **#25 Recipes:** see the ranked list (live, being watched).
- **#11 A second AI checks before memory deletes anything** (`a914529`, `9b8a47e`): outdated facts, forgets,
  duplicate merges. Eval 15/16. It found a real hole: "posts at 9am" vs "9pm" and "brother Jake" vs
  "cousin Jake" looked like duplicates to the search model and could have been merged away.
- **#23 Restarts wait for a running task** (`d0016cf`), holding new work meanwhile (reminders still go out).
- **#12 Model health** (`d0016cf`): see the ranked list.
- **TikTok quotes can't be near-copies** (TiktokVideos `2412f4a`): two posts on Oct 4 both said "Drop your
  shoulders… feel the floor beneath you". The writer now sees the last 40 quotes; near-copies are rejected
  and rewritten; moods rotate.
- **Tests can't touch the live database or the real tools** (`4e65f83`): they had been wiping memory's search
  vectors before every deploy.

**Oct 4 (earlier)**
- **#19 Each fact shows where it came from** (`560eb33`, DeskAI build 34): "From your message on Sep 24: …".
- **#24 Sunday growth review** (`bf31b1e`, `449f0eb`, DeskAI build 33): a review draft with up to 3
  suggestions, every number checked against the data. First real one: Oct 11.
- **#18 Profile check, #20 heartbeat that checks the parts, #21 API contract test** (`d3045fe`, `d306302`).
  Also found and closed: a browser on the Mini could have sent chat commands (CORS removed).
- **#22 Every project backed up on GitHub** (AgentWork, Connectors, MatrixRain now private repos).
- **#17 Prompt-injection test:** 14/14 runs took no action an email or web page tried to trigger.
- **Interrupted TikTok posts are never re-run**, so a restart can't post twice.
- **#16 Tool routing measured:** ~72% fewer tool tokens per message, no needed tool lost.
- **#15 Memory search on a better model:** Qwen3-Embedding 0.6B; search eval 21/21.
- **#1 External heartbeat:** healthchecks.io emails the owner if the Mini goes quiet.
- **DeskAI Memory screen** showed four fake memories; it loads the real ones now (build 31).
- **#8 Attachment thumbnails** in DeskAI (build 30).
- **#9 Memory eval** on real conversations (`scripts/eval_memory.py`).
- **#13 Restore drill:** every nightly backup now restores its Drive copy and checks it.
- **#2 Newer facts win** over old summaries. **#3** Erase rewrites are checked before saving. **#5** No fact
  extraction on "thanks" or "ok". **#6** A test that the persona only names tools that exist. **#7** Two chat
  fixes. **Forget really forgets** (the `forget_memory` tool). Three rule-breaking facts removed.

---

## How this board works (for AIs)

- **Who decides:** any AI may propose. **The Claude Code session on the owner's laptop is the curator**: it
  places each idea (ranked, brainstorm, parked), ranks it, records why, and works the ranked list from the
  top.
- **To propose:** add it to the **Inbox** below, signed and dated, e.g. `(Ziggy, 2026-10-05)`. Say what problem
  it solves and what you checked. To argue with a ranked entry, add an Inbox note ("#26: I think…"); don't
  rewrite the entry.
- **Curator:** empty the Inbox each session. Check every claim against the code or the Mini first, then rank,
  merge, brainstorm or park it with a one-line reason. Move finished work to Done with commit hashes.
- **Numbers are permanent.** Code comments cite them ("IDEAS #3"). New ideas take the next unused number
  (next: **#30**).
- **Verify before you write.** Wrong claims here get acted on.
- **Keep it readable for the owner:** plain words, short entries (what / why / status / next). Long history
  belongs in git, `docs/CHANGELOG.md` and the other docs, not here. Keep **At a glance** current. The system's
  state belongs in [AI_HANDOFF.md](AI_HANDOFF.md), not here.

## Inbox (proposals waiting for the curator)

*Add new ideas or notes on ranked entries here, signed and dated. The curator moves them out.*

*(Empty: emptied 2026-10-10.)*
