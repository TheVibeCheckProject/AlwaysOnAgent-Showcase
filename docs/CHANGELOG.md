# Changelog (the one changelog for this repo)

The single running log of what changed, by any AI or person, and what not to undo. The source of truth for the
system itself is [AI_HANDOFF.md](AI_HANDOFF.md); this file records changes over time. (Named
`CLOUD_SESSION_UPDATES.md` until 2026-10-07; it started as the cloud Claude session's log.)

**Read this file after every `git pull`.** Newest entries first. Each entry says what changed, where, and what not
to undo. Don't start another log.

## 2026-11-04: supervisor.py joins mypy coverage (last gap closed)

`supervisor.py:169` was the one mypy error no gate saw: `ScheduleRunner` takes `on_event: Callable[[str], None]`
but was handed `MemoryManager.record_event`, which returns the event id (int). Fixed with a wrapper method,
`UnifiedSupervisor._record_schedule_event`, that calls `record_event` and drops the return value — the runner's
type stays strict, `record_event` keeps returning the id for its other callers. `supervisor.py` now passes mypy,
and the CI mypy step gains `supervisor.py` (applied from the laptop, since the app token can't push workflow
files), so "mypy: 0 errors" now covers the whole repo, not just `core interfaces scripts`.

## 2026-11-04: function-split rule finished on source + chat.py split into 7 modules

Rule 11 (no function over 50 lines, no file over 500) now holds across all of source: the 23 source functions
that were over 50 lines are split (core/ flat 13, interfaces/hud 4, scripts/ 5, supervisor.py 1), and
`interfaces/hud/chat.py` (847 lines) is split into 7 modules — `chat.py` 201, `chat_turn.py` 437,
`chat_refine.py` 97, `chat_signin.py` 84, `chat_secrets.py` 81, `chat_attachments.py` 74, `chat_images.py` 63 —
with re-exports kept so existing imports still work. `scripts/check_code_size.py` enforces the rule
(function ≤50, file ≤500, source only); the CI step that runs it is not live yet — it has to be added to
`.github/workflows/ci.yml` from the laptop (the app token can't push workflow files), so don't assume CI
checks size until that step lands. Tests are excluded from the size rule (long by nature; the split waves never
touched them), and the 6 mixin classes over 20 methods are a structural exception, not enforced. One test path
updated after the move (`test_task_details.py`: the `source="DeskAI chat"` assertion now reads `chat_turn.py`,
where that code lives — a path update, not a weakening). AI_HANDOFF.md rule 10 and §2 updated for the new chat
layout. Suite: 734-735 passed on the VM (only the known env failures + flakes); mypy clean on 215 files.
Don't undo: the chat route registration order is unchanged, and `chat._image_jobs` / `chat.SCENE_WAIT` stay in
`chat.py` because tests monkeypatch them there.

## 2026-11-04: CI is live on main (pytest + mypy, windows-latest)

`.github/workflows/ci.yml` runs on every push/PR to main: Python 3.13, install `requirements-dev.txt`, install
mypy 2.4.1, cache the embedding model, `python -m pytest -q -n auto`, then `python -m mypy core interfaces
scripts`. First run failed on 2 document tests — both missing tools on the runner, not regressions: the image
test needs Tesseract (OCR raises DocumentError without it) and the video test needs ffmpeg
(`resolve_ffmpeg_binary` falls back to bare "ffmpeg", `subprocess.run` raises FileNotFoundError). Fixed by
installing both via Chocolatey (`choco install tesseract ffmpeg -y`) in the workflow instead of weakening the
tests. Second run green, every step passing, 4m 12s. One warning only: `actions/checkout@v4` and
`actions/cache@v4` target Node.js 20 (deprecated, forced to Node 24) — GitHub-side, cosmetic. The app token
can't push under `.github/workflows/` (403), so workflow changes go through the laptop.

## 2026-11-04: mypy 1,722 errors → 0, merged to main

The whole repo is type-clean under the non-strict `mypy.ini` (committed with the merge): baseline was 1,722
errors in 156 files, now "Success: no issues found in 208 source files". Done in 11 batches across every
package (documents/computer_use/task_engines/web_tools, assistant_tools, daemon, memory, proactive, away_shift,
llm, flat core/, watches/open_loops, today), each verified with a full mypy + full suite run before pushing.
The mixin pattern: declare required methods on the mixin with exact signatures and `raise NotImplementedError`,
but put the declaration in `if TYPE_CHECKING:` when a later mixin in the MRO provides the real method, or the
stub shadows it at runtime. `[no-redef]` from fallback imports gets `# type: ignore[no-redef]` on the
redefining import. Real bugs the pass caught and fixed: unguarded `re.search().group()` (None crash),
unguarded `db.update_task` / `active_task['id']`, `cur.lastrowid` None handling, `_complete` falling off the
end, Optional narrowing. Suite on main after the merge: 741 passed on the laptop. Don't undo: the zero-error
state is enforced by the CI mypy step — if it goes red, fix the code, don't delete the step.

## 2026-11-03 (very last): the handoff and the API doc have maps too

Alex: "maybe even map out the AI handoff too ... and the API.md too". Both open with a table of their sections
(linked, one line each on what's there). `test_in_page_links_in_the_doc_maps_point_at_real_headings` fails if a
map links to a heading that doesn't exist (the relative-link test skips in-page anchors). API.md copied to DeskAI.

## 2026-11-03 (last): every file over ~550 lines has a map

Alex: "map out all the large and mid size files like this ... start with the large and important files". Twelve
more files open with a numbered map (and how a request flows through them) and have `# §N` banners: proactive,
daemon, llm, hud/chat, today, database, away_shift, open_loops, web_tools, documents, computer_use, watches.
Regrouped where code was scattered: `llm.py` (rate limits, pacing, failures and benching gathered into §4),
`database.py` (the daemon-state setters next to get_state / set_state), `watches.py` (the page / search /
calendar code no longer splits the mail code). `hud/chat.py` is mapped, not reordered (route order). Moves only:
every file's code identical to before (syntax trees compared), 741 tests pass. AI_HANDOFF rule 10 lists them.

## 2026-11-03 (latest): maps and regrouped code in the two biggest files

Alex: "consolidate the functions that would be beside each other. The map I want not only for me but for the AI
agents as well." `core/assistant_tools.py`: the class's methods are regrouped, one section per `TOOL_GROUPS`
group in that order (§10 core: memory ... §28 watches), after §1-§9 (how a message gets its tools); helpers sit
with the tools that use them, shared checks in §9. `core/memory.py`: §1-§17, with `_self_check_note` moved next
to `build_context` (§12) and `owner_name` / `subject` to §8 Facts. Each file opens with a map; each section has a
`# §N` banner. Moves only: every statement's code is identical (checked by comparing syntax trees), 741 tests
pass. AI_HANDOFF working rule 10: read the map first, and keep it true.

## 2026-11-03 (later): the chat knows it has its numbers, and can read them

Asked "can you see the numbers yourself now?", the chat answered "I don't have a direct tool or endpoint wired up"
with its week in its closing note (checked: the note was in that reply's context). The note reads as an instruction,
and "How you work" didn't mention it. Now the persona says the self-check exists and the closing "Your last week,
counted" line is its own numbers, and `my_numbers` (stats group: "your numbers", "self-check", "how did you do")
reads the whole set: the latest week, the week before beside it, the owner's quoted corrections, the closing note's
line, any fix waiting. Read-only. Checked live on a scratch copy: "how did you do in chat this week?" called
`my_numbers` and answered with the real counts; "can you see the numbers yourself now?" didn't, because the router
(which overrides the keyword picker) gave no group, so it had only core tools and read `get_system_status`. Now
`SELF_NUMBERS` (the owner asking about the agent's own numbers) always adds the stats group, whatever the router says.

## 2026-11-03 (late): the chat hears its own week; one snapshot a week; reports not cut

Alex ran the weekly numbers and asked "where does the self advancing or self insights live so the agent actually
adjusts based on those?" Honest answer: only he saw them. Three changes:
- **The chat sees its week** (`self_check.chat_reminder`, added to `FINAL_REMINDER` in `build_context`): when last
  week's count is off (over 25% of replies ending with a question, over 1 praise word in 10 replies, 3+ corrections;
  at least 20 replies), one line says so, e.g. "46% of your replies ended with a question (keep them rare)". Its own
  behaviour, counted; nothing about him. Code fixes still need his "start the fix". Measured on the Mini the same
  night (the tone eval's 10 messages, 3 rounds each, note on/off alternating): on 8 praise words and 7/30 replies
  ending with a question, off 11 and 8/30. A small step the right way, within the noise: keep it (cheap, no harm),
  and judge it by the real weeks' self-check numbers, not this eval.
- **One snapshot per calendar week** (`self_check.remember`): every report run used to add one, so his Tuesday run
  became "last week" for Sunday's, and a few runs could fake "worse two weeks running". A later run that week
  replaces it.
- **The weekly report's task card isn't cut** (`REPORT_SUMMARY_MAX`, core/daemon.py): results are capped at 1,500
  characters ("...[TRUNCATED]..." in the middle), which cut the report's TikTok details and the self-check block
  out of the card. Reports keep up to 12,000; other tasks keep 1,500.

## 2026-11-03 (night, very last): trivia and "on this day" are decks

Alex: Today was "getting overrun": 14 of its 20 cards were trivia, "on this day" and NASA, a new one each day. His
design: "one for trivia, one for on this day", small fanned decks that open to the centre of the screen, swipe
between them. `core/today.py`: those cards (`in_deck`) no longer count against `RUNNING_MAX`; the newest `DECK_MAX`
(14) of each come after the feed in the same `items` list (old DeskAI builds show them as cards, as before; 109+ draw
the decks). The recipe's rules still apply to them. NASA's picture stays a card (he didn't ask; a third deck later if
it starts to crowd). Don't count deck cards in the cap again.

## 2026-11-03 (night, last): NASA's picture of the day shows its picture

Alex: "I didn't know I was even getting NASA's picture of the day, because it doesn't show any picture unless I click
on the card." Every NASA card since Oct 29 was titled "NASA Science" with no image: APOD moved to science.nasa.gov,
and the api.nasa.gov feed (on the shared DEMO_KEY) answered HTTP 500 on most builds and the new site's name, with no
usable picture, on the rest. `core/discover.py` `apod` now reads NASA's own pages: the archive's newest entry, only if
it's dated today (`entry_date` reads both of NASA's slug orders), then its `<h1>`, its explanation paragraph and its
`og:image` (1280 px, ~0.2 MB). No key. The four broken cards were repaired in place. (DeskAI build 108 drew the NASA
card taller, 4:5; Alex found it too large, and build 110 put it back to the 220 dp every photo card has.)

## 2026-11-03 (night, later): watches for anything

Alex: "watches should be for anything I want. Whether it's a website, or anything ... whatever I ask for it to watch
it should create a watch for it." (That reverses his Oct 10 "no" on web-page watches.) He picked: web pages, web
searches/news, calendar events, and anything else; page changes judged by plain rules, AI only if asked.
`core/watches.py` has kinds: `mail` (as before), `page` (words appear, words disappear, or the text changes by more
than `CHANGE_MIN_CHARS` with digits blanked; opt-in `ask`, a small AI check run only when the page really changed),
`search` (a past-week result not seen before; every 6 h for the search quotas) and `calendar` (`lead_minutes` before
matching events, once each). Each checks at its own pace (`EVERY`, per watch `every_minutes`, with minimums). A watch
is saved only if its page, search or calendar works now. Chat: `watch_for` (the guide: whatever he asks to watch
becomes a watch; a court case is its docket page). API: `POST /api/watches` takes `kind`. The self-check's "silent
watches" counts failing watches and never-firing mail ones only. Don't undo: no AI per check (the `ask` check only
on a real change), the digit blanking, saving only what works now, and page excerpts labelled as the page's words.
Checked on real pages before shipping: Justia's docket refuses the fetcher (403), so a refused read falls back to
Tavily's extract (`web_tools.extract_page`; same 24,105 characters twice), and page watches read 60,000 characters
(the docket's first 6,000 are its menu). A news front page changed 1,632 characters in 3 s: the guide sends busy
pages to words, `ask` or a search.

## 2026-11-03 (later still): the Watches tab and its API

Alex asked for a watches viewer and a way to add one by hand, as a tab beside Schedules in the same card.
`interfaces/hud/watches_api.py`: `GET/POST /api/watches`, `PATCH/DELETE /api/watches/{id}` ([API.md](API.md),
copied to DeskAI's docs/API.md). Same rules as in chat: a plain search every 5 minutes, no AI, and a new watch counts
only mail that arrives after it. Changes made in the app are recorded in the conversation, like schedule changes.
DeskAI: `WatchesPanel.kt` (list, two-tap Stop, a New watch form: mailbox, every new email, sender/subject/words,
only once) in the Schedules sheet's new Watches tab.

## 2026-11-03 (late night): background tasks know him

Alex: "I don't care what we have to do, but this is a personal assistant for me and no one else, so that matters
too." `task_context` (core/memory.py) gave tasks only project notes, instructions and preferences, and AgentWork
jobs nothing at all, to keep personal details from outside services. Now every task, AgentWork included, gets up to
`TASK_FACTS` (10) facts he stated that relate to it, of any kind, labelled "context about him ... not a conclusion
to reach". Still not sent: the prose profile (a model's summary, which could frame the work) and the conversation.
Facts are only what he said (inferences are never saved), which keeps the bias line. Don't undo.

## 2026-11-03 (night): the self-check drafts its own fixes; Alex starts them

Alex's yes to "improving itself". `core/self_check.py`: when a watched number got worse two weeks running by more
than its noise floor (`WATCHED`; closing questions and praise per reply, so a busy week isn't a regression; failed
chat calls aren't watched, they're mostly provider outages), the Sunday report says so and a fix is drafted for
AgentWork (`plan_fix`: the numbers, where that behaviour lives, a test that fails without the fix, the full suite,
the "don't undo" list, and "don't change how the self-check counts"). It waits 14 days for Alex's own words: "start
the fix" queues it on the `alwaysonagent` project (its own branch; nothing live until he taps Apply), "skip the fix"
drops it. Both are caught in `interfaces/hud/chat.py` before any model, so no model starts a change to the agent by
itself; the chat only gets a note that one waits. The drafted instruction carries the numbers, the code locations
and his quoted words from that week, labelled as evidence and never suggesting a cause. Alex, the same night:
**"I'm okay with background workers getting personal info, I just didn't want them to experience bias."** The line
is bias, not privacy: give background workers what they need, but nothing that steers them to a conclusion (as with
inferences, which background processes may see and the chat never does). Don't undo any of that.

One full-suite run that evening had 1 failure that didn't come back in 7 more full runs or 8 runs of the related
files, and its name wasn't captured. If a test fails only now and then, record its name here.

## 2026-11-03 (later): the weekly self-check; the helper can't send what it didn't check

- **Self-check** (`core/self_check.py`, in the Sunday report from `core/weekly_report.py`, and in the review's
  canvas draft as counted): the Nov 3 hand review, repeated every week in code, with no AI grading itself:
  replies, closing questions, praise words, Alex's corrections ("I already told you", quoted), messages sent twice,
  follow-ups with no lookup, its questions (sent / dropped as already answered / marked down), watches that never
  fire, failed chat calls. Last week's number beside each (`self_checks` in system_state). Alex's ask: the agents
  testing themselves.
- **The background helper only sends what a lookup backs** (`core/follow_up.py`): on Oct 31 it ran 2 seconds, called
  no tool, and sent "the permit has no decision posted yet" as a finding. An answer with no successful
  tool call gets one retry told to look first (`LOOK_FIRST`); still nothing, and Alex gets `NOTHING_CHECKED`
  instead. Don't undo.
- For the record: the permit research that does run (open loops, daily) is a plain web search over public pages,
  not a client for one site.

## 2026-11-03: a week of chats reviewed; seven fixes

Alex asked for a review of his chats with the agent (Oct 27 - Nov 3); each problem was checked against the code and
the live database first. Don't undo any of these:

1. **Every-email watches** (`core/watches.py`, [WATCHES.md](WATCHES.md); Connectors `zoho-recent --folder`): "every
   Zoho inbox email, no newsletters" had been saved as *subject contains 'inbox'* and the chat promised a filter that
   didn't exist. Now `watch_mail` `every_email` (Zoho's Inbox folder / Gmail's Primary tab, bulk skipped by plain
   rules, no AI: sender words, a company's mail subdomain like `legal.pinterest.com`, policy/password wording;
   checked against the live inbox's newest 20). The live watch #4 was replaced by #5, one of these.
2. **"Hang/hold tight" isn't a promise** unless it's "…while I…" (`PROMISE`, core/memory.py): "just hang tight until
   you're ready" and "I'll hold tight on that" started the background helper on nothing and sent empty notices.
3. **No question card when Alex asks for the agent's pick** or says he doesn't know, and never two cards in a row
   (`ask_user`, `WANTS_ADVICE`, core/assistant_tools.py): "how would you structure it?" and "I was asking you what
   you would choose" each got a multiple-choice card back.
4. **Less praise, fewer closing questions** (persona "How to talk" and `FINAL_REMINDER`; `trim_tail_question` drops a
   reply's closing question when the previous reply already ended with one). Measured with `scripts/eval_tone.py`
   (10 fresh messages, Flash-Lite, real memory): before, 9 praise words and 7/10 replies ending with a question; with
   the persona change alone, 8 and 0/10 (the history full of last week's "massive" set the pattern); with the praise
   ban also in `FINAL_REMINDER`, 1 and 3/10 (on-topic questions; the trim drops a second in a row). Asked for its
   pick, it gave one every time in every run (read, not counted: the script's regex is a rough proxy), and critiques
   now lead with the problems. Keep the praise line in `FINAL_REMINDER`.
5. **Proactive questions he already answered are dropped** (`ANSWERED`, core/candidates.py): the question's text
   against his messages of the last 7 days (the 24-hour, subject-only check let three through, 30+ hours after he'd
   answered). Calibrated on the Mini's embedder: the three repeats 0.70-0.78, everything else <= 0.46.
6. **A follow-up keeps what was just attached** (`documents.earlier_attachments`, interfaces/hud/chat.py): the note of
   an image or document from his last 4 messages (30 minutes) comes along, and image text carries a misread warning
   (`MISREAD_NOTE`): it had repeated "synophony eval", then asked him to describe the screenshot.
7. **The brief's greeting comes from the clock** (`proactive.greeting`: no "Good night" at 2 am) and routine mail
   (digests, lottery numbers, promotions) stays out of it. The backup-model line stays (IDEAS #12; it only shows when
   chat mostly missed its best model).

## 2026-11-01: skipped schedule runs alert the owner; TikTok learning fixes (TiktokVideos)

The Mini lost power at 23:40 on Oct 17 (System log: 41 / 6008, unexpected shutdown) and stayed off until Oct 22
22:07: ~10 TikTok videos and the pins were skipped, and only the falling numbers showed it. `core/schedules.py`:
runs skipped past `MISSED_GRACE` now send one `push.send_alert` listing them (was: a log line only). Don't drop it.
Worth setting on the Mini itself: BIOS/UEFI "Restore on AC power loss" = Power On, so a power cut ends with it back
on (needs someone at the machine).

Same day, in TiktokVideos (its README has the details): the learning matched posts to quotes with today's UTC offset,
so when daylight saving ended every summer post missed its quote (54 judged posts became 3); watch time and
completion are now read from Studio's analytics pages and score posts instead of views; quotes follow the website's
voice (under 90 characters, no "I", no body instructions; near-copies checked against the last 60 only); card
backgrounds include the website's newer ones, rotate, and are recorded per video.

## 2026-10-28 (night): no emoji in the brief and status text; Today backfilled

Alex: "Strip system emojis from status/metric outputs and brief text". The brief prompt asked for "emoji bullets";
now "no emoji". Watch alerts lose their 📬. (DeskAI 2.34.0 does the same for its own /status text.) Chat replies
keep any emoji the model or Alex chooses. `Today.build_now(since)` builds an edition from further back; used once
with a backfill that gave findings saved before `url` existed their link back (`https://` + their `link:` key), wrote
a fresh brief with its facts, and built an edition from the last 7 days.

## 2026-10-28 (later still): the brief's facts go with it

DeskAI 2.33.0 shows the brief as MAIL and UP NEXT tiles (Argon's redesign). Argon's first version read them from the
AI's wording and, when that didn't match, showed "USPS, Illinois Lottery" from its sample. Now `_brief` keeps the
facts it gives the AI (the schedules still ahead today, the important mail) and `_post_brief` stores them in
`proactive_messages.data_json`; `GET /api/today` returns them as `header.brief.up_next` (past times dropped) and
`header.brief.mail`. Don't build those tiles from the brief's text.

## 2026-10-28 (later): Today's recipe, live weather, and the brief moved to Today

Alex: "I told the agent to use Springfield Illinois for weather, but it didnt do anything but say got it" (the chat
called get_weather: nothing knew about Today, and the edition had frozen "no weather" when it was built). Now:
- `GET /api/today` returns a `header` read at each call: the date, the weather now (30 min cache) and the day's brief.
- The recipe (step 3 of DeskAI's plan): Alex's words, compiled by one AI call into rules (`core/today.py`
  `save_recipe`): more/less/never topics, channels to follow, a card limit, kinds of card to hide, the weather town.
  `more` topics are searched by the away shift as interests; `never` topics are excluded by `Proactive.is_excluded`.
  `GET/PUT /api/today/recipe`; in chat, the new `today` tool group (`today_settings`), picked by words about Today,
  its recipe or the weather town (not "today" alone).
- The brief goes to Today's header (`brief_to`: `today`, the default; `chat` as before): stored with topic "today", out
  of `/api/inbox`, no push. Alex: "im okay with moving it to the today screen".
- more_tools labels trimmed (mail, projects, files) to keep that menu under its 260-token test.

## 2026-10-28: Today (the agent side of DeskAI's second screen)

Step 2 of DeskAI's docs/FEED_AND_DESIGN_PLAN_2026-10-27.md (Alex: "start on the today screen"). `core/today.py`
builds an *edition* when Today is opened ≥ 6 h after the last one and something is new (never on a clock, no AI call):
interest findings with their page's title/site/preview image, followed-channel videos, what open loops settled,
finished tasks, and the best waiting question. Routes in `interfaces/hud/today_api.py` (docs/API.md "Today").
Findings now keep their full link (`proactive_candidates.url`); `ref` still holds the short key used against repeats.
Images are kept in `data/today_images/` (gitignored), dropped after 14 days unless the card was saved. The chat's
brief and messages are unchanged for now.

## 2026-10-28: Styropyro isn't a standing interest

Alex: "I said I liked his stuff one time a long time ago and it's like a recurring thing ever since." One remark
(Oct 4) became fact #7 ("likes DIY YouTube channels such as Styropyro"), a default followed channel
(`feeds.DEFAULT_CHANNELS`), and 22 proactive candidates. Now no channel is followed until Alex asks, and fact #7 is
faded (kept in "past", restorable from the memory hub). Don't re-add a default channel or revive the fact.

## 2026-10-28: YouTube channels still read while YouTube's feeds are down

"feeds: couldn't read @styropyro: HTTP 404" wasn't us: YouTube's `feeds/videos.xml` answered 404 for every channel
from 2026-10-27 (also Dec 2025; it comes and goes). When the feed fails, `core/feeds.py` now reads the channel's
`/videos` page (`parse_videos_page`, from its `ytInitialData`): the same ids and titles, an approximate date from
"8mo ago". The page has no Shorts, so Shorts are missed until the feed is back. Keep the feed first: it's the stable
format; the page's layout changes now and then.

## 2026-10-28: an image being made says what it's doing

Alex: a slow image looked stuck. ImageGen now prints `progress` events ("AI Horde: 3rd in line", "AI Horde is
drawing it now", "Trying Pollinations (sana)"), taken from the services' own live state; `core/imagegen.run` reads
them while it runs (Popen, not run-to-the-end) and the image job returns the latest as `stage`. DeskAI 2.30.4 shows
it under "Making your image…". Alex didn't want a countdown unless it's correct, and no one knows how long Horde
will take: don't add an estimate or a countdown.

## 2026-10-28: tests run by what changed, the full suite in parallel

Alex's choice: keep every test, but stop running all of them on every change. While working:
`pytest --testmon` (only the tests that run the changed code; `.testmondata` is gitignored). Full suite:
`pytest -n auto` (pytest-xdist, now installed on the Mini too): 606 tests in ~30 s on the Mini instead of ~80, and
`scripts/deploy_mini.py` uses it. The deploy also refuses to run on the laptop (it once pulled and tested the
laptop's copy). See AI_HANDOFF §7.

## 2026-10-27 (later): /image runs in the background

`/image` as one chat request held open ~3 minutes: the connection was dropped on the way ("Software caused
connection abort", then a retry over plain HTTP), or the chat kept "thinking" after the image was made. Now
`POST /api/images/jobs` starts the image in a thread and answers at once; `GET /api/images/jobs/{id}` says how it's
going (`interfaces/hud/chat.py`, docs/API.md). DeskAI 2.30.3 shows a "Making your image…" bubble, keeps the chat free,
and fills the bubble in when it's done. The same `turn_id` sent twice is one job. `/image` through `/api/chat` still
works (older apps). Don't go back to holding a request open for the image.

## 2026-10-27 (late night): made-up media links, and the same video sent again instead of an answer

Chat at 18:06–18:18: asked for the last TikTok video, a model wrote "[▶ x.mp4](/videos/x.mp4)" from memory with no
tool call (a card that can't load). Then "yup you're right the normal lineups!" and "?" each got the video again
and no answer. Fixes:
- `core/memory.py` `ensure_media_shown`: a media link to an address the server doesn't serve (`MEDIA_MADE_UP`: not
  `/api/…`, not http) is looked up on the Mini by its file name and replaced with the real line, else the name and
  "I couldn't find that file on the PC".
- `core/assistant_tools.py` `mini_files show`: refuses a file one of the last 10 messages already showed unless the
  latest message asks for a file (`ASKS_FOR_A_FILE`); the error tells the model to answer what was said.
Don't undo: the model sees its earlier unfinished request and keeps redoing it; the tool is the dependable place to
stop that. Tests in `tests/test_mini_files.py`.

Also today, ImageGen (its own repo; cfae5f8): Cloudflare FLUX.2 [klein] first (keeps `--ar`), SDXL on AI Horde within
the free kudos budget, the whole chain stops at 200 s (the agent waits 240), and the enhancer no longer cuts prompts
off at 140 tokens.

## 2026-10-27 (night): the chat can look at the Mini's files and show media (step 1 of fuller computer access)

Alex: "I want the assistant to be able to display images or mp4, or wave files etc in chat if i ask for them", and
"I dont want the agent to be doing things on the computer autonomously when im not able to watch it".
- **The tool:** `mini_files` (`core/mini_files.py`, files group) finds, lists, reads and shows files on the Mini.
  Roots: the user folder and `C:\Projects` (`MINI_FILE_ROOTS`).
- **Showing:** `show` gives a line the reply copies. An image is shown inline; video, audio or PDF is a card DeskAI
  (build 87) downloads with the token and opens in the phone's own player. Served by `GET /api/files/m/{ref}/{name}`
  (`interfaces/hud/files_api.py`), which checks the path again.
- **Don't undo:** it is read only. Off limits: secrets (`.env`, keys, databases, secret/token/password names),
  AppData and browser data, the agent's own `data/` and `secrets/`, and anything outside the roots. The off-limits
  rules apply below the root, so a root inside a temp folder still works.
- **Step 2 (not built):** changes to files and commands run only from Alex's own words, on his tap, while the app is
  open, with live steps and undo.

## 2026-10-27 (evening): background audit fixes; proactive timing by presence; fewer background calls

From the 2026-10-27 background audit (kept private), Alex's go-ahead. Also his direction: no regular
sleep pattern, so quiet hours don't matter to him. Details are in [PROACTIVE.md](PROACTIVE.md).

**Timing and delivery:**
- **By presence, not the clock:** quiet hours off by default. The brief ("auto") is written ahead and sent when he
  shows up. Non-urgent messages wait in `proactive_outbox` and go one at a time, 45 min apart. Opening the app runs the
  cycle at once (`events.OWNER_ARRIVED`). **Don't undo:** `deliver()` is the one gate for every sender.
- **Away shift open loops** use the cycle's budget and question rules (the 00:47 question). A held question counts
  as the unanswered one.
- **Offline notice** after a heartbeat gap of 1 h or more, with the scheduled runs missed (the Oct 17-22 power cut was
  silent).
- **Reminder schedules no longer make "coming up" notices:** they push by themselves, and the same notice went
  twice.

**Fewer background calls** (Alex: "doing background calls way too often"; measured):
- **The cycle's curious question** was written every 30 minutes (~48 calls, ~190K tokens a day), then dropped as a
  repeat. Now every 6 h, or 24 h after a dropped one (`_question_gen_due`).
- **Away-shift generators** back off the same way.
- **Interests:** novelty by link, at most every 12 h, never the owner's private matters or the research worker's topic.
- **YouTube creators:** free feeds (`core/feeds.py`) instead of web searches.
- **Open-loop research:** ×2 back-off up to weekly.
- **The background helper** (`look_into`, this morning) fired 4 times in 10 minutes (23 calls) and named folders no
  tool had shown. Now: one at a time, 10 min apart, 5 rounds, never a path it didn't see. The promise guard reads only
  a reply's closing words.

**New for the chat:**
- `computer_screenshots` shows a computer run's step screenshots in the chat (`GET
  /api/computer/runs/{run}/screenshots/{name}`).
- `proactive_settings` follows YouTube channels.

**Memory and upkeep:**
- A full duplicate pass weekly (`consolidate(force=True)`).
- The extract prompt: no changing counts or versions, the person's name rather than "the user".
- `_has_background_record` reads `proactive_candidates`; it used `candidate_pool`, which doesn't exist, behind a
  bare except.
- The memory model's "not answering" warning waits 10 min after a start.

## 2026-10-27 (later): HTTPS over Tailscale (S4)

- `tailscale serve --bg 8080` on the Mini: `https://mini.tailnet.example`, tailnet only (not Funnel), with
  the tailnet's own Let's Encrypt certificate. DeskAI tries an https address first, so the token stops crossing the
  home Wi-Fi in plain text whenever the phone's Tailscale is on.
- **Don't undo:** `auth.FORWARDING_HEADERS` includes Tailscale's headers. Serve relays every tailnet device from
  127.0.0.1, and without them they'd get this PC's no-token trust (`test_robustness`).

## 2026-10-27 (afternoon): audit fixes: a third of the chat prompt gone, passwords kept from models, one answer per message

From [AUDIT_2026-10-27.md](AUDIT_2026-10-27.md), Alex's go-ahead:
- **T1/T2, prompt diet:**
  - The persona is 1,696 tokens (was 3,280). Each tool group's how-to moved to `GROUP_GUIDES`
    (`core/assistant_tools.py`), sent in the reply's context note only with that group, and by `more_tools` when it
    loads one.
  - `more_tools` is 250 tokens (was 450: short `GROUP_ABOUT` labels).
  - `connect_google` left core for mail, calendar and drive.
  - About 1,700–1,900 fewer tokens per call, before the up-to-190 tokens of a group guide.
  - **Don't undo:** `test_the_persona_stays_small` (≤ 2,000) and the guide tests.
- **T3:** `GEMINI_REASONING` (`core/llm.py`) sends `reasoning_effort: "low"` to 3.8 and 3.6 Flash. Unset they think
  at "medium", and 3.8 Flash timed out on every chat attempt that week.
- **T4:** an empty answer right after a tool round gets one nudge on the same model before moving on. Gemini 3.x
  does this; it cost a 20 s Flash timeout twice on 10-26.
- **R2:** a failed task's `last_error` leads with the run's own last line (`daemon._finalize_task_completion`), so
  `list_tasks` shows the real reason. task-231's said only "exit code: 1", and the chat invented a cause.
- **R3:** `PROMISE_BACKED_BY` includes watches, schedules and the card tools ("I'll check every 5 minutes and let you
  know" is the watch talking).
- **R1:** `/api/chat` answers each `turn_id` once (`_turns` in `interfaces/hud/chat.py`). A repeat waits for the
  first run and gets its answer.
- **S2:** a password in a chat message is caught before any model (`holds_password`, `_password_reply`; refine too).
  It's never stored; the reply points to the Vault. "The password is going to be typed in" still goes through.
- **S6:** no `Access-Control-Allow-Origin: *` on chat, refine or media routes. **S7:** the vault API saves passwords
  exactly as given (no `strip`).
- **R5:** a computer task's card ends with "Screenshots: N, in <run folder>" (`computer_use.describe`).
- `/status` counts facts with a COUNT query.
- AgentComputerUse (same day): a password the owner types in the app is kept in memory until the run ends. It's
  saved only if the run finished and the owner taps **Save**, for the exact host it was typed on. This replaces
  `175063a`'s auto-save, which saved typos, guessed the domain and user, and hid failures.

## 2026-10-27: the chat keeps its promises (background helper); computer runs survive a two-object reply

From the 2026-10-26 evening chat (Gemini API key screenshots):
- **Broken promises.** Flash-Lite answered "I'll pull those files for you right now. One moment!", "Let me fix that
  right now ... Hang tight" and "Let's do a quick search in the background", and nothing ran: a reply ends the turn.
  It had also guessed where the screenshots were (they're in AgentComputerUse's `data/runs/<run id>/step-NN.jpg`).
  Alex: "I would be fine with the chat agent doing something in the background ... so the chat stays responsive."
  New core tool **`look_into`** (`core/follow_up.py`): a background helper with read-only tools (`READ_ONLY_TOOLS`,
  up to 8 rounds, 2 at once, on a thread, not the task queue) answers later through `Proactive.deliver` (a chat
  message with a push). **`keep_promise`** (`core/memory.py`) starts it when a reply matches `PROMISE` and started
  nothing, and adds a note saying so. The persona and `FINAL_REMINDER` say a reply ends the turn.
  **Don't undo:** the helper never gets a tool that proposes, starts, posts or changes anything.
- **task-231 died on `JSONDecodeError: Extra data`** (AgentComputerUse `brain.parse_json`): the model sent two JSON
  objects back to back and both `json.loads` and the greedy `{.*}` fallback raised. It now reads the first object
  (`raw_decode`) and raises `BrainError` when there's none.

## 2026-10-16: the tool router moves to Llama 3.3 on Cloudflare; an unknown group hands over to the keywords

- **Router models** (`ROUTER_MODELS` in `core/assistant_tools.py`): Cloudflare Llama 3.3 70B
  (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`), then Groq gpt-oss-120b. Was 3.5 Flash-Lite first, the chat's own
  first model, so every message spent two of its requests and per-minute slots. Measured on the router prompt with
  20 typical messages: Llama 3.3 20/20, median 0.67 s, ~12 neurons a call; gpt-oss-120b 19/20, 0.50 s; Flash-Lite
  15/20 (four 503s, one timeout), median 3.9 s. **Don't undo:** keep the chat's first model out of the router
  (`test_router_uses_its_own_models_not_the_chats_first`). **Llama 3.3 is not a chat model here:** with tools it
  ignored a tool's result and refused a joke; in refine it turned "push it" (git) into posting a video.
- **Unknown group names** (e.g. `"email"`): the router's answer is discarded and the keyword picker decides.
  Before, the name was dropped and the reply got core tools only.
- **`chat_with_tools` timeout 25 → 20 s** per call: it applies per model and per tool round, so a hung model cost
  each reply 25 s before the next was tried.
- The `.env` line `CLOUDFLFARE_API_CHAT_KEY` (sic) is a second token for the **same** Cloudflare account: the free
  10,000 neurons a day are per account, so it adds no allowance. The code uses `CLOUDFLARE_API_TOKEN` only.

## 2026-10-14 (afternoon): the router's 5 s timeout no longer benches the chat's first model

From Alex's `worker.log` (08:43-10:10): Google answered HTTP 503 on 3.8 Flash, 3.6 Flash and 3.1 Flash-Lite and
500 on Gemma 4 all morning, and Groq can't take a chat request (~10-11K tokens, over its 8K cap). Three times the
tool router's call to 3.5 Flash-Lite missed its **5 s** deadline, and `_complete` benched Flash-Lite for **180 s**
(`TIMEOUT_COOLDOWN`) on the CloudLLM the chat shares. The replies right after skipped Flash-Lite, the one Gemini
model still answering, and went down the failing list. Now a timeout shorter than `BENCH_MIN_TIMEOUT` (15 s) is
logged and counted as a failure but doesn't bench the route (`CloudLLM._timed_out`). **Don't undo:** a caller's
deliberately short deadline must not take a model away from the chat. Test:
`test_a_short_deadline_does_not_bench_the_chat_model`. Google's 503s themselves are on Google's side.

## 2026-10-14 (afternoon): `scripts/mini_diagnose.py`, why calls aren't going through

Alex: "So many calls not going through." A cloud session can't reach the Mini (Tailscale only; Hark's access
stays removed), so `scripts/mini_diagnose.py` gathers what's needed in one read-only run from the laptop: the
status, failed AI calls by purpose/model/reason (`llm_failures`), answered calls with timings, Gemini calls today
against the free limits, and the last 24 h of warnings, errors and tracebacks from `logs/worker.log`, repeats
counted. It never opens `.env`.

## 2026-10-14 (afternoon): `scripts/router_report.py`, is the router worth its call?

Alex asked for the numbers before changing the router or the 25 s timeout. `scripts/router_report.py` (read-only,
run on the Mini through `scripts/mini.py py`) reports misses (`more_tools` calls), keyword fallbacks, the router's
timings and failures, Flash-Lite calls per day and chat timeouts, with a short verdict ([TOOLS.md](TOOLS.md)).
To tell router picks from fallbacks, `tool_routing.jsonl` lines now carry `picker` (`router`/`keywords`, from
`AssistantTools.picked_by`). Tests: `tests/test_router_report.py`, `test_the_routing_log_says_who_picked_the_groups`.
Plan: let it run 3-5 days after this deploys, then read the report before touching the router.

## 2026-10-14: Tool router fixed (core tools always go); refine keeps sign-ins away from models

Found in a review of the last day's commits, with the live chat on the Mini as evidence.

- **The router (`0007ff7`, `core/assistant_tools.py`) could send a reply no tools at all.** Last night "oh I tapped
  it but it failed" (about task-196's go-live) got none, and with no `NO_TOOLS_NOTE` either, the chat said "let me
  check the logs", then **invented** "task-196's merge failed because of uncommitted changes in `core/`", and later
  claimed the merge was done (the honesty footer caught that one three times). Now `tool_specs` always sends
  **core** + the router's groups + `more_tools` (and `memory_check` while the weekly check waits). The router
  sees the previous reply every time, can't pick `core`/`memory_check`, and its prompt lists `files` and
  `computer` (missing before). `memory.py` adds `NO_TOOLS_NOTE` if a reply ever has no tools. **Don't undo:** a
  router verdict must never remove the core tools. Tests: `tests/test_tool_router.py` (task-196 regression).
- **`POST /api/chat/refine`** sent the draft to a model even when it held a Google sign-in address or Zoho
  details (the chat intercepts those before any model). Now it hands such a draft back untouched (`model:
  "none"`). It uses `BACKGROUND_MODELS` (it fell through to the Flash models, whose ~20 a day are for chat), logs
  as `refine` instead of `chat`, and its fallback `CloudLLM()` no longer gets the database as its Groq key.
- Docs: the router is now in [TOOLS.md](TOOLS.md), `CHAT_TOOL_ROUTER` in [AGENT.md](AGENT.md), and
  [CHAT_TOKEN_OPTIMIZATION_PLAN.md](CHAT_TOKEN_OPTIMIZATION_PLAN.md) is linked from AI_HANDOFF (reference only).
- Left as is: `chat_with_tools`' per-call timeout went from 15 s to 25 s in `0007ff7` (a hung model now costs
  25 s before the next is tried). Worth measuring before changing back.

## 2026-10-14: Chat Prompt Refine & Enhance endpoints (`POST /api/chat/refine`)

- **Chat prompt refine endpoint (`interfaces/hud/chat.py`):** Added `POST /api/chat/refine` and alias `POST /api/chat/enhance` with request/response models in `interfaces/hud/models.py`.
- **CloudLLM fallback:** Refines the user's prompt into a concise, actionable instruction, returning it unchanged when no model answers. (Corrected the same day: it walked the full chat list, Flash included, and could send sign-in details to a model; see the entry above.)
- **Client pairing:** Powers the ✨ Enhance button in DeskAI Build 80 (`v2.23.0`).
- **Tests & Contract:** Added `test_api_chat_refine_and_enhance` in `tests/test_api_chat.py`. Contract verified in `tests/test_api_contract.py` and documented in `docs/API.md`.

## 2026-10-13: Proactiveness Phase 1 – Candidate pool & Away shift (docs/PROACTIVE_RESEARCH.md)

Implementation of Phase 1 from `docs/PROACTIVE_RESEARCH.md` across 16 steps with zero code breaks and full gate verification:
- **Candidate Pool (`core/candidates.py`):** Structured storage (`proactive_candidates`) for candidate ideas from signals, open loops, curiosity, patterns, and research. Short subjects compared against all past questions and Alex's recent messages via Qwen3 embedding model (repeat threshold ≥ 0.64; discussion threshold ≥ 0.65 for 6+ words or matching names). Background AI ranking (`RANK_PROMPT`) sets score combined with novelty and source-variety penalties.
- **Routing & Lull (`core/proactive.py`):** Candidates routed by urgency (`now`, `today`, `whenever`). "Whenever" candidates held during active conversations until a 10-minute lull. Freshness check before sending drops candidates if Alex discussed the topic or backing memory lapsed, with zero additional AI calls. "Today" items folded into morning brief.
- **Silence Check-in (`core/proactive.py`):** After 6 hours of silence from Alex (outside quiet hours), sends the top-scored candidate. Exponential backoff to 12h, then 24h when ignored; any incoming message from Alex resets the backoff.
- **Implicit Feedback (`core/candidates.py`, `core/proactive.py`):** Replies within 6 hours mark candidates answered; otherwise ignored. Per-source reply rates adjust score thresholds. Two ignored questions in a row halve questions_per_day until Alex replies.
- **Away Shift Worker (`core/away_shift.py`, `core/daemon.py`):** Dedicated background worker thread runs sessions when the app has been closed for 2 hours (monitored via `Presence` in `core/events.py`). Executes open loops research on 48h/24h schedule, research opportunities worker, subject backfill, and consolidation. Capped at 15 web searches/day; emits live SSE `event: activity` and updates `GET /api/activity`.
- **Chat Awareness & Controls (`core/assistant_tools.py`, `interfaces/hud/chat.py`):** System prompt reflects background worker state; `background_status` tool queries recent steps and findings. Extended `proactive_settings` chat tool manages new defaults (10 daily cap, 4 questions/day), per-kind switches (`open_loops`, `curiosity`, `research`, `patterns`), and curated `interests` / `exclusions`.
- **Guardrails & Grounding (`core/proactive.py`):** Prompts and checks prevent guilt/retention messaging ("don't leave", "we miss you"), ungrounded signals, sensitive topics (health, finance, family unless raised this week), and personal details in search queries.
- **Transparency, Feedback & Inspection (`interfaces/hud/companion.py`):** `GET /api/inbox` returns `why` transparently. `POST /api/inbox/{id}/feedback` records reactions (`thumbs_up`, `thumbs_down`, `less_like_this`), with negative feedback auto-excluding the topic. `GET /api/proactive/candidates` enables full pool inspection.
- **Weekly Numbers (`core/growth_review.py`, `core/weekly_report.py`):** Sunday growth review and weekly report include candidate pool metrics: sent, answered, negative feedback, and breakdown of reasons not sent.
- **Tests & Gates:** All 542 tests passing; G-unit, G-import, G-compat, and G-full verified at each step.

## 2026-10-13 (evening): open loops, researched; curious questions (by the laptop Claude session)

Alex: "It has a memory saved that says Alex is waiting on a decision on his permit. I have since got this decision",
then "seeing a memory that is perhaps researchable, should be searched", "this should be a part of the
proactiveness stuff", and "the proactiveness is kind of mundane ... i want the agent to be naturally curious".
What was found: the agent itself had it right (#38 "permit granted Oct 5" replaced #35 on Oct 10; the weekly check
faded #36), but `GET /api/memory` sent every replaced and faded fact in `facts`, and DeskAI (no status field) showed
all five "waiting on the permit" versions as current. The profile, rebuilt only from current facts, said "waiting" from
Oct 8 until 12:16 on Oct 10. And nothing went back to see whether a "waiting for X" fact had settled.
- **core/open_loops.py (new), a step of the proactive cycle:** tags facts that wait on something (waiting_on,
  check_after, mail words, and up to two web queries when the outcome would be public); closes loops settled
  elsewhere; gathers evidence (newer facts, owner messages and events, mail, the agent's own web search with the
  page text Tavily returns, cut to the parts about the matter); replaces the fact only with a quote found in that
  evidence and the second opinion's agreement, then tells Alex. "News" (something happened, outcome not shown) is
  told once with the link and asked about; stale loops past check_after are asked about once (again after 14 days).
  It shares the proactive budget: the daily cap and the question rules. No site-specific code: Alex didn't want a
  client for one website; the permit page turned up as a search result.
  Dry run on the Mini (its search keys) with a copy of its memory where the decision was unknown (the "granted" fact
  and chat removed): it searched the owner's name plus the permit number, read the city's permit portal ("DECISION
  approving the application", Oct 5) and replaced #35 with "Alex's permit ... was
  approved by the city on October 5, 2026" (evidence: the quote and the link). Found on the way: a permit number
  alone matched another city's permit (queries lead with names now); the second opinion was shown only an item's
  first 900 characters, the page header, and refused (it now sees the passage around the quote).
- **core/proactive.py, curiosity:** the question step no longer asks "to get to know them better"; it picks one
  specific thing Alex said or cares about and wonders about it (follow-up, why, connection, wonder), quoting what
  sparked it (checked against what he said), sometimes looks it up and shares one thing he probably doesn't know
  (with the link). Guards: no repeat question, no topic from the last 8, no name from the last 3 questions, nothing
  assumed that he hasn't said, and pending matters are left to open loops. Budget unchanged (2 a day, never while
  one is unanswered).
- **core/web_tools.py:** `search_results()` (the web_search providers, results as data; `raw=True` adds Tavily's
  page text).
- **`GET /api/memory`:** `facts` is current facts only; replaced and faded ones are in `past`; plus `open_loops` (an
  empty list if they can't be read, so the Memory screen still opens).
  Don't undo: the app has no status field, so anything in `facts` reads as true now.
- **core/memory.py:** conversation summaries in context carry "Each was true as of its dates; where one disagrees
  with your memories, the newer wins" (Sept's summary still says "awaiting permit response").
- **core/proactive.py:** curiosity's name check covers every question ever asked, not the last 3 (Styropyro, asked
  Oct 11, came up again in the samples).
- **docs/PROACTIVE_RESEARCH.md (new):** the research (OpenAI, Google, Microsoft, Apple, Amazon, Meta, Gmail; CHI
  and ICLR papers), the plan (away shift, candidate pool, silence check-in, the background icon) and the
  step-by-step checklist with its gates. Phase 0 is this entry.
- tests: test_open_loops.py (20), test_proactive.py (+9).

## 2026-10-13: Automated AI research worker for free credits, previews, free tiers & hackathons

- **Research Worker (`core/research_worker.py`):** An automated background worker that periodically
  searches for free AI credits, developer previews, free tiers, and free AI hackathons fitting Alex's
  building work (AlwaysOnAgent, Example Site, DeskAI, AgentWork/ComputerUse).
- **Zero-cost alignment:** Hard constraint to zero-card / free-tier / builder-grant opportunities. Evaluates
  each opportunity for alignment with Alex's infrastructure (CPU-only mini PC, free Gemini/Groq/Cloudflare/Mistral routes).
- **Digests to Canvas & Memory:** Compiles findings into an HTML document draft in Canvas (`AI Opportunities: <date>`)
  and records the event into long-term Memory (`mem_messages`).
- **Proactive notification:** Sends a proactive notice with the Canvas draft card to DeskAI so Alex can review
  and open the draft on his phone.
- **Daemon & manual triggers:** Runs periodically in `AutonomousDaemon` heartbeat cycle (`AI_RESEARCH_INTERVAL_DAYS`,
  default 3 days; avoids night quiet hours) and on-demand via chat tool `research_ai_opportunities` or endpoints
  `GET /api/research/opportunities` and `POST /api/research/run`.

---

## 2026-10-13 (later): "apply task-189" couldn't be applied, and the chat said it had (by the laptop Claude session)

What happened (chat of 2026-10-13 00:58-01:14): Alex said "apply task-189" twice. `offer_go_live` ran both
times (log) and the reply carried the task, but DeskAI folds finished tasks into one-line rows (build 71), so the
⚡ Apply update chip never showed; a tap opened the details, which have no chips. The reply also ended "There's no
card this time" (`offer_go_live` wasn't in the card check's list). The model then said "Yes, it worked! ...
applied and merged into live" (it ran only get_system_status), passed a web search off as the new worker, and
claimed a "task-190" that doesn't exist. Nothing was applied (no restart; task-189 is still waiting).
- **core/memory.py `unbacked_claim_note`:** `offer_go_live`, `start_project_job` and `render_video` count as
  showing a card; a reply naming a task id that doesn't exist gets "There's no task-190: ..."; a reply saying an
  update was applied / merged / is live gets `LIVE_NOTE` unless the go-live record (`core/self_update.status`)
  says so. Don't undo: a go-live claim is checked against that record, not trusted.
- **DeskAI build (pending ship):** the newest chat message about a finished task that still awaits a tap (a primary
  `post` chip: Apply update, Push live, Try again) keeps its full card with the chips, even with tasks hidden.
- task-189 (the research worker, 834 lines on `agent/task-189`, 436 tests passed) still needs a review and Alex's
  tap; nothing about it is live.

## 2026-10-13: Message in a bottle scene, scene rotation, six voices, /render chest|bottle (by the laptop Claude session)

- **TiktokVideos** gained a second 3D scene, the message in a bottle (open sea, it washes up on a moonlit beach,
  the cork pops, the scroll unrolls), built with Alex over many previews; realistic assets only (CC0 Poly Haven /
  ambientCG scans, NASA's moon, a real photo of drops on glass). `MEDIA_SCENE=rotate` is its new default: the chest
  and the bottle take turns (never three in a row). The card flip is only the fallback. Don't undo: the scene's
  look was approved beat by beat (the dissolve, the camera following the bottle, the curl at the paper's edge, the
  spray); spiral-rolled corners were tried and rejected.
- **Six voice casts** (TiktokVideos `VOICE_CASTS`, ElevenLabs + Edge), none reused within four videos.
- **Choosing a scene:** `POST /api/trigger_media` takes `scene` (`chest`, `bottle`, `card`; 400 if unknown), kept on
  the task's metadata and passed on as `--scene`; the chat's `render_video` tool takes `scene` when the user named
  one; DeskAI's `/render chest|bottle <quote>` (and `/video`, `/tiktok` with a scene word first) send it.
  `core/actions.py` `video_scene()` maps the names. Video task titles name the scene (no more "3D Card Flip").
- **To do on the Mini:** TiktokVideos' own `.env` may pin `MEDIA_SCENE=treasure_chest` (the laptop's does): set
  `MEDIA_SCENE=rotate` or remove the line for the rotation to happen. The bottle's assets (~25 MB) download on its
  first render.
- **Found, not fixed:** the free Edge voices answer 403 (edge-tts 7.2.7 and 7.2.8 alike, 2026-10-13), so if
  ElevenLabs runs out a render fails instead of falling back.

---

## 2026-10-11 (later): Retasks, /retask, slash commands, the DeskAI redesign (by the laptop Claude session)

- **Retasks** (AgentComputerUse retasks.py + replay.py; agent `GET/DELETE /api/computer/retasks`,
  `POST /api/computer/retasks/{id}/run`): a computer run that went clean is saved, and the SAME task (same wording)
  asked again is repeated without the AI. The owner's hard lines, don't undo: answers are never replayed (a question
  repeats up to the reading point, then the model reads the screen now); written text is never replayed (written
  fresh); only clean runs are saved (no refusal, failure, retype, owner's help, sign-in or password); no reading moves
  are stored (scrolls, waits, paging keys); every step goes through check/act; off course hands over to the AI, and a
  retask is dropped after 2. A repeated task's card says "(Repeated an earlier run: N AI requests.)" once.
- **Narration lines carry `level` "retask" and a `kind`** (open, type, click, save, plan) through the agent's
  `activity_log` (docs/API.md), so DeskAI colors "What it did".
- **DeskAI builds 67-74:** a Retasks screen; `/retask` (pick a retask above the chat box and run it); slash
  commands pop up as you type `/`, and `/?` or `/help` open a grouped list (one source: SlashCommands.kt); the
  owner's chosen redesign, "Circuit": terminal green with deep cerulean, a hex circuit texture, black lightning;
  task cards are cerulean-indigo; tasks fold in chat (rows, strips, a Tasks switch); the menu is grouped with live
  details; the Standby, gear and three-dot buttons are removed; task details have stat tiles and a colored log.
  Details in DeskAI's ALIGNMENT_DIALOG.md.

---

## 2026-10-11: Learned recipes are know-how, never a copy of the job; computer runs hardened; loop.py split (by the laptop Claude session)

- **Recipes (core/procedures.py):** the owner: "a task isn't copied step by step... I don't want a repeat Notepad
  call wanting to read a grocery list". A learned recipe (from a tool or a retried task) is now refused when it
  repeats the job's own details (a file name, quoted text or a 3+ digit number that's in what happened) or is a
  script of more than 3 steps (`copy_problem`, checked before the second opinion). The distil prompt and the
  second opinion say the same. The owner's own recipes aren't checked. Don't undo: a copied recipe would be
  replayed into every later job of its area. Computer tasks still get no recipes (`_with_memory_briefing`).
- **AgentComputerUse, fresh batches 3-10 on the Mini** (each failure traced from its run log, then fixed in
  general): Windows-form paths for the Open window; a text file with no app opens in Notepad (or the app the task
  names); a minimized window is never "the screen"; no chained answer to a question (it was a guess); the repeat
  guard refuses only the same claim; one Enter per line break; no retyping what a field already holds exactly (OCR
  misread the cursor); a task's done needs no summary line; **monitor on or off, runs work** (the owner: "it needs
  to be able to do both"; `--need-monitor` to stop instead). Batches 8-10: 11/11 clean, monitor off.
- **AgentComputerUse loop.py split** (1,854 -> 828 lines, no behaviour change): rules.py, prompt.py, goals.py,
  files.py, verify.py, owner.py, chain.py (mixins of `Loop`). Tests patch helpers where they live (`goals.X`).

---

## 2026-10-11: File-job baseline tests; a stray window no longer derails a run; whole task titles (by the laptop Claude session)

- **Baseline (today's tool, before building file actions):** task-102 "open Story1.txt and tell me its first sentence"
  failed (clicked Open four times, then closed Notepad before reading); task-103 "download Form W-9 from irs.gov" was
  cancelled by the owner: a Pinterest sign-in page left in the owner's Edge (from the 10-10 Pinterest sign-in) sat in
  front, the tool looked at it instead of its own Edge, every step was refused, and a "Continue with Google" box on
  it was answered as the task's pop-up (the owner's answer opened a Google sign-in tab).
- **Fixed, AgentComputerUse `a3c5b14` (188 tests pass on the Mini):** `_current_window` (its docstring said "the
  task's window if it's still in front", the code always took the window in front): once the task has a window of
  its own, another window in front is never looked at or acted in; the task's own comes back to the front (narrated).
  `unstick` answers pop-ups only in the task's own windows. The owner: "a tab should not stop a run".
- **Whole task titles** (`actions.task_title`): computer tasks were cut at 70 characters, `/task` at 50, app tasks at
  60, mid-word ("...into my Downloads folde"). Now the whole request up to 200, else cut at a word with "…".
- **Memory check** reads the week with dates (`b080184`): the first real check turned "it's his birthday today"
  (said Oct 7) into "October 5th".
- **Root cause of the IRS failures (task-104/105):** the tool runs in the background (the agent starts it), and
  Windows only lets the process that sent the last input take the foreground, so its own Edge opened BEHIND the
  owner's maximized Pinterest window; `SetForegroundWindow` (with `AttachThreadInput`) was refused silently, and
  the fallback clicked "its title bar" blind, which landed on the owner's Pinterest tab, every step. Failures
  weren't logged, so it looked like nothing happened. (`2015160`, taking only the tool's own Edge window by
  profile, is kept but wasn't the cause: Edge names every profile "Personal".)
- **Fixed, AgentComputerUse `37863fd` (192 tests pass on the Mini):** every real run minimizes all windows first
  (the owner's rule: `actions.minimize_all`, the taskbar's "minimize all"); `_ask_for_foreground` sends one F24
  key first (the input Windows wants), raises the window (topmost on/off) and asks; the title-bar click only
  happens when `WindowFromPoint` shows the task's own window there; a failed step is logged (`failed` event) and
  narrated. `tests/conftest.py` makes sure no test minimizes a real machine's windows.
- **Verified live (task-106):** W-9 downloaded to `Downloads\fw9.pdf` (140 KB) in 54 s, 7 requests, with the
  owner's Pinterest tab still open; it used `save_as` for the PDF's Save dialog.
- Next: `open_file`, `download` and `attach_file` as tool actions (like `save_as`).

---

## 2026-10-10: The pin maker (by the laptop Claude session; the owner's ask)

- **The owner:** "a sophisticated pin maker. And ensure it creates everything we need and shows the website link
  well." (Pins otherwise run out ~Oct 23.)
- **SocialPostEngine `pinmaker.py` (`23bee9d`):** pins from the website's own data (`data/listicles.json`,
  `data/messages.json`, read from GitHub): the 225 listicle messages had no pins yet. Each pin: a 1000x1500 image in
  the site's style (Caveat/Outfit/Space Grotesk in `fonts/`, the page's color, the message on a frosted card,
  centered, balanced line breaks) with the page's name and a **example-site.com** button band at the bottom;
  title (guide + section), description (message, guide, "See all N", hashtags), alt text, board, and the page link
  (checked live, utm-tagged). Drafts first (`make`, `drafts`, `approve`, `reject`); approved pins live in
  `data/made_pins/` and the queue reads them after `pins/pins.json`.
- **Agent:** chat tools `make_pins` / `approve_pins` / `reject_pins` (group stats; also loaded by "approve",
  "reject", "drafts"); the reply shows the preview sheet (`/images/pin-drafts-*.jpg`) and a numbered list.
  **Top-up:** once a day between 10:00 and 20:00, when fewer than 8 pins wait and no drafts do, the agent makes 8
  drafts and sends the preview as a suggestion (`social_posts.run_topup`, the daemon's PinsTopUp thread).

- **First batch (8 drafts) approved** by the owner. Their "approve all" first got a Notepad computer-task card:
  the drafts message had been sent with `memory=None` (not in the conversation) and the recent tasks were
  Notepad ones. Approved by hand (33 pins waiting); now every chat reply's context says when drafts wait
  (`social_posts.chat_note`, read from the tool's drafts file). A message that expects a reply must go
  through a Proactive with memory (the daemon's does).
**Don't undo:** nothing reaches the posting queue without the owner's approve.

---

## 2026-10-10: The chat said it changed a watch when it hadn't; Pinterest Standard access found (by the laptop Claude session)

- **What the owner saw:** asked on Oct 7 and Oct 10 to keep only Pinterest's upgrade email; both times the reply
  said it had ("I've muted those completely") and nothing changed (no tool could change a watch; on Oct 10 the
  message had no "watch" in it, so no watch tools were loaded at all). The third ask (with "watches") really
  replaced it, with a watch needing all of "trial upgraded access api", which Pinterest's real email wouldn't match.
- **Fixed:** `Watches.change` + chat tool `change_watch` (narrow in place; what matches now is marked seen);
  the watches group also loads when a message names what an active watch matches (`_mentions_a_watch`), and on
  "mute", "ignore", "spam", "only show/tell/alert"; `core/memory.py` `unbacked_claim_note`: a reply that claims it
  changed, muted or stopped a watch/alert with no watch, schedule or proactive tool called gets an honest note
  appended; the persona says never to claim it. Tests in `tests/test_watches.py`.
- **Pinterest:** the email they were waiting for came on **Oct 7, 14:33** (pinbot@account.pinterest.com: "The Vibe
  Check Project Auto-Poster has been approved for Standard access"), lost among recommendation alerts. All watches
  are stopped (the owner: none needed). SocialPostEngine still posts to the sandbox until `PINTEREST_ENV=production`
  is set on the Mini (the owner's call).
- **Pinterest live (later that night, the owner's go):** the password change of Sep 29 had revoked the production
  sign-in ("Session is no longer valid"; a forced refresh was refused). The owner reset the password (the reset
  email read from Zoho), signed in once through Take over (`socialpostengine.py --env production auth`, run in the
  desktop session; account theexample-site, refresh good until 2026-12-09). SocialPostEngine's `.env` now has
  `PINTEREST_ENV=production`. First live pin: task-101, "Whatever Happens, Ice Cream After" (Encouraging Words).
  Schedule #6 "Pinterest pins (twice daily)": 1 pin at 12:00 and 21:00. 25 pins left: about 12 days (to ~Oct 23).
  **Note:** `run_in_session.ps1 -Command` breaks on a quoted program path plus other quotes (cmd /c quote rules):
  pass the program unquoted when its path has no spaces.

**Don't undo:** a reply must never claim a change no tool made.

---

## 2026-10-10: task-096 ("save it as Story1.txt to the desktop") claimed done but wasn't; fixed (by the laptop Claude session)

- **What happened** (run `20261010-193302`, 18 steps, 74 s): the model chained ctrl+s, type "Story1.txt", enter,
  alt+f4. The Save dialog wasn't on the list the chain was planned from, so the file name went into the dialog's
  **Search box**, Enter searched, and alt+f4 closed the **Save dialog**, not Notepad. After two more tries it saved
  under Notepad's default name, "Once upon a time in a small, quiet.txt", in the dialog's last folder
  (`AgentComputerUse\data\demo`), and the double-check accepted "saved as Story1.txt to the desktop".
- **The owner also saw the story typed, deleted and typed again:** typing is read back to confirm it, 0.3 s after
  the last key; a 983-character story was still landing, looked wrong, and was cleared and retyped.
- **Fixed in AgentComputerUse `5bd6277` (174 tests pass on the Mini):** a dialog (class `#32770`) that appears
  mid-chain stops the chain so the model looks at it first; the prompt says how to save (end the chain at ctrl+s,
  full path into "File name", never Search; replacing a file is needs_approval) with this machine's real Desktop
  path; `saved_file_problem` checks on disk that a "save as NAME" goal's file was written during the run where the
  goal says, before the double-check; typing is re-read until it stops growing (up to 4 s) before it's judged;
  narration shows file names ("Story1.txt" was hidden as if private).
- Not run live again yet: that waits for the owner's go.
- **Re-run (task-097, owner's go) went wrong differently:** it saved Story1.txt correctly at step 2, didn't notice,
  and chained "type the path into 59" (a field that wasn't there); the "only text field" fallback put the path into
  Notepad's page, replacing the story; the owner approved replacing the file; a refused "done" was claimed again
  with nothing done and a second check passed it. Desktop\Story1.txt held only the path.
- **Fixed in AgentComputerUse `b4b4036` (180 tests pass on the Mini):** the fallback field must be empty; a chained
  type never replaces a field's text with something that doesn't start the same; every step tells the model
  whether the goal's file is saved (`ON DISK`, words and start, read from the disk); "a 200 word story" must hold
  at least 75% of the words; the double-check sees the file; a refused done isn't re-judged until something is
  done; Notepad gets 1 s to settle after opening.
- **The owner saw each story typed, cleared and typed again in both runs** (both typing steps took twice as long
  as one pass), and nothing was logged. Now every read-back mismatch is logged (`retype` event: where the text
  differed, both sides), narrated as a warning, and photographed: `snap-NNN.jpg` before typing a long text, every
  250 characters, when all keys are sent, and before a clear. Cause not yet known: the next live run will show it.
- **Third run (task-099):** typed once (no retype; the 1 s settle after opening Notepad looks like the fix), but it
  asked to approve "overwrite Story1" with Notepad's default name still in File name, so the approval saved under
  that name; it then closed Notepad, the disk check refused, and the no-reopen rule left it stuck.
- **The real fix, AgentComputerUse `8b16124` (183 tests pass on the Mini): `save_as`.** The model no longer drives
  Save dialogs from a list of controls (three runs, three different wrong turns). It says
  `{"action": "save_as", "path": "<full path>"}` and the tool does ctrl+shift+s, types the path into File name,
  Enter, asks the owner before replacing a file (Confirm Save As → the approval clicks Yes), and checks the file on
  disk. Not chainable, so an approval always goes through the normal flow. Guards from `45cb041` stay: Save can't
  be clicked or approved until File name holds the goal's name; the app can't be closed before the file is saved.
  **Don't undo:** common, fiddly operations belong in the tool as one action, not in the model's clicks.
- **Fourth run (task-100): done.** Typed once, `save_as` first try, the owner approved replacing the old file. Then
  (`86ed4d2`) lengths are approximate ("a 200 word story" passes from 70%; the owner: "it doesn't have to be
  perfect"), and (`4e0902c`) the screenshots all through typing are gone (the owner: no constant screenshots);
  one is kept only when typing reads back wrong, before the retype. Needs-you screenshots are unchanged.
- **Storage stays small** (`650e660`, the owner: "I don't want the images adding up"): step screenshots are
  JPEGs (~5x smaller); run folders over 14 days old are deleted at each run (the newest 30 always kept);
  Needs-you request files over 14 days old too. Before: 55 runs = 104 MB in 5 days, never cleaned.

---

## 2026-10-10: Share a repo file or folder from the phone (by the laptop Claude session; the owner's ask)

- **The owner:** "share a file or a folder with another person or AI, where I don't have to copy a whole code and
  paste it".
- **Connectors** `github.download` / `github-download`: a file as itself; a folder (or a whole repo) as a zip (the
  folder's name on top) or one text file (each text file under `===== path =====`, for AI chats that can't open a
  zip). The tree comes from GitHub's recursive tree API, files are fetched 8 at a time; at most 400 files, 5 MB
  each, 40 MB in all, and what's left out is listed inside. Tried for real: `interfaces/hud` (17 files) in 6 s.
- **Agent** `GET /api/github/download?repo&path&format=zip|text|raw` (`interfaces/hud/github_api.py`): the file
  with its name; `X-Files`, `X-Left-Out`; the temp copy in `data/tmp/share/` is deleted once sent (`data/tmp/` is
  gitignored).
- **DeskAI build 64:** Repos has Share file / Share folder / Share repo beside Work on this; a folder asks "One text
  file" or "Zip"; Android's share menu sends it as an attachment (FileProvider, cache `share_out/`). Code files go
  as text/plain so AI chat apps take them.
- **DeskAI build 65:** Gemini isn't in Android's share menu (the owner), so the button is now Share / save: the same
  choice, then Share… or **Save to Downloads** (Downloads/DeskAI, Android 10+ without a permission), to attach from
  any app's file picker.

---

## 2026-10-10: Computer tasks narrate in plain words; a log on the task card (by the laptop Claude session; the owner's ask)

- **The owner:** "a spot that shows what the agent is actually doing... Clicking on start menu, typing Notepad,
  pressing enter, typing a joke... like if anyone else was controlling it they would understand", and "a log of this
  ... on the task card, so I can see the logs and any errors".
- **AgentComputerUse:** `activity.say()` turns each step into a sentence ("Opening Notepad", "Clicking “Start”",
  "Typing “Why did the golfer…”", "Pressing Ctrl+W", "… : not allowed (reason)"), with a level (info, ok, warn,
  error). `activity.json` keeps the last 60 as `log` (`GET /api/computer/takeover/activity` returns it), and the
  runner (`emit_run`) adds `say`/`level` to each progress event and **drops the raw typed text** before the event
  leaves the process. The loop now passes each step's target name, app, keys, url, window and direction.
- **Typed text is shown now** (it was kept off the banner on Oct 8), except secrets: hidden when the field's name
  looks like a password, PIN or code, or the text looks like one (`activity.secret_like`: one word mixing letters
  with digits or symbols, or 4+ digits; web addresses, file names and email addresses are shown).
- **Agent:** `_execute_computer` keeps the sentences and saves them as `metadata.activity_log` (last 150, plus how it
  ended; tool errors as an `error` line); tasks carry `activity_log`; the live progress line reads
  "Step 3 · Opening Notepad".
- **DeskAI build 63:** View only has a narration panel (bottom left: the goal and the newest 3 lines; tap for the
  whole log); task details have "What it did" with warnings amber and errors red, and Copy.

**Don't undo:** raw typed text never leaves AgentComputerUse's process except inside `say`'s sentence, where secrets
are hidden.

---

## 2026-10-10: Weekly memory check; memory cleaned up (by the laptop Claude session; the owner's ask)

- **Why:** the memory knew 24 facts and nothing about Example Site, the wecare@ inbox or the permit's
  number. Chat extraction only saves what the owner states in one message, so things learned through actions
  (connecting accounts, tasks) or said in passing were never saved.
- **New `core/memory_review.py`:** Saturdays from 11:00 (proactive thread, outside quiet hours) an AI proposes up
  to 6 missing facts and up to 4 out-of-date ones; the code drops any new fact whose quote isn't in the week's
  owner messages or events, and any retire that doesn't name an active fact. Sent as inbox kind `memory`.
- **Answering:** `GET/POST /api/memory/review` (DeskAI build 62: a Review card opens a checklist, everything ticked
  to start with) or chat: the `memory_check` tool, in its own tool group, offered only while a check waits.
- **Cleanup done by hand with the owner's OK:** fact #36 (work ready "once the permit is approved") faded;
  #38 now holds the permit's number and office; #19 says "Alex"; added #39 Example Site and
  #40 the wecare@ inbox; profile rebuilt.

**Don't undo:** nothing is saved or retired without the owner's answer; proposals without a quote found in the
week's messages or events never reach the owner.

---

## 2026-10-10: Local model off for memory chores (by the laptop Claude session; owner's go)

- `core/memory.py`: `MEMORY_LOCAL_MODE` defaults to `off` (was `shadow`). Judged blind on the 64 shadow pairs that
  differed, qwen3:4b-instruct lost 26-5 to the cloud and did harm 16 times to the cloud's 1. Details in IDEAS.md
  ("Parked", #10). This also frees ~30 s of Mini CPU per chat message.
- `scripts/eval_shadow.py` (Gemma 4 26B as a blind judge) stays for testing any future local model.

**Don't undo:** don't turn on `primary` for a local model without a shadow run judged as good as the cloud.

---

## 2026-10-10: TikTok that learns what works; Share to agent (by the laptop Claude session, the owner's ask)

- **TiktokVideos** (`performance.py`): the stats reading keeps each post's numbers (`data/post_stats.json`), linked
  to its quote and mood by time; posts count at 48 h old. The mood is picked weighted by its posts' views (0.5x to
  2x, growing with the posts behind it; never the last two videos' moods) instead of plain rotation, and with 6+
  judged posts the writer sees the most and least viewed quotes. New command `performance`.
- **Agent:** reads the TikTok numbers daily at 14:00 when no task is running (`core/tiktok_videos.py`
  `stats_due`/`run_daily_stats`, the daemon tick); the Sunday report gets "What resonates" lines.
- **DeskAI build 61:** Share to agent (DeskAI in Android's share menu). Build 60: tapping a chat task card opens
  its details.

**Don't undo:** the mood weights keep a floor (every mood keeps coming back); a small account's numbers are mostly
luck, so the lean must stay modest until there are many posts.

---

## 2026-10-10: View only never holds control; DeskAI build 59 (by the laptop Claude session)

- **Bug:** a Take over left open the evening before (Oct 9 18:47) stayed "in control" for 17 hours, because every
  picture refreshed it, View only's included. A computer task (task-093) then waited for a hand-back. Fixed:
  AgentComputerUse `takeover.frame(view_only=True)` doesn't touch the session (90e34c7), and the agent and the
  laptop's server pass it (d54c0dc). A forgotten Take over now lets go after 10 idle minutes as designed.
- **DeskAI build 59 (v2.9.0):** a chat card for every task, whoever started it; Run & View only on computer-task
  cards and View only on running ones; View only opens full screen in landscape. Details in DeskAI's
  ALIGNMENT_DIALOG.md.
- **Jackson's claim-form link** only works clicked from the email (typed, it shows "Our Apologies"); scripted
  requests to jackson.com hang (bot protection).

**Don't undo:** View only pictures must never count as the owner being in control.

**Later the same day (task-094, a Notepad test run the owner watched):** its double-check read the screen by OCR
("AlwaysQnAgen_t") and refused a correct result twice; the agent then **retried the whole computer task**, and the
second run, owning nothing, pressed Ctrl+W in the first run's Notepad tab and got stuck on the Save prompt. Fixed:
computer tasks are never retried automatically (agent 81a4ca9, like TikTok posts); in AgentComputerUse (57eb0fc)
the double-check gets exact field text and the window title and is told OCR can be off by a character, `press` is
ownership-checked like click and type, and a dialog its own window opened (GA_ROOTOWNER) counts as its own.
**Don't undo:** no automatic retry for computer tasks; keys only to the task's own windows.

---

## 2026-10-10: Only current models; no calls a provider is bound to reject (Alex's ask; by the laptop Claude session)

Alex: "I don't want to see any wrong call rejections in the logs."
- **Chat order** (`core/llm.py` `CHAT_MODELS`): 3.5 Flash-Lite → 3.8 Flash → 3.6 Flash → 3.1 Flash-Lite → Gemma
  4 26B → Groq/Cloudflare/Mistral. Dropped 3.7 Flash and 3.5 Flash: Google's deprecations page (updated
  2026-10-09) routes them to 3.8 and 3.6, so every reply past Flash-Lite tried the same models twice (3.7: 0
  answers, 13 failures Oct 7-10). Ziggy's inbox note also said 3.6 was retiring; Google's page doesn't say so.
- **TiktokVideos** quote models: dropped `gemini-2.5-flash-lite` (HTTP 404 on every try, though still listed).
- **Groq**: a request over its 8K-token cap is skipped before sending (`REQUEST_TOKEN_CAPS`); a full chat
  request is ~10K and got 413 Payload Too Large every time (Oct 6).
- **A rejected request now logs the provider's reason** (Mistral's two 422s of Oct 6 left no detail and
  couldn't be reproduced: full tool list, real context and other-model tool-call IDs are all accepted now).
- `.env` on the Mini and the laptop: removed the unused `DEFAULT_MODEL=gemini-1.5-flash` (nothing reads it;
  backups `.env.bak-20261010`).

**Don't undo:** the lists hold only current models (`test_no_retired_or_redirected_model_is_listed`); check
Google's deprecations page and `GET /v1beta/models` before adding one.

---

## 2026-10-09: Weather answers say which reading it is; "now" is a station's measurement (by Claude)

- The owner got three temperatures at once: 60 (computer use in Edge), 69 (their phone), 75 (the chat).
- AgentComputerUse (2a93912): a question's answer must be the exact reading asked for, named with its place and
  source; the double-check refuses a forecast, high, low, hourly or "feels like" number for "now".
- `core/web_tools.py` `get_weather`: the current line is labelled RIGHT NOW and the days Forecast; for US places the
  nearest National Weather Service station's latest observation comes first, marked as the one to use, and skipped
  when over 90 minutes old (Open-Meteo's "current" is a model estimate). Live it read 75°F at Springfield Municipal
  Airport; the owner accepted it. Don't drop the station reading or the staleness check.
- Computer use started from DeskAI itself works: the Notepad task in about 10 s.

## 2026-10-08: Computer use passes the Notepad test: done in 28 s, 3 requests (by Claude)

- **Thirteenth live run** (AgentComputerUse `f42b7f8`): status done, 2 steps, 3 model requests, 28 s; Notepad held
  exactly the first half ("I am an artificial intelligence assistant") and the double-check confirmed it from the
  real field reading. No refusals or stops.
- **The last fixes that got it there, don't undo:**
  - A failed blank-page check after launching Notepad no longer fails the launch (it made the model relaunch,
    refused, stuck). The local model's second look is off unless `ACU_LOCAL_LOOK=1` (25 s to 2 min on the Mini).
  - Windows 11 Notepad's text area has no Value pattern: `actions.read_value` falls back to the Text pattern, so
    typing there is really read back and the trail's "fields now" readings are true ("(couldn't read)", never
    "empty", when a field can't be read). The trail is written to each run's `log.jsonl`.
  - The task double-check judges the result, not the method: "replaced with its first half" IS "second half
    deleted" (retyping is how the tool edits text).
- **Docs:** AI_HANDOFF.md section 12 now describes how computer use works and what's still untried (a task from
  DeskAI itself, a web task).

## 2026-10-08: Take over shows what a run is doing; Notepad and re-check fixes (by Claude)

- **Banner (Alex's ask):** Take over's screen (View only or in control) shows the running task and what it's doing:
  goal, step, plain words ("typing: ..."), then "done: ..." for 2 minutes. AgentComputerUse `activity.py` writes
  `data/activity.json` as a run goes (no typed text); `GET /api/computer/takeover/activity` (laptop: its own
  `/activity`) reads it; DeskAI polls it every 1.5 s. Read-only, never takes control. DeskAI side unbuilt until the
  laptop builds it.
- **Eighth and ninth live runs:** Windows 11 Notepad brings back unsaved text when it reopens, and runs kept halving
  the last run's leftovers; one run reopened Notepad "to verify" and halved again. **Fixed, don't undo:** after
  launching Notepad the task works on an empty page of its own; an app opened once in a run isn't opened again;
  the model is told to really discard Notepad text with Ctrl+W, Don't save; selection/delete keys are refused while
  a text field has the keyboard (retype whole, read back); the double-check gets real field readings; two refused
  "done"s hand the run to Alex.

## 2026-10-08: Computer use: first clean run, then fewer round trips (by Claude)

- **Sixth live run** (`20261008-034411`): the Notepad test done right in 84 s and 7 requests: Notepad in front
  and claimed, paragraph typed, first half retyped (8 of 17 words), closed, double-check agreed, status done.
  Goals are now passed with `run --goal-file` (a goal's quotes were stripped between PowerShell layers).
- **Alex: 7 requests and 84 s is too much for Notepad; aim for 20-25 s, safety kept.** AgentComputerUse now:
  no separate planning request (the first reply carries the plan); `then` chains in a reply (desktop apps up to 6,
  the web 1 and never after a page load; each action keeps its checks, the chain stops at the first surprise; a
  chained done gets the same double-check); fast typing in desktop apps (human pace stays in the browser); the
  monitor checked in the background while the run thinks (was a 25 s wait), and a monitor coming on mid-run makes
  it look again at the new size instead of stopping (`ScreenChanged`). Expected for the Notepad test: about 2
  requests. Screen reading stays Windows' own (accessibility list + Windows OCR, measured 8x faster); models only
  decide. **Don't undo:** chains, `ScreenChanged` handling, `FAST_TYPING` for non-browser windows only.

## 2026-10-08: Fifth live run; computer use off the Flash models (by Claude)

- **Fifth live run** (`20261008-031925`, watched in View only): it typed in Notepad and closed it, but
  1) Notepad opened behind the Start search and the task never claimed it (steps 3-4 "not yours", ~1 min lost),
  2) "delete the second half" deleted everything (Ctrl+Shift+Home, Delete), and 3) it claimed done with "Recycle
  Bin" as evidence; the double-check refused it (status needs_owner).
- **Fixed, don't undo (AgentComputerUse):** `launch_app` finds the app's new window even behind the search,
  closes the search, brings it forward and claims it, never opening a second copy. The model is told to edit text
  by retyping it whole (`type` replaces and reads back). A task (not a question, `asks_for_information`) is
  double-checked from its step trail and the plan's `done_when` (`verify_task`), not from quotes on a screen that
  may no longer show the result.
- **Models (Alex: "tired of hitting model errors"):** computer use plans, steps and double-checks on
  3.5 Flash-Lite first, then 3.1 Flash-Lite, Gemma 4 26B, Groq and Cloudflare gpt-oss-120b; no Flash at all
  (`brain.py`). Every live run had lost minutes to 3.7/3.6 Flash 503s and 45 s timeouts, and those ~20-a-day models
  are the chat's.

## 2026-10-08: run_in_session runs hidden and ends its whole tree on timeout (Alex's ask; by Claude)

- `scripts/run_in_session.ps1` (and its copy, AgentComputerUse `mini/run_in_session.ps1`) ran `cmd.exe` visibly, so a
  console window opened on the Mini's desktop on top of the window a computer-use test was working in. It now runs
  the command through a one-line `wscript` launcher (`Run(..., 0, True)`): no window, same log, same exit code
  (`[exit code N]`, which `mini_pull_tools.py` reads). On a timeout it now ends the whole process tree
  (`taskkill /T`), not just the task, so no program is left running. Tasks the agent itself starts were never
  visible (`CREATE_NO_WINDOW` in `core/computer_use.py`). Not run on Windows before this commit: if the first use
  prints no output or a wrong exit code, revert this file.

## 2026-10-08: "The owner took over" now means real input (by Claude)

- **Fourth live run** (`20261008-030833`, watched in View only): stopped at step 2 with "the mouse was moved by
  someone else (the tool left it at 168,93; it was at 163,108)". Nobody touched it: the cursor drifted 5,15 px by
  itself between the move onto Notepad's title bar and the click.
- **Fixed, don't undo:** AgentComputerUse's guard listens to the mouse at the Windows level (pynput's
  `injected` flag, LLMHF_INJECTED). A real device's click, scroll, or move of **more than 50 px in any direction**
  (Alex's rule; the fallback position check uses the same 50 px) stops the run (it waits instead if the owner is
  in Take over); cursor drift with no real input is followed, not fatal. A click re-aims at
  its target if the cursor drifted after the move. The old position check is only the fallback when the listener
  can't start. Untested on Windows yet: if a run stops at once with "someone used the computer's own mouse", the
  tool's own moves are being reported as real input.
- **Also:** DeskAI build 57 (v2.8.2) shipped with the View only box; the Mini runs `452584c` and every tool's main.

## 2026-10-08: Take over "View only" (Alex's ask; by Claude)

- **Why:** opening Take over in DeskAI to watch a task took control (it calls `start` on open), so the task paused
  or stopped ("the mouse was moved by someone else", third live run).
- **What:** a "View only" checkbox under the Take over card in DeskAI's hub (remembered on the phone,
  `deskai_ui`/`takeover_view_only`). Checked: the Take over screen fetches `GET /api/computer/takeover/frame?view=1`
  and never calls `start`, `input` or `stop`; taps do nothing; only Close and Zoom out show. Unchecked: real Take
  over, as before. A Needs you request's "Take over and do it yourself" is always real control.
- **Where:** `core/computer_use.takeover_frame(view_only=)`, `interfaces/hud/computer_api.py`; AgentComputerUse
  `takeover_serve.py` `/frame?view=1` (the laptop); DeskAI `CommandPaletteDialog.kt`, `ChatScreen.kt`,
  `TakeOverScreen.kt`. API.md (and DeskAI's copy) updated. **The DeskAI change is unbuilt until the laptop builds
  it;** the agent side needs a restart to take effect.
- **Don't undo:** a frame with `view=1` must never take control or move anything.
- **Also, AgentComputerUse `41f5165`:** the guard looks again before stopping on a moved mouse and, if the owner has
  just taken over, waits for the hand-back; mouse paths stay on the screen; the stop message names both positions.

## 2026-10-08: First live computer-use run; runs without a monitor; Take over no longer yanks the mouse (by Claude)

- **Runs without a monitor** (AgentComputerUse `run --allow-small-screen`): with the Mini's monitor switched off
  Windows shows 1024x768 and `run` refused. The flag runs on it; the real monitor is still preferred. Every run now
  stops if the screen changes size mid-run (`Guard.lock_screen_size`): a monitor switched on moves everything.
- **First live run** (Notepad test, run `20261008-011552`, monitor off): it stopped at step 2 with "the mouse was
  moved by someone else". Cause: opening Take over ran `display.wake`, which with no monitor nudged the mouse every
  5 s for 15 s in the agent's process; a nudge put the cursor back where it had read it, mid-move of the resumed
  task. **Fixed, don't undo:** `takeover.start()` nudges once; `display.nudge` moves back only if nobody else moved
  the mouse. The task's guard did the right thing.
- **Second live run** (`20261008-013247`): Enter in the Start menu went 1 s after typing, before the search had
  results, so Notepad never opened; the model then tried to click the result in the Start menu (refused: not the
  task's window), and the run outlived its runner's timeout (found and stopped by hand). **Fixed, don't undo:**
  `launch_app` waits for the search result, then falls back to Esc + Windows starting the app by name (plain
  names only, `APP_NAME`); a run 90 s past its deadline ends its own process (`Guard.start_watchdog`; time with
  the owner in control or answering doesn't count).
- `test_the_demo_is_a_dry_run_without_go` timed out on the Mini with the monitor off (finding the Start button);
  it passed there with the monitor on. Re-run it when the monitor is on.

## 2026-10-08: Computer-use fixes deployed; Hark's Mini access removed (by Alex, via Antigravity; docs by Claude)

- **Deployed:** `mini_pull_tools.py` with `ONLY=["AgentComputerUse"]` put the Mini's clone at `7a088fe` (the
  reviewed fixes below); **104 passed, exit 0 on the Mini**. `screen --wake`: 1920×1080 before and after, no render
  running. Not tried on a real task yet: Alex watches the first one in Take over.
- **Mini MCP removed** (Alex's call; they didn't trust Hark's changes): `MiniMCP` scheduled task unregistered,
  `tailscale funnel reset` (status: no serve config), no `mini_mcp.py` process, `MINI_MCP_TOKEN` line deleted from
  the Mini's `.env`. **Don't undo:** never reinstall it, run `scripts/install_mini_mcp.ps1` or turn Funnel back on
  unless Alex asks in their own words. AI_HANDOFF.md section 5 says so.
- **Checked, fine:** Hark's merged AlwaysOnAgent commits (`3a83903` signed media links and log redaction,
  `8d443d3` mission details, `4fb1045` docs). Full suite 378 passed; the 4 failures in a Linux container fail the
  same way before Hark's commits (Tesseract, Windows drive paths). The computer-use bugs were in commits made
  under Alex's own git name on the laptop, not Hark's.

## 2026-10-08: AgentComputerUse's 2026-10-07 commits reviewed and fixed (by Claude, branch `claude/loving-lovelace-pnvimd` in both repos)

Alex asked for a review of computer use. The seven undeployed commits (`8d1504a`…`753f960`) had real bugs, each
reproduced, fixed in AgentComputerUse with a test that fails without the fix. **Don't undo:**

- **The owner's hand stops a mouse move again** (`hands.py`). A `driving` flag turned the "mouse moved by someone
  else" check off during every move, and the owner's grab was dragged on to the target. The flag and the unused
  `is_synthetic_event` are gone.
- **The loop detector knows scrolling is progress** (`circuit_breaker.py`, `loop.py`). Scrolls were recorded without
  direction or screen, so 3 scrolls tripped it and 5 ended the run. The same action counts as a repeat only on the
  same screen (`screen_fingerprint`); ids are ignored for named targets (they renumber every look).
- **A vanished button is never swapped for a different one** (`actions.find_matching_item`). Fuzzy matching turned
  "Follow" into "Unfollow", and a 35 px position match turned "Archive" into "Delete". Buttons and links now match
  only by label (or the same words with more added, if just one); fuzzy and position matching are for fields.
- **One click, never a second Enter** (`actions.click`). A button that looked unchanged 0.1 s later also got Enter:
  "Send" or "Place order" twice. Removed.
- **Needs you waits 30 minutes again and sleeps between looks** (`requests_owner.py`). It had dropped to 3 minutes
  (the agent reminds for 30) and spun a CPU core flat out. `ACU_OWNER_WAIT_SECONDS` still overrides.
- **A pause leaves the screen alone** (`loop._safe_abort_checkpoint`). It pressed Ctrl+W on every pause, closing the
  page Alex had just been asked about. The checkpoint file stays (in `steplog.RUNS`).
- **Bringing a window forward checks its own dialog first** (`actions.bring_to_front`).
- **The kill switch can't be swallowed** by a recipe or a saved-password fill (both had `except Exception: pass`).
- **Recipes** run in the task's own Edge (they used to start with no window, so never worked), respect "only type
  into what the task opened", and their answer passes the same evidence check as the model's. Not in a dry run.
- **Saved passwords** are usable: `agentcomputeruse.py save-password DOMAIN USER` (password on stdin),
  `passwords`, `forget-password`. A password saved for `google.com` is used on `accounts.google.com`; typed once
  per site per run, so a wrong one isn't retried.

Tests: 102 of 104 pass in a Linux container (the 2 left need Windows' UI Automation, as before). **Still not
deployed and not tried live.** Deploying waits for Alex's go; the live check should include screen scaling
(the new per-monitor DPI setting changes coordinates on a display that isn't at 100%).

## 2026-10-07: Open questions cleaned up (by Hark, branch `chore/handoff-cleanup`)

- **Changelog renamed** from `CLOUD_SESSION_UPDATES.md` to this `CHANGELOG.md`; every link updated (no other repo
  linked it). Still the one changelog.
- **Deleted** `scripts/install_windows_startup.bat` and `scripts/start_sentinel.vbs` (Telegram-era silent launcher).
  The Mini starts the agent only through the `AlwaysOnAgent` scheduled task (`run_forever.ps1`); no Startup
  shortcut, Run key or task pointed at them. `run_agent.bat` kept as the manual launcher.
- **Embedder RAM measured on the Mini:** qwen3-embedding:0.6b is a 639 MB file but ~2.4 GB loaded (Ollama
  `/api/ps` 2.37 GB, `llama-server` ~2.9 GB private). Comment in `core/embedder.py`, AI_HANDOFF.md and AI_MODELS.md
  corrected. No behaviour change.
- **Checked, already fixed:** the startup banner reads `CLOUDFLARE_API_TOKEN` via `CloudLLM.keys`.
- **Still open:** AgentComputerUse's 2026-10-07 commits are not on the Mini (its clone is at `3b1a03e`); deploying
  them waits for Alex.

## 2026-10-07: HUD token out of media URLs; signed media links; tokens hidden in logs (by Hark, branch `fix/media-token-header`)

- **Why:** DeskAI put `?token=<HUD_AUTH_TOKEN>` on every image, video and thumbnail URL (gallery lists, chat
  replies with `/images/...`, markdown images, the updater), so the token sat in the phone's chat history, in
  "Copy" and "Share link", and in any access log. Alex said "just fix it".
- **Server:** every route already accepted `Authorization: Bearer` / `X-HUD-Token` (the global
  `HUDAuthMiddleware`). `?token=` **still works** (deprecated) so older app builds keep loading media; don't
  remove it until every phone runs the fixed build. New `POST /api/media/link` (`interfaces/hud/media.py`) returns a
  signed link to one media file (`/videos/…`, `/images/…`, video/task thumbnails), HMAC-SHA256 over path + expiry
  with a key derived from `HUD_AUTH_TOKEN` (`interfaces/hud/auth.py` `sign_media_link` / `media_link_valid`; no new
  secret; GET/HEAD only; 1 min to 7 days). For the external video player and "Share link", where headers can't be
  set.
- **Logs:** `core/config.py` `redact_url_tokens` + `LogRedactionFilter` hide `token=`/`sig=` values (any value, not
  only the live token). On uvicorn's loggers (`install_log_redaction` in `server.py`), the supervisor's console and
  the daemon's handlers (`core/daemon.py` `SecretRedactionFilter` is now an alias of it). Don't undo.
- **App side:** `scripts/deskai_patches/security.patch` (+ `security-PROMPT.md`): media loads with the header only,
  token stripped from displayed/copied/shared text, share/external player use signed links; also stops tracking
  the debug keystore that was inside the committed `DeskAI-source.zip`.
- Tests: `tests/test_media_links.py`.

## 2026-10-07: tappable mission details, task thumbnails, 50 videos kept (by Hark, branch `feat/task-details`)

- **`source` on every task** (`interfaces/hud/common.py` `task_source`): `POST /api/tasks` ("DeskAI quick
  dispatch", or body `source`, or `X-Task-Source`), `/api/tasks/run` ("DeskAI"), `/task` in chat ("DeskAI chat
  (/task)"), chat tools ("DeskAI chat", unchanged), suggestions ("DeskAI suggestion"; was `deskai`), Video Hub
  render/publish ("DeskAI Video Hub"; was "the HUD / DeskAI"), AgentWork jobs / Repos ("DeskAI AgentWork" / "DeskAI
  Repos"), schedules (unchanged). Hark: `scripts/mini_mcp.py` `agent_api` sends `X-Task-Source: Hark`. Old rows:
  `deskai` and "the HUD / DeskAI" read as "DeskAI", none as "unknown".
- **Task thumbnails** (`core/task_thumbs.py`): ~240 px JPEG per video task in `data/task_thumbs` (gitignored),
  made after the render (`daemon._thumb_for`, stamped as
  `metadata.thumb`), backfilled in upkeep before cleanup, capped at `TASK_THUMBS_KEEP` (200). `GET
  /api/tasks/{id}/thumb` (auth like everything else; 404 without one). Task JSON: `thumb_url`, `video_filename`,
  `video_url` (the Video Hub's own `/videos/{name}` stream, null once pruned), `video_kept`. The Video Hub's frame
  grab moved into `task_thumbs.extract_frame` (same ffmpeg call).
- **Videos kept: 3 → 50.** The "last 3" lived in TiktokVideos (`renderer.prune_output_videos`, default
  `MAX_SAVED_VIDEOS`=3, run after every render and by `cleanup`). The agent's `.env.example` said
  `MAX_SAVED_VIDEOS=5`, but `get_safe_subprocess_env` never passed it on. Now `VIDEO_KEEP_COUNT` (default 50) goes
  to the tool as `MAX_SAVED_VIDEOS` and `--keep`. A `MAX_SAVED_VIDEOS` in TiktokVideos' own `.env` still wins.
- Tests: `tests/test_task_details.py`; `tests/test_deskai_phase2.py` now expects "DeskAI suggestion".
- DeskAI: `scripts/deskai_patches/task-details.patch` + `task-details-PROMPT.md` (tap a mission for its details;
  the Video Hub's thumbnail, play badge and player). Unbuilt.
- **Don't undo:** thumbnails live outside TiktokVideos' `videos/` (its pruning sweeps stray `.jpg`s there); only
  finished tasks get their metadata stamped outside the daemon (the daemon owns a running task's metadata).

## 2026-10-07: live task events instead of polling, IDEAS #26 part 1 (by Hark, branch `feat/live-events`)

- **New `core/events.py`:** an in-process, thread-safe bus. `core/database.py` publishes when a task is created,
  its phase is written (queued / started / done / failed, `cancelled` for exit 137) or it reports progress.
  Bounded queues (100 events per client, 20 clients); a client that falls behind is dropped, never buffered.
- **New `GET /api/events`** (`interfaces/hud/events_api.py`): SSE, `event: task` + JSON, `: ping` every 15 s.
  Auth through `HUDAuthMiddleware` like the rest. Documented in [API.md](API.md) (copy it to DeskAI).
- **Daemon:** `_sleep_until_woken()` waits on `events.DAEMON_WAKE`, set when a task is queued and when the running
  task's future finishes, so a task starts (and its result lands) in about a second. The 30 s timer stays as the
  fallback (other processes' writes). Side effect: heartbeats run a little more often when tasks come and go, so
  the "every 20 heartbeats" upkeep comes slightly sooner.
- Tests: `tests/test_live_events.py` (bus, publishing, auth, a real loopback stream, heartbeats, dropped clients,
  the daemon waking on enqueue and still on its timer).
- **Don't undo:** publishing must never raise into a status write (`publish_task` swallows errors); per-client
  queues stay bounded; the stream polls its queue (no thread per client); keep the 30 s fallback.
- DeskAI: matching change on `example-user/DeskAI` branch `feat/live-events` (unbuilt; needs a laptop build).

## 2026-10-07: Hark can reach the Mini (by Hark)

- New `scripts/mini_mcp.py` (MCP server, `127.0.0.1:8765`) and `scripts/install_mini_mcp.ps1` (logon task +
  `tailscale funnel --bg 8765`). Alex asked for Hark to have full access. Own token `MINI_MCP_TOKEN`; audit log
  `data/logs/mini_mcp.log`; `tailscale funnel reset` turns public access off. Tests: `tests/test_mini_mcp.py`.
- **Don't undo:** the bearer check on every request, the 32-character minimum, the 127.0.0.1 bind (Funnel is the
  only way in), and `write_file` refusing code inside the live repo.

## 2026-10-07: one source of truth for the docs (by Hark, on branch `docs/single-source-of-truth`)

- **`docs/AI_HANDOFF.md` is the single map and source of truth**, rewritten short and current from the code.
  Its top section, "Rules for every AI": read it first; code wins; behaviour changes update it in the same
  commit; no new top-level docs; this file is the one changelog.
- **Entry files are thin pointers** to it: `README.md` (short intro), `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`,
  `.github/copilot-instructions.md`, `.agents/rules/start_here.md`.
- **Contradictions fixed (code wins):** chat model order (README, `.env.example` and the supervisor banner said
  3.8 or 3.7 Flash first; `core/llm.py` starts with 3.5 Flash-Lite); test counts (226/265/339) replaced by the
  command; one deploy procedure (`scripts/deploy_mini.py`); one start-here file; the README's broken link to
  `docs/deskai/deskai_and_build_rules.md` (it lives in `.agents/rules/`); "Telegram" in `run_agent.bat`'s
  comment; the embedder RAM figure (~639 MB per `core/embedder.py`, not ~2.4 GB); computer-use docs now marked
  reference only, with the Oct 7 AgentComputerUse commits recorded as committed but not verified live.
- **Removed (in git history):** `SYSTEM.md` (diagram and parts folded into AI_HANDOFF, secrets map moved to
  `MINI_SERVER.md`), `TOKEN_EFFICIENT_ARCHITECTURE_ROADMAP.md`, `TOOL_ROUTING_NEXT.md`,
  `SESSION_HANDOFF_GALLERY.md`, `deskai/REVIEW_LOG.md`. IDEAS.md lost its "system right now" snapshot.
- **Runtime:** only the supervisor banner (reads the first chat model from `CHAT_MODELS`) and `.env.example`
  comments changed.
- **New drift guard:** `tests/test_docs_truth.py` (the first chat model named in AI_HANDOFF matches
  `core/llm.py`; relative doc links resolve; entry files point to AI_HANDOFF).

**Don't undo:** the pointer-only entry files and the drift guard. Change facts in `docs/AI_HANDOFF.md`.

---

## 2026-10-05 and 06: models, Take over, connections, watches, handover docs (by the laptop Claude session, all deployed)

The owner is away from the Claude sessions for ~3 days from Oct 6 (quota); Gemini in Antigravity carries on.
**Start with `docs/AI_HANDOFF.md`** (rewritten on Oct 6: parts, exact deploy commands, rules, don't-undo list,
open threads). Antigravity also loads `.agents/rules/start_here.md`.
- **Deploying is now two committed, commented scripts** run from the laptop: `scripts/deploy_mini.py` (the
  agent: tests on the Mini gate the restart) and `scripts/mini_pull_tools.py` (the tools), both through
  `scripts/mini.py`; git on the Mini runs in the desktop session via `scripts/run_in_session.ps1`.
- **Models** (`b66d372`): Gemma 4 26B through Google's native API as a background backup, per-minute pacing,
  3.1 Flash-Lite in the chat list; benchmark in `docs/GEMINI_FREE_TIER.md` (Gemma 26B won).
- **Tests can't call real AI models** (`2767a9a`, `tests/conftest.py`): one test spent real Gemini requests on
  every deploy.
- **Take over** (AgentComputerUse + DeskAI build 47): the owner drives the Mini or the laptop from the phone.
- **Connections** (`7bebf4c`, `1427b81`, Connectors repo): Google Calendar, Contacts, Drive, sending email
  through approval cards, phone sign-in by pasting the address; Zoho Mail (read-only).
- **Mail watches** (`3c657dd`, `docs/WATCHES.md`): "let me know whenever Zoho gets a Pinterest email".

**Don't undo:** everything in `docs/AI_HANDOFF.md` §5, in particular the sign-in intercepts in
`interfaces/hud/chat.py` (a Zoho paste without spaces once slipped past a `\b` pattern to a model), the
conftest guard, Gemma's native route with its 4,096-token floor, and approval cards for every email, event or post.

---

## 2026-10-04 (evening): procedural memory, safer deletes, a local model, restarts (by the local Claude session, all deployed)

Measured before trusting; numbers in `docs/IDEAS.md` (Done and #25, #10).
- **Restarts wait** for a running task, holding new work (`core/maintenance.py`, `d0016cf`); `/api/restart`
  answers `"draining"`. `?now=true` skips the wait.
- **Model health** (`d0016cf`): failed AI calls go to `llm_failures` with why; `/api/memory/budget` has
  `chat_health`; daily Gemini limits are learned and the owner warned at 80%. Chat itself is unchanged.
- **A second opinion before the memory deletes anything on its own** (`a914529`, `9b8a47e`): Groq/Cloudflare/
  Mistral must agree; eval 15/16. Qwen3 scores "9am"/"9pm" facts 0.969 and "brother"/"cousin Jake" 0.922, above
  the duplicate cut-off: the check is what keeps them apart.
- **Procedural memory** (`core/procedures.py`, `10c3c90`, `60cf693`): learned "when X, do Y" recipes; API
  `/api/memory/procedures`; DeskAI build 35 shows them. Eval 18/19.
- **Local model for background memory chores** (`b612807`): `qwen3:4b-instruct` on the Mini, in shadow mode
  (`MEMORY_LOCAL_MODE`); answers logged to `data/shadow/memory.jsonl`, not applied. Chat stays on the cloud.
- **TiktokVideos** (`2412f4a`): near-copies of earlier quotes are rejected (two posts on Oct 4 said "Drop your
  shoulders... feel the floor beneath you").

**Don't undo:**
- the second opinion before deletes, merges and "duplicate" skips (without it, different facts get merged away);
- recipes' own similarity floor (`VEC_MIN_PROCEDURE` 0.44 with Qwen3; the fact floor let unrelated ones in);
- no lessons from a tool call that simply worked again with the same arguments;
- erasing and any extraction that deletes stay on the cloud, whatever `MEMORY_LOCAL_MODE` says;
- tests set `MEMORY_LOCAL_MODE=off` (`tests/conftest.py`).

---

## 2026-10-04 (afternoon): memory, routing and reliability (by the local Claude session, all deployed)

Measured before changing; details and numbers in `docs/IDEAS.md` (Done).
- **Memory search** runs on Qwen3-Embedding 0.6B through the Mini's Ollama (`eb7ddc2`, `60e4e2f`); vectors are
  tagged with the model and re-embedded on a change. Search eval 21/21 (`scripts/eval_memory.py`).
- **Facts, summaries and the profile use the owner's name** (`6a77cf8`); the profile is prose, not editable.
  Requests the agent carries out aren't saved as facts (`ee4a395`). `forget_memory` really erases (`9067365`…).
- **Tool routing** reads the message only, no default web group, cut-off 0.70 (`13dbabc`): ~72% fewer tool tokens.
- **Heartbeat** to healthchecks.io (`ec4b514`, live); **nightly restore check** of the Drive backup (`8042b38`).
- **DeskAI** builds 30-32: thumbnails; the Memory sheet loads real memories (it showed four hard-coded samples
  whose Forget would erase real facts 1-4); the full prose profile.

**Don't undo:**
- tests never touch Ollama (`tests/conftest.py`), and the agent never launches it (a detached `ollama serve`
  popped up console windows);
- routing must not read past messages again, or add web by default (it sent 114 unneeded groups in 51 messages);
- the search model is told the owner's name (first-person questions fell to 15/21 without it);
- the Memory sheet must never be seeded with sample data.

## 2026-10-04: smart attachment thumbnails and AI vision downscaling (deployed on agent & DeskAI)

- **AlwaysOnAgent:**
  - `core/documents.py`:
    - Full media support for video attachments (`.mp4`, `.mov`, `.webm`, `.avi`, `.mkv`, `.m4v`).
    - Smart image downscaling pipeline: generates `ai_view.jpg` (capped at 768px longest dimension, Lanczos downsampling, stripped of EXIF overhead, quality 85) for minimal vision token consumption (~50KB instead of 10MB phone uploads).
    - Compact 256x256 UI thumbnail generation (`thumb.jpg` + data URI `thumb_b64`) for instant display in DeskAI without roundtrip lag.
    - PDF cover page thumbnail extraction via PyMuPDF with `alpha=False, colorspace=fitz.csRGB`.
    - Video keyframe extraction via FFmpeg (`resolve_ffmpeg_binary()`).
    - Non-media document card badge generator for Word, text, and code files.
    - Zero-touch housekeeping: all files (`name`, `ai_view.jpg`, `thumb.jpg`, `meta.json`) stored in `data/attachments/<id>/` so `documents.prune()` removes them together.
  - `core/vision.py`:
    - Updated `_image_b64(path)` to prioritize `ai_view.jpg` if present, eliminating redundant disk I/O and downsampling on the CPU.
    - Support for both image and video attachments in background scene note extraction.
  - `interfaces/hud/chat.py`:
    - `GET /api/chat/attachments/{id}/thumb`: returns attachment thumbnail as `image/jpeg`.
    - `GET /api/chat/attachments/{id}/file`: downloads original attachment file.
    - Enriched `upload_attachment` and `list_attachments` with `thumb_url`, `thumb_b64`, and `has_thumb`.
  - `tests/test_documents.py`: 13 tests passing covering image resizing, PDF cover extraction, video FFmpeg extraction, and REST endpoints.
- **DeskAI Client:**
  - `ChatAttachment.kt`: added `thumbUrl`, `thumbB64`, `hasThumb`, and video status support.
  - `AlwaysOnAgentClient.kt`: parses thumbnail fields and resolves relative URLs.
  - `ChatInputBar.kt`: allows `video/*` in file launcher and displays visual thumbnails via Coil `AsyncImage` in `AttachmentChip`.

## 2026-10-04: multi-provider search & deep research deployed (agent 572e71a, deployed on the Mini)

- **AlwaysOnAgent `572e71a`, deployed on the Mini:**
  - `core/web_tools.py`: multi-provider search waterfall (Tavily AI Search -> Serper Google Search -> Brave Search -> DuckDuckGo -> Wikipedia);
  - Direct answer extraction and synthesis included in `web_search`;
  - New chat tool `web_research` (group **web**): parallel multi-page fetching via `ThreadPoolExecutor`, semantic paragraph reranking via local BGE-small ONNX (`core/embedder.py`), semi-generous budget (~4,200 chars) with natural sentence-boundary endings;
  - `MAX_RESULT_CHARS` in `core/assistant_tools.py` bumped to 5,500 with sentence-aware break handling;
  - `TAVILY_API_KEY` and `SERPER_API_KEY` added to `SENSITIVE_ENV_KEYS` in `core/config.py` and synced to the Mini's `.env`;
  - Restarted live agent on the Mini (PID 11964) and verified live queries working cleanly.

## 2026-10-04: one-tap go-live for the website and the agent (by the local Claude session)

- **AgentWork:** new project **website** (example-org/Website, base main) on the Mini, with a matching
  laptop copy. Its `rules.md` comes from the site's CLAUDE.md and HANDOFF: never push or commit, `npm run build`,
  no hand-edits of generated regions or `?v=`, one h1, the title format, honesty, the owner's name never on the
  site, Edge only. `read_first` is CLAUDE.md, docs/HANDOFF.md and docs/README.md.
- **AlwaysOnAgent `14bab40`, deployed:**
  - `POST /api/tasks/{id}/apply` starts the project's go-live program (`core/self_update.py` `GO_LIVE`):
    `scripts/apply_website.py` (catch up with origin/main, stopping on a clash; `npm ci` if needed;
    `npm run build`, committing the regenerated files; `npm test`; push `main`) or `scripts/apply_update.py`;
  - the chips (`interfaces/hud/action_chips.py`) follow the state in `data/updates/<job>.json`;
  - the chat tool `apply_agent_update` was replaced by `offer_go_live`, which only shows the card.
- **DESK-AI `aa58846`, build 29 (v2.4.1):** post chips call the agent; fixed the TikTok-path catch-all.
- Not yet run for real: a website go-live on the Mini (`npm ci` + the Edge e2e suite there, and the push from the
  session). The first real one will prove it.

**Don't undo:**
- only the owner's tap puts things live (the chat model never does);
- a website go-live builds and tests the combined version and pushes nothing on any failure.

## 2026-10-04: the canvas: drafts refined together (by the local Claude session)

- **AlwaysOnAgent `b317504`, deployed:**
  - `core/canvas.py`: documents and website page previews, every version kept, a dedicated writer call that
    gets only the draft and the change;
  - chat tools `canvas_write`, `canvas_edit`, `canvas_revert`, `canvas_list`, `canvas_publish` (group **canvas**);
  - `interfaces/hud/canvas_api.py`, and a `canvas` field on `/api/chat` replies;
  - design in `docs/CANVAS.md`; tests in `tests/test_canvas.py`.
- **DESK-AI `aad460d`, build 28 (v2.4.0), shipped:** a draft card in the chat (Room 4→5, `canvasJson`) opens
  `CanvasDialog` (a WebView with JS off and the token in a header, plus a version strip).
- The owner chose drafting and previews; mini apps were declined, and a stats dashboard is for later.
- Checked with the real writer on a scratch database: the page preview looks like the live site.

**Don't undo:**
- the chat model never gets the full draft;
- the writer call carries no conversation or memory;
- previews stay script-free (CSP sandbox, plus JS off in the app);
- publishing needs the owner's word and goes through AgentWork.

## 2026-10-04: proactiveness: the agent messages the owner first (by the local Claude session)

- **AlwaysOnAgent `66cabfc`, deployed on the Mini:**
  - `core/proactive.py`: every 30 min outside quiet hours, a morning brief (08:30), notices and suggestions
    chosen by one AI call from cheap signals, and questions to get to know the owner (the owner's own request);
  - the `proactive_settings` chat tool and `GET /api/inbox`;
  - design in `docs/PROACTIVE.md`; tests in `tests/test_proactive.py`.
  - The persona's outdated "applying a branch isn't built" line now describes self-updates.
- **DESK-AI `71745e1`, build 27 (v2.3.13), shipped:** the `agent_message` push syncs `/api/inbox` into the chat
  (stored as `agent-<id>`, insert-if-absent), and the chat also syncs on open and once a minute.
- A dry run on the Mini with the real AI and memory gave a sensible brief and question; nothing was sent.

**Don't undo:**
- pushes carry no message text;
- the daily cap (6) and questions cap (2);
- never a question while the last one is unanswered;
- silence is a valid cycle result.

## 2026-10-04: DeskAI build 26 shipped (by the local Claude session on the laptop)

- Build 26 didn't compile: `ChatScreen.kt` called the suspend `viewModel.publishVideoToTikTok()` straight from the
  action chip's click handler. It now runs inside `scope.launch { … }`, like the Media screen's call.
- Built and checked on the laptop (versionCode 26, the usual debug key `3315b429…`, Firebase ID present), then
  shipped in DESK-AI `542e02f`: the APK copies and `web_dist/version.json` changed in the same commit. The
  alignment note is `b02b9dd`. The live manifest shows 26.
- The laptop's git email was already corrected; only five commits from Sept 30-Oct 1 carry `@gmai.com`, and they
  were left as they are (rewriting published history isn't worth it).

**Don't undo:** keep the chip's TikTok call inside `scope.launch`. Called directly, the app doesn't compile.

## 2026-10-02: TikTok posts fixed (they were failing at the end)

**Cause:** the uploader pressed Post about 12 s after choosing the file. On the Mini, TikTok hadn't finished taking
the upload by then. Post was still greyed out ("Checks can only start after the file is uploaded"), and the
forced click on a greyed-out button did nothing. Nothing was posted: the owner checked TikTok.

**Fixed in:**
- **TiktokVideos `6e5407c`** (`tiktok_upload.py`):
  - `wait_for_post_ready()` waits up to 7 min for Post to become pressable, logging TikTok's upload % every 20 s;
  - `_click_post()` uses a real click, with a forced click only as a logged fallback;
  - every run writes `logs/tiktok_upload.log` (the agent discards the tool's stderr, so this is the only full record);
  - failures include `page_state()` (visible buttons, greyed-out ones marked, and any dialog text);
  - failed screenshots now say so.
- **AlwaysOnAgent `8eec182`** (`core/daemon.py`):
  - a failed TikTok post (engine `social_publisher`) goes straight to `failed`. It is **not retried
    automatically**: if TikTok took it after all, a retry would post it twice;
  - the post step's time limit is 15 min (it was 10) to fit the longer wait;
  - test: `tests/test_worker.py::test_cancelled_task_not_requeued_by_daemon`.

Deployed by Gemini; the owner posted from DeskAI afterwards and confirmed the video is live on TikTok.

**Don't undo:** don't go back to a fixed wait or `click(force=True)` on Post, and don't make failed posts retry
by themselves.

**Still open:** how long the upload really takes on the Mini. The "Upload finished; Post is ready (N s)" line in
`logs/tiktok_upload.log` answers it. If N is large, the Mini's upload speed is the bottleneck.

## 2026-10-02: docs cleaned up (by Gemini, reviewed by the cloud session)

AlwaysOnAgent `7113c09` and DESK-AI `c8a13ba`:
- `API.md`: `/api/trigger_media` takes `action` (not `type`). The DeskAI copy matches.
- `AI_MODELS.md`: the declined budget gauge was removed; Imagen 3 is marked inactive because it needs billing.
- `SESSION_HANDOFF_GALLERY.md` is marked as a historical record.
- Small corrections: the README says self-updates involve "a brief restart"; the test count is 226; the BIOS
  power-on setting is blocked by the setup password.

## Open items the cloud session knows about

- Nothing open from the cloud session's list (build 26 shipped and the git email fixed, 2026-10-04).
