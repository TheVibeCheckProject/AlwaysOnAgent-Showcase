# Plan: AgentComputerUse

> **Reference only, not source of truth.** Plans and research; parts may describe features that are not built,
> deployed or verified. What is actually live: [AI_HANDOFF.md](../AI_HANDOFF.md). Roadmap: [IDEAS.md](../IDEAS.md) #27.

Started 2026-10-05 and kept up to date as it's built (see **Where it stands** first). A new tool, its own project like ImageGen or
TiktokVideos, that uses the Windows computer the way a person does: it opens the browser, searches, reads
sites, types, fills in forms, and works with desktop apps. The agent stays the brain and calls the tool, which
is the hands. Everything stays free: free libraries, free or local models, no paid APIs.

## Where it stands

**Update 2026-10-11** (the current state is in [AI_HANDOFF.md](../AI_HANDOFF.md) section 12; what changed and why in
[CHANGELOG.md](../CHANGELOG.md)):
- **Reliable on fresh tasks:** fresh, untuned batches with the monitor off ended 11/11 clean. Every run minimizes all
  windows first and works only in its own.
- **File work:** done whole by the tool, `open_file` and `save_as`.
- **Retasks:** a clean run is saved, and the same task asked again is repeated without the AI. Answers and written
  text are always fresh.
- **Logs:** narration lines carry a kind, so DeskAI colors them.
- **Code:** the tool's `loop.py` is split into mixins.
- **Not yet used for real:** saved passwords and applications (the profile store and the form filler).

The section below is the 2026-10-06 snapshot.

### 2026-10-06

**Built and live on the Mini:** phases 1, 1b and 2, and phase 3 parts 1-3 (see "Plan of work").
The tool's repo is example-org/AgentComputerUse (private, latest e2840c1). The agent side is
1813a27 and later, and the DeskAI side is build 47 (v2.6.4).

- **Checked live:**
  - The phase 1 demo on the laptop and on the Mini.
  - The first AI-run answer. It gave Chicago's temperature for Springfield, which is now refused in
    code. The next run answered 71°F, checked against its screenshot.
  - Starting a task from a chat card in DeskAI.
  - **Take over** (phase 3 part 3): the owner tried it on the laptop from their phone first ("it felt fast and it
    was good"), then in DeskAI on the Mini and the laptop. The Mini's or the laptop's screen live on the phone;
    tap = click, hold = right-click, pinch to zoom, Double and Drag modes, scroll, text and key buttons. A running
    task waits while the owner is in control (its guard waits instead of stopping) and looks again after Hand
    back; a request answered by handing back gets `__handed_back__`. Lets go after 10 idle minutes.
    - The laptop runs its own Take over from login (`takeover-serve --host tailscale --token-file ...`, the
      scheduled task "AgentComputerUse Take over"), kept awake while plugged in; the agent passes calls on
      (`LAPTOP_TAKEOVER_URL` / `_TOKEN` in the Mini's .env); DeskAI has a Mini / Laptop switch.
    - In DeskAI it's drawn in the app's main screen with two containers (the picture, then the controls above
      the keyboard): as a pop-up window the keyboard covered the controls (builds 45-46).
- **Fixed from real runs (2026-10-06):**
  - Caps Lock was on at the Mini: every letter came out in capitals (the owner's Take over typing too) and the
    read-back never matched, so the address was typed three times and then a suggestion clicked. Typing now turns
    Caps Lock off first; the address bar counts with Edge's autocomplete, then Delete and Enter.
  - The Jackson task got 10 steps in, then took the Windows taskbar (explorer.exe, like a File Explorer window)
    for a pop-up and stopped. The shell's windows never count as pop-ups now, and a pop-up found while getting
    unstuck is asked about instead of ending the task.
- **Built but not yet tried end to end:**
  - "Needs you" alerts on the owner's phone.
  - Sign-ins from the app (2026-10-06; tool c87dd42, DeskAI build 40), from a real Gmail sign-in where the owner
    wanted "Try another way" and was only offered the password:
    - On a sign-in page the owner picks "Type my password" or the page's own options ("Try another way" and
      the like). A pick acts only on the button it came from.
    - A password or code is typed on DeskAI's own screen, in a hidden field with screenshots blocked. The tool
      types it into the outlined box itself; the model never sees it, and nothing keeps it.
    - "Tap Yes on your phone" pages are recognized (only on sign-in pages) and the tool waits for the owner.
- **Next:**
  - Phase 3 part 4: the Passwords screen (saved passwords in Credential Manager, locked to their sites).
  - Then phase 4: forms and applications.

**In this plan but not built yet** (so nobody assumes them):

- The `research`, `apply`, `profile` and `recipes` commands. Today everything goes through `run "<goal>"`.
- Signing in: Credential Manager, passwords locked to their sites, and the code box. Phase 0's real Google
  sign-in test hasn't been done either.
- Turning off Edge's automatic sign-in in the tool's profile. Today the profile only skips first-run and
  default-browser prompts.
- The "two sources agree on a field's label before typing" check.
- Stopping by slamming the mouse into the top-left corner. Ctrl+Alt+End and moving the mouse do stop it.
- A per-task allow-list of websites and apps, and blocking banking sites and password managers. `--allow`
  today only names the owner's own windows and files a task may change.
- A "Show" button on the Submit approval.
- The cross-site frame test.

## What the owner wants it to do

- **Find things out:** "go figure out X" means open the browser, search, read several sites, and come back
  with the answer and where it came from.
- **Apply for things:** mainly **jobs** and **free sign-ups for giveaways**. Open the site, fill in the
  form from the owner's details, and stop for the owner's OK before submitting.
- **Look things up on the owner's accounts,** including social media, signed in as the owner.
- **Do things on the computer in general:** open apps, handle files, and change settings when asked.

## The short version

- **One agent for the whole computer, browser and desktop together.** A single task can move freely
  between them, the way a person does: download a form from a website, fill it in with Word, save it, and
  upload it back. One loop, one planner, one set of actions (click, type, keys, scroll, open app, switch
  window).
- **Only the real mouse and keyboard, never an automated browser** (the owner's rule). Sites detect and
  block browsers driven through their automation interface (Playwright, Selenium, the debugging port),
  so it uses a **normal Edge, launched normally** (Edge and Chrome share the same engine), and moves the real mouse and types on the real
  keyboard, like a person.
- **One way of reading the screen for everything:** Windows' accessibility system (the one screen
  readers use), which lists every field, label, button and link with its position in Chrome pages and in
  apps alike, **plus an OCR pass on every look** for text the accessibility system misses. Both are merged
  into the same kind of numbered list every time. Tested: it read a whole web form in a normal Chrome,
  and Windows' OCR read the whole screen in 0.6 s (see below). A vision model is the last resort.
- **Modeled on [Windows-Use](https://github.com/CursorTouch/Windows-Use)** (MIT), which reads Windows the
  same way.
- **You can watch it.** It uses a real, visible Edge window with its own profile, kept separate
  from your personal browsing.
- **Spend few AI requests.** One Flash call plans, Flash-Lite does the steps, a whole form is filled from
  one request, and repeat tasks replay as recipes with no AI.
- **It signs in by itself.** Your passwords live in Windows' encrypted Credential Manager, are locked to
  their own sites, and are never shown to the AI. When a site asks for a 6-digit code, DeskAI asks you
  for it and the tool types it in.
- **You stay in charge.** It stops before Submit, Send, Pay, Delete or Post and shows you the page in
  DeskAI (per task type you can let trusted ones go without asking). It never types card numbers or your
  Social Security number, and it never solves CAPTCHAs; it asks you.
- **First goal:** "find out X" works end to end. You ask in DeskAI; an Edge window opens on the Mini,
  searches, reads 2-3 sites, and the answer comes back with links.

## What already exists (checked 2026-10-05)

| Project | What it does | License | Use it? |
| --- | --- | --- | --- |
| [Windows-Use](https://github.com/CursorTouch/Windows-Use) | An AI drives Windows through the accessibility tree | MIT | **Our model.** Same libraries we'd use. Small (455 stars). Its docs say to run it in a VM or Sandbox. |
| [Microsoft UFO²](https://github.com/microsoft/UFO) | Windows agent: accessibility tree + vision | MIT | Read for ideas. Needs Python 3.10-3.11, built around OpenAI models, now in long-term support. |
| [Browser Use](https://github.com/browser-use/browser-use) | An AI drives a browser through its automation interface | MIT | **Not used:** it controls Chrome through the debugging connection, which sites can detect. Its prompts and ideas are still worth reading. |
| OmniParser V2 | Finds clickable things in screenshots | MIT | Later, for screens nothing else can read. Needs a GPU. |

**Tested on the laptop (Python 3.13.7, 2026-10-05):**

- `uiautomation` listed the open windows in 0.5 s and read the taskbar's 61 elements in 0.1 s; `mss` took
  a 1920×1080 screenshot in 0.03 s.
- **A web form in normal Chrome** (no automation, no special flags, a throwaway profile, the test page
  httpbin.org/forms/post): the accessibility system read all 15 of its items with labels and positions
  ("Customer name", "Telephone", the Small/Medium/Large choices, the topping checkboxes, "Submit order") in
  2.7 s. That includes a 2 s wait: Chrome switches on page accessibility once something first looks.
  Then the **real mouse** moved to "Customer name", clicked, and the **real keyboard** typed "Test Name",
  which the page then showed. Nothing was submitted.
- **OCR on a full 1920×1080 screenshot:** Windows' built-in OCR read 581 words in 169 lines, each with its
  position, in 0.6 s. Tesseract found about as many in 5-8 s. So Windows OCR runs on every look, and
  Tesseract only as a second opinion.

## How it works

### One loop for the whole computer

Every step, whatever is on screen:

1. **See** what's in front, two ways at once, every time:
   - **Windows' accessibility list:** a web page in Edge (its fields, links and buttons, plus the browser's tabs and
     address bar), Word, Explorer, a Save dialog, Settings, the taskbar.
   - **An OCR pass over a screenshot** with Windows' built-in OCR (free, in Windows 11), which gives every
     word on screen with its position. Tested: the whole 1920×1080 screen in 0.6 s.

   They're merged into one numbered list. OCR text inside an accessibility item (a button's label) is a
   duplicate and dropped. Text only OCR saw (in images, pages drawn as one picture, custom-drawn apps) is
   added with its position, so it can be clicked: "[41] Text 'Enter now' (seen on screen)". When the two
   disagree, what's on screen wins. Tesseract (slower, 5-8 s for a screen) is a second opinion for hard
   text, and a vision model is the last resort.
2. **Decide:** the model gets the goal, the plan, what it's done so far and that list, and picks one action
   from the same set everywhere: click, type, press keys, scroll, open an app, switch window, go to a
   web address (by typing it in the address bar).
3. **Act with the real mouse and keyboard only:** move to the item's position, click, type at a human
   pace (with small pauses), scroll with the wheel. To websites this is a person using Edge.
4. **Check** that it worked, then go on. If the action opened something new (a download finished, a Save
   dialog appeared, another app took the front), the next look simply reads that.

**An example that crosses over:** "Get the rental application from the landlord's site, fill it in, and
send it back." Edge opens the site and downloads the PDF; Explorer or Acrobat opens it; the form is filled
from your profile; Save As puts it in Documents; Edge uploads it on the site; it stops before **Submit**.
One task, one plan, no handoffs.

### Finding things out

1. Flash plans: what to search and what counts as an answer.
2. Edge opens, searches, opens the most promising results, and reads them as text (the page's
   structure, not screenshots). It can also open what it finds, like a downloaded PDF or a spreadsheet.
3. Flash-Lite pulls out what answers the question, and notes each fact's page.
4. The answer comes back to chat with its sources, and the agent can remember it.

The agent's existing web search (`web_search`, `web_research`) already answers simple questions without a
browser. This tool is for sites that need clicking: menus, search boxes, logins, multi-page lookups.

### Applying for something

1. **Your profile grows as you go.** It starts empty, and you decide what goes in, case by case. When a
   form needs something the profile doesn't have (your name, a username, your city, a resume file), it asks
   you in DeskAI with two choices: **Use once** or **Use and remember**. Remembered details can be viewed,
   changed or deleted any time (`profile show|edit`). It stays on your computer, never in git. Nothing is
   invented.
2. **Read the form:** every field with its label, type and choices, whether it's a web page or a
   fillable PDF or Word file.
3. **Fill it in one request:** one model call maps your profile to all the fields on the page. Then the
   tool types them in itself, field by field with the real mouse and keyboard, with no AI per field.
4. **Questions it can't answer** ("Why do you want this job?", "Years of experience with X?") go to you in
   DeskAI as a question. It never makes up an answer on an application.
5. **Stop before Submit:** DeskAI shows a card with screenshots of the filled pages. You tap **Submit** or
   change what's wrong. It never ticks "I certify" or "I agree" boxes for you.
6. **Sign-ins:** it signs in by itself (see "Signing in by itself" below), and prefers "Continue with
   Google" buttons once its Edge profile is signed in to Google.
7. **CAPTCHAs and "are you human" checks:** it stops and alerts you; you solve it from your phone on the
   Take over screen (below), and it continues after. It never solves one itself.

### Signing in by itself

The owner's choice: no signing in by hand. The agent logs in and asks only for the 6-digit code.

**Built first (2026-10-06, the owner's choice):** the owner types the password or code in DeskAI each time a
sign-in needs it (see "Where it stands"). It follows the same rule as below: the tool types it, and the AI never
sees it. Saved passwords in Credential Manager can come later, so it can sign in without asking.

- **Where passwords live:** Windows Credential Manager (through Python's `keyring`), encrypted with your
  Windows login. Not in files, not in git, not in the agent's memory, not in chat.
- **The AI never sees a password.** Your chat model is Gemini's free tier, and Google may use free-tier
  prompts for training. The model only ever sees a placeholder ("type the Google password into [7]"); the
  tool puts the real password into the field itself, after the model has decided.
- **Locked to their sites:** each password works only on the sites you list for it (Google: only
  `accounts.google.com`). If a page impersonates Google, or a page's text tells it to type the password
  somewhere else, the tool refuses and tells you. This is the protection against phishing and against
  web pages giving it orders.
- **Adding a password, from anywhere:** in DeskAI, never in chat (chat goes to the AI and into memory).
  See "What the owner provides, and when" below.
- **6-digit codes (authenticator app):** when a page asks for one, DeskAI shows "Google wants your
  6-digit code" with a box; you type it from your phone and it carries on. Codes last about 30 seconds, so
  it waits for yours, then enters it at once. For "tap Yes on your phone" prompts it just waits.
- **It stays signed in:** its own Edge profile keeps sessions, usually for weeks, so codes are rare after
  the first sign-in.
- **Why the real mouse and keyboard matter here:** Google blocks sign-ins from browsers driven by
  automation ("This browser or app may not be secure"). A normal browser typed into by the real keyboard
  doesn't have that problem. Phase 0 tests a real Google sign-in to confirm it.

### The sites' own rules (read this)

- **Giveaways:** many giveaway rules void entries made "by script, macro or other automated means." You
  approve every entry yourself, so each is your entry. It enters each giveaway once, at a normal human
  pace, and never mass-enters, which is what gets entries thrown out.
- **Social media** (Facebook, Instagram, X, LinkedIn and others): their terms ban automated use, and they do
  suspend accounts for it. On these sites it only reads, slowly, like a person scrolling, with limits per
  day. It never posts, comments, messages, follows or likes without your tap.
- **Job sites:** applying is fine with you approving each application. It applies one at a time, never in
  bulk. Some sites, like LinkedIn, also ban automation, so the same slow, read-mostly rule applies there.

### Working in apps

The same loop, in Word, Excel, Explorer, Settings, Notepad or any app: open it, read its list, click and
type, check. Windows dialogs that pop up mid-task (Save As, Open, a permission question) are just the next
thing in front. Uploading a file to a website goes through the real Open dialog, typing the file's path,
the way a person does.

### Scrolling

Tested on 2026-10-05 on a very long page (Wikipedia's Illinois article) in normal Chrome:

- Chrome's accessibility list covers **the whole page, not just what's visible**: 1,999 links, 75 marked
  on-screen and 1,922 marked off-screen with their positions further down. So the tool knows what's
  below before scrolling.
- The **real mouse wheel** brought a link from several screens down into view in 2 scrolls; only that
  link's position was re-checked each time.
- The page reports its scroll position (2% at the top), so the tool knows when it's at the bottom.
- Reading all 1,999 links took 15 s: fine once per page, too slow for every step.

How it handles each case:

1. **Clicking something further down:** the list shows off-screen items too, marked "(below)". When the
   model picks one, the tool scrolls itself with the real wheel until the item is in view, re-reads its
   position, and clicks. The model never has to manage scrolling.
2. **Reading a long page** (research, terms and rules pages): the page's text comes from the accessibility
   list in one go. Text only OCR can see (images, picture-drawn pages) is read screen by screen: scroll a
   screen, OCR it, join the pieces without repeats, and stop at the bottom (scroll position 100%) or when
   the screen stops changing.
3. **Endless feeds** (social media, search results that keep loading): stop after a set number of
   screens, or as soon as the question is answered.
4. **Scrolling inside part of a page or app** (a dropdown list, a chat panel, a pop-up, a long Settings
   list): the wheel is turned over that area, which reports its own scroll position.
5. **Apps that only list what's shown** (long lists in Explorer or Settings build their items as you
   scroll): scroll and read again, or ask the list for the item by name, which Windows supports for
   these lists.
6. **Speed:** a page is read fully once and kept; after that, each step re-reads only the visible part
   and the items it's tracking. Phase 1 also batches the reads (Windows can return many items' details in
   one request), which should make a full read several times faster.

### Spending the free AI allowance well

| Step | Model | Requests |
| --- | --- | --- |
| Plan a task | A Flash model | 1 |
| Each browsing or app step | Flash-Lite (500 a day), from text, not images | 1 per step |
| Fill a whole form page | Flash-Lite or Flash | 1 per page |
| Stuck twice | A Flash model replans | 0-2 |
| A task done before | Its recipe, no AI | 0 |

A lookup that reads 3 sites takes about 6-10 requests. Most applications take about 10-25: a few pages,
plus one fill per page. Screenshots to a model are the last resort.

## When it needs you: phone alerts

The owner may not be watching, so **anything that needs the owner's phone or answer sets off an alert**,
never just a quiet card in the app.

**The rule (the owner's): anything the tool needs from a person is pushed to the DeskAI app and
answered in the app.** Nothing ever requires sitting at the Mini.

- **A new "Needs you" channel in DeskAI:** loud (heads-up, its own sound, strong vibration), separate from
  the calm task and assistant notifications, so it's clear at a glance that the agent is stuck until you
  answer. Sent through the push setup the agent already has (high-priority FCM messages).
- **What triggers it:**

  | What happened | The alert says | You |
  | --- | --- | --- |
  | A site wants a 6-digit code | "Google wants your 6-digit code (expires soon)" | Type the code **right in the notification**, no need to open the app |
  | A "tap Yes on your phone" sign-in prompt | "Google sent a sign-in prompt to your phone: tap Yes" | Tap Yes in Google's own prompt; the agent sees it and carries on |
  | Ready to submit, send or post | "Ready to submit your application to X" | Approve or Show in the notification; Show opens the filled pages |
  | A question it can't answer | "The application asks: years of experience with forklifts?" | Answer in the notification or the app |
  | A detail the profile doesn't have | "The form needs your username for X: use once or remember?" | Answer in the app |
  | A CAPTCHA or "are you human" check | "A site wants a human check" | Solve it on the **Take over** screen |
  | Something it can't figure out | "Stuck on X: can you take a look?" | Take over, or answer in words |

- **Every request shows you what you're answering into** (the owner's ask): a screenshot of the Mini's
  screen comes with each one (code, password, profile detail, approval, question).
  - The field your answer goes into is **outlined** on it.
  - Above it, in words, the **site's address** as the tool read it from the address bar
    ("accounts.google.com"). For a password, also whether that address is one of the password's allowed
    sites: ✓ or ⚠. A look-alike page shows up right there.
  - It's in the notification itself (expanded picture) and on the screen where you answer.
  - Taken **before** anything is typed, so it never shows a password. The notification carries only a
    link; the app fetches the picture from the agent over Tailscale with its token. Kept only with that
    task's step log.
- **Take over, from the phone:** a DeskAI screen with a live picture of the Mini's screen. You tap and
  type on the picture; the tool repeats your taps and typing on the Mini with the real mouse and keyboard,
  so it's you doing it, from anywhere. Used for CAPTCHAs and anything unusual, so a task never dead-ends
  waiting for someone at the Mini. While you're in control the tool doesn't act; tap **Hand back** and it
  carries on from the new screen. Pictures go only over Tailscale with the app's token, refresh about
  once a second while the screen is open, and aren't kept.
- **It keeps reminding:** if there's no answer, it alerts again every minute 5 times, then twice more 10
  minutes apart (`REMINDERS` in `core/computer_use.py`). The tool waits 30 minutes in all
  (`requests_owner.WAIT`), then the task **pauses safely**, with nothing half-submitted. When you answer,
  the alert disappears from your phone.
- **Quiet is fine too:** progress and finished tasks stay on the calm task channel. Only "needs you" is
  loud.

**Built (phase 3 part 2, 2026-10-05):**

- **The tool:**
  - `requests_owner.ask()` writes `data/requests/<id>.json` and a screenshot (the spot outlined in red),
    then waits for `data/answers/<id>.json`.
  - `run --ask-owner` turns it on. The loop asks before a risky click (Approve/Deny), for a pop-up it
    can't answer by its own rules (that pop-up's choices), and when it has a question (text).
  - Answers go into the plan as "the owner said …".
- **The agent (eecc879):**
  - The daemon passes `--ask-owner`, and `computer_use.alert_owner()` sends the push (`type`/`channel`
    `needs_you`) with the reminders above.
  - `GET /api/computer/requests`, `GET /api/computer/requests/{id}/screenshot`, and
    `POST /api/computer/requests/{id}/answer`, documented in `docs/API.md`.
- **DeskAI (build 39):**
  - Its own loud channel, with the screenshot shown large.
  - Answers right in the notification: Approve/Deny, up to 3 choices, or a reply box. A 6-digit code will
    use the reply box.
  - A "Needs you" panel, and a banner in the chat while anything is waiting.

## Understand before acting (the owner's rule)

From a live demo on 2026-10-05: launching Notepad was expected to give a new blank page, but Windows 11
brought the owner's existing Notepad window to the front on one of their files. The demo typed into it and
pressed Ctrl+S, which saved over that file. The owner: "make sure the bot goes slow and understands before
it does things." So:

1. **It only touches what it made.** A task keeps track of the windows, tabs and files it created. The
   owner's own windows and files are **look-only**: typing into them or saving them is refused, unless
   the task names them, and even then a change goes to the owner first.
2. **It checks the situation before each step.** Each step states what it expects ("a new, empty page",
   "a Save dialog with a File name box"). If the screen isn't that, it stops, looks again, and asks rather
   than pushing ahead.
3. **It never saves blind.** Ctrl+S only on its own untitled page, which must bring up a Save dialog; it
   never overwrites an existing file. A program that reuses an open window (Windows 11 Notepad) gets a
   new tab (Ctrl+N), checked blank, first.
4. **It goes slowly:** a pause and a fresh look between steps, and its intention is written to the step
   log before it acts.

Built in `task.py` (commit 56ea0a7, with tests for exactly the case above).

## Accuracy first (the owner: "not worried about speed, more worried about accuracy")

Speed work (batching reads) is dropped unless it ever gets in the way. Instead, phase 2 builds:

- **Grounded actions only:** it acts only on items in the list it actually read (accessibility or OCR).
  Nothing a model "sees" that the screen reading doesn't confirm is ever clicked or typed into.
- **Expected result for every step,** checked before the next step starts (`task.expect`).
- **Two sources agree before anything that matters:** the accessibility list and OCR must agree on what a
  field is (its label) before it types into it.
- **Read back everything typed**, and compare what's on screen with what was meant.
- **When unsure, ask:** low confidence goes to the owner in DeskAI, with a screenshot, rather than a guess.

**Built in phase 2 (`loop.py`):**

- **Actions only on items it read:**
  - A step names an item number from the list the tool just read.
  - Typing or clicking outside the task's own windows is refused (`_may_touch`).
  - Typing is read back (`actions.type_into`).
- **Risky clicks need the owner's OK:** Submit, Send, Pay, Buy, Delete, Post, Install, Sign up, Register,
  Apply now, Enter giveaway and similar (`RISKY_CLICK`).
- **An answer needs evidence that names its subject.** The evidence must mention the goal's place or thing
  (`goal_subjects`), so Chicago's weather can't pass as Springfield's.
- **Every answer gets a second opinion** (`verify_answer`) against the page's own text before it's
  accepted.
- **Not built yet:** the accessibility list and OCR agreeing on a field's label before typing.

### When it's stuck: the ladder

1. **Look again:** a fresh read; free.
2. **Its own rules:** pop-ups, ownership, expectations.
3. **A local model as a second pair of eyes** (the owner's idea): free and unlimited on the Mini, about
   40 s an image on its CPU, so only when stuck. It answers **narrow questions** ("Is there a pop-up
   asking something?", "Which of these numbered items is the Apply button?", "Does this field show what was
   typed?"). Its answer is **checked against the accessibility and OCR lists**, and thrown out if it names
   something not there or contradicts the screen. It never decides an action alone: gemma3:4b invented a
   "10" on a tree and a "No Trespassing" sign in photos on 2026-10-05.
4. **Gemini Flash:** the stronger cloud model, for real decisions and re-planning.
5. **The owner, through DeskAI:** an alert with a screenshot, or Take over.

Built as `Loop.unstick()`. After 2 failed or unconfirmed steps in a row:

1. It answers pop-ups by its own rules.
2. It asks gemma3:4b two seeing-only questions: is there a pop-up, and what is the screen showing.
   A "yes, a pop-up" the tool can't find is marked unconfirmed.
3. It re-plans with a Flash model.

The owner comes in through "Needs you" (built). Take over isn't built yet.

**Measured on the Mini (2026-10-05):** 12 questions on 3 screenshots from the live runs, cropped to the
browser (Edge with the sync pop-up, the weather results page, Notepad's Save dialog), with known answers,
including 2 trick questions about things that aren't there.

| | gemma3:4b | qwen3-vl:2b |
| --- | --- | --- |
| Is there a pop-up or dialog? | 3/3 | 2/3 |
| Read labels and text (buttons, File name box, temperature, Save) | 4/4 | 3/4 |
| Trick questions (no error message, no phone number) | 2/2, invented nothing | 1/2 |
| Pick the right numbered item | 1/2 (chose "Customize" to dismiss instead of "Got it") | 0/2 |
| A value in a dense grid (Wed high/low) | wrong: read Thursday's 88/61 for 85/56 | blank |
| Total, time per answer | **10/12**, about 25 s | 6/12, about 5 s but half its answers empty |

So step 3 uses **gemma3:4b** with these rules:

- **Seeing questions only:** yes/no ("is there a pop-up?") and "what does this say?". It got all of those
  right and invented nothing.
- **It never chooses an action.** Choices come from the tool's rules, the accessibility list, Gemini or the
  owner. Its wrong choice here (Customize) is the mistake this rule prevents.
- **It never supplies values** (prices, dates, temperatures): those are always read from the accessibility
  or OCR text. It can look at the right place and still read the wrong column.
- **Only when stuck:** about 25 s an answer on the Mini's CPU.
- qwen3-vl:2b isn't used: too many empty answers (as in its photo test).

## Reliability rules for the real mouse and keyboard

From an outside AI's review (2026-10-05), checked against the tests. All of these were built in phase 1
(`screen.py`, `actions.py`, `popups.py`, `hands.py`) except the cross-site frame test.

- **Wait until the page is ready, never a fixed time.** Chrome's page list is empty for about 2 s after
  a page loads, and items vanish while it fills in (both seen in the tests). The tool waits until the list
  is filled and unchanged over two reads. It redoes that wait whenever the page changes (a new address or
  title, or a single-page site redrawing itself), with a time limit.
- **Never type blind.** Keystrokes go to whatever window has focus, so a pop-up that steals it would
  catch them. Before typing, it confirms the right window is in front and the right field has the
  keyboard (Windows reports both). If not, it deals with what's in the way (next point).
- **Pop-ups: see them, read them, answer them.** Tested on 2026-10-05 in Edge, where a fresh profile showed
  a pop-up over the form ("We are now syncing your browsing data…"). A first run without this rule
  clicked the pop-up instead of the field, and the typing went nowhere. With it:
  1. **Notice:** before typing, the keyboard isn't on the field, or Windows says something else is at the
     spot it's about to click.
  2. **Find the pop-up:** the panel holding the keyboard, whatever covers the target, or another small
     window of the browser or app.
  3. **Read it** two ways: its choices from the accessibility list ("Microsoft Privacy Statement", "Got it",
     "Customize") and its text by OCR of just that area.
  4. **Answer it:** harmless pop-ups (privacy notices, "make this your default", notification requests,
     tips) get the dismissing answer ("Got it", "No thanks", "Not now", "Close"). Cookie banners get
     "Reject non-essential" where there is one. It never picks "Allow", "Turn on", "Sign in", "Accept all",
     "Subscribe" or "Buy" on its own. Anything else goes to the model with the pop-up's text and choices,
     and risky ones (permissions, "Leave site?", payment) go to you as a "Needs you" alert.
  5. **Check it's gone,** then go back to the step it was on.

  In the test: it found the 500×320 pop-up, read its text and 3 choices, clicked "Got it" with the real
  mouse, then typed "Tëst Námé" (accents included, through `pynput`) and read it back correctly.
- **Read typing back.** After typing, it reads the field's value; if it isn't what was typed, it clears and
  retries once, then stops and says why.
- **Fewer pop-ups to begin with:** the browser profile is set up once on the Mini (first-run screens done,
  notifications and default-browser prompts off), so most never appear.
- **Type, don't paste.** Text goes in as keystrokes at a human pace. Passwords never touch the clipboard:
  Windows can keep clipboard history and sync it to the cloud. If long text (a cover letter) is ever
  pasted for speed, your clipboard is saved first and put back after.
- **Any character, not just English.** PyAutoGUI's typing only handles plain English characters, not
  é, ñ, curly quotes or emoji, which turn up in names and letters. So it types through Windows' Unicode
  keyboard input (`pynput`) instead.
- **Embedded frames:** sign-in, payment and job-portal pages often show part of the page in a frame from
  another site. Chrome usually passes those to the accessibility list, sometimes late. OCR runs on every
  look anyway, so a frame that shows nothing to the accessibility list is still read and clickable.
  Phase 1 tests a page with a cross-site frame.

## Safety

- **Stops before anything that counts:** Submit, Send, Pay, Buy, Delete, Post, Install, or changing a
  setting. These need your tap in DeskAI. Once a kind of task has gone right several times (say, giveaway
  sign-ups), you can let that kind submit without asking. Job applications always ask.
- **Passwords:** only from Credential Manager, only on their own sites, never shown to the AI (see
  "Signing in by itself").
- **Never:** card numbers, bank details, Social Security number, solving CAPTCHAs, ticking legal
  agreements, or admin prompts (UAC).
- **Its own Edge profile:** your personal browsing, saved passwords and logged-in accounts are not in
  it.
- **Kill switch:** Ctrl+Alt+End stops everything (built). Slamming the mouse into the top-left corner is
  planned but not built; moving the mouse at all already pauses it (next point).
- **It gives way to you:** before every click, keypress or scroll it checks the mouse is where it left it.
  If you've moved it, you've taken over, so it pauses instead of clicking or scrolling under your hand.
  (A test on 2026-10-05 without this check scrolled for minutes; it's the first thing phase 1 builds.)
- **Allow-list** of apps and websites per task. It doesn't open banking sites or password managers.
  (Not built yet. Today a task may use only the windows it opened itself, plus any of the owner's that
  `--allow` names.)
- **Web pages are data, not instructions:** text on a page can't redirect it ("ignore your instructions
  and..."), the same rule as email today.
- **Full log** of every step with a screenshot, so you can see exactly what it did.
- **Limits** on steps and minutes per task.

## Where it runs

**It lives on the Mini** (the owner's choice: the laptop is on its last legs). The laptop is for building
and testing, with the owner watching.

Checked on the Mini, 2026-10-05:

| | The Mini | Means |
| --- | --- | --- |
| Windows | 11 Pro | Windows Sandbox and Hyper-V are there if an isolated space is ever wanted |
| Desktop | `owner` signed in automatically, console session, active and unlocked; no lock screen set | It can read the screen and use the mouse and keyboard. Keep the lock off: a locked screen stops it |
| The agent | Runs inside that same desktop session | The agent starts the tool, so the tool lands on the right desktop |
| Screen off | After 15 min | Fine; a dark screen doesn't lock |
| Resolution | **1024×768, no monitor detected** while checked | A monitor is connected (the owner), but it sleeps when idle, and many monitors disconnect when asleep, so Windows falls back to 1024×768. Fix below |
| Browser | **Edge** (the owner's choice: already installed, lighter on memory); Chrome isn't installed | Same engine as Chrome: reading, the real mouse and keyboard, and pop-up handling all tested in Edge |
| Python 3.13, Tesseract, Windows OCR (English) | Installed | Ready |
| Free memory | 12.3 of 19.8 GB | Plenty |

- **Edge signs itself in to your account:** even a fresh profile was signed in automatically with the
  account Windows uses (seen in the pop-up test), which would bring your synced passwords and history.
  The tool's Edge profile has that turned off (Edge has a setting for it), so it stays separate from your
  personal browsing and signs in only where a task needs it, from Credential Manager.
- **Waking the screen:** before each task the tool tells Windows to keep the display on (the same
  signal video players use) and nudges the mouse. Then it waits until the screen is back at its normal
  resolution before reading anything. When the task ends it lets go, and the monitor sleeps as usual.
  Confirmed 2026-10-05: the owner watched the sleeping monitor wake during a run on the Mini. It must stay
  switched **on**: switched off, it disconnects and Windows falls back to 1024×768.
- **It shares the desktop with the agent and video renders.** A render needs every core, so computer tasks
  and renders take turns: a computer task waits for a render to finish, the same way image descriptions
  already do.
- **Starting it on the Mini:** remote commands over SSH run outside the desktop and can't see the screen.
  - **Real tasks:** the agent starts them, since it runs in the desktop session.
  - **Tests:** `mini/run_in_session.ps1` in the tool's repo. It runs one command through a one-off
    scheduled task with an interactive logon and prints the output.
  - **Install:** the project is at `C:\Projects\AgentComputerUse`, with its packages in the agent's
    Python 3.13 (no venv on the Mini). The tests pass there.
- **The owner's own use of the Mini's desktop** is rare (it's a server), so a separate account isn't
  needed now. If it ever is, Hyper-V on Windows 11 Pro is the option.

## How it plugs into the agent

A separate project at `C:\Projects\AgentComputerUse`, run as a command line with one JSON result, like the
other tools:

**Built:**

- `run "<goal>" [--go] [--close] [--ask-owner] [--max-steps N] [--seconds S] [--allow ...]`
  - Does anything, and is a dry run without `--go`.
  - `--close` closes its windows at the end.
  - `--ask-owner` waits for the owner instead of stopping. Waiting time is added to the time limit.
- `observe` / `act`: one look or one action (for testing).
- `windows`: the open windows.
- `screen [--wake]`: the screen size; wakes a sleeping monitor; says whether a render is running.
- `demo`: the phase 1 check (Edge search, then Notepad, then a saved file).

**Planned:**

- `research "<question>"`
- `apply "<url or what to apply for>"`
- `profile show|edit`
- `recipes list|show|delete`

**In the agent (built):**

1. You ask in chat, and the `computer_task(goal)` tool proposes a **Computer task** card.
2. You tap Run. It's queued as engine `computer` (`core/actions.queue_computer`), with a 1-hour limit.
3. `daemon._execute_computer` runs `run <goal> --go --close --ask-owner`.
4. Each step shows on the live task card as "Step N: …".
5. "Needs you" alerts come as above.
6. The task counts as done only if the tool's status is "done".

Not built yet: each step's screenshot on the live card.

## Plan of work

| Phase | What | Done when | Effort | Status (2026-10-05) |
| --- | --- | --- | --- | --- |
| 0. Try what exists | Run Windows-Use in a throwaway environment on 3 small tasks with a free Gemini model (Chrome and apps); test a real Google sign-in in normal Chrome with the real keyboard | We know what works, what breaks, requests per task, and that Google sign-in works | 1 short session | Windows-Use tried; **Google sign-in test not done** |
| 1. Eyes and hands, both sides | AgentComputerUse project; one `observe` that reads Edge pages and Windows apps into one list; one `act` (real mouse and keyboard, pop-up handling); kill switch, dry run, own Edge profile, step log with screenshots (no AI yet) | A script (no AI) searches in Edge, saves the result into Notepad, and saves the file | 2 sessions | **Done** (demo passed on the laptop and the Mini) |
| 1b. Ready on the Mini | Waking the sleeping monitor and waiting for its full resolution (virtual display driver only if that fails), Edge with its own profile (automatic sign-in off, first-run screens done, prompts off), the project on the Mini, started from the agent inside the desktop session, taking turns with renders | The phase 1 script runs on the Mini, started from the agent, while nobody watches | 1 session | **Done** (no virtual display needed). Automatic sign-in isn't turned off yet |
| 2. The loop | Plan with Flash, steps with Flash-Lite, check after each step, limits, allow-list | It does the cross-over test tasks below without a script | 1-2 sessions | **Done** for a look-up (71°F, verified). Cross-over test tasks not run yet; site allow-list not built |
| 3. In the agent and DeskAI | Chat tool, Computer task card, live step card, the loud "Needs you" alerts with code box and approve buttons, reminders and safe pause, the Take over screen, the Passwords screen | You start a task from your phone, and a test sign-in alerts you and takes your code from the notification | 1-2 sessions | Parts 1 (chat card, live card), 2 (Needs you, sign-ins from the app) and 3 (Take over, Mini and laptop) **done**. Next: part 4 saved passwords |
| 4. Forms and applications | Profile file, fill a page in one request, questions to DeskAI, stop before Submit | It fills a practice application (web and PDF) and waits for your tap | 2 sessions | Not started |
| 5. Recipes | Save successful runs; replay with no AI | A repeated task uses 0 AI requests | 1 session | Not started |

**Test tasks (several cross between the browser and apps on purpose):**

- Look up a fact that needs clicking through a site, and write the answer into a Notepad file.
- Compare prices of one item on 3 sites and put them in an Excel sheet.
- Download a PDF from a site and save it into a folder it makes in Explorer.
- Fill in a practice job application and a practice giveaway sign-up (test pages we make, so nothing
  real is submitted), including a field the profile doesn't have yet.
- Look something up on a site you're signed in to (read only).
- Change a Windows setting (night light or volume).

Each is scored on success, steps, AI requests and time.

## Libraries (all free, checked for Python 3.13)

| Library | For |
| --- | --- |
| `uiautomation` 2.0.29, `pywinauto` 0.6.9 | Reading Chrome pages and Windows apps (accessibility tree; both tested today) |
| `mss` 10.2 | Screenshots (tested today) |
| `pynput` 1.8, `pyautogui` 0.9.54 | The real mouse and keyboard, the only way it acts (`pynput` types any character); the kill-switch hotkey |
| `winrt-Windows.Media.Ocr` (Windows' built-in OCR) | The OCR pass on every look (tested today: 0.6 s for the screen) |
| `opencv-python` 5.0, `pytesseract` + Tesseract 5.4 | Finding images on screen; a slower second opinion for hard text |
| `keyring` | Passwords in Windows Credential Manager |

## Starter questions (made up until the owner has real ones)

Each needs real clicking through sites, not just a search snippet:

1. "Find 3 jobs posted this week within 30 miles of Springfield, IL that list the pay, and put them in an
   Excel sheet with the link, pay and how to apply." (job site search filters, then Excel)
2. "Find 3 free giveaways that end this month, need no purchase, and are open to Illinois residents. What
   does each one need to enter?" (reading the official rules pages)
3. "When is Illinois State's next home football game, and what's the cheapest ticket listed?" (schedule
   page, then the ticket site)

## What the owner provides, and when

| What | Where it goes | When |
| --- | --- | --- |
| AI models | Nothing new: the agent's Gemini key, already in the Mini's `.env` | Already done |
| Passwords (Google, job sites…) | **Windows Credential Manager** on the Mini. Never `.env`, chat, memory or git: `.env` is plain text that other tools and backups read | From the DeskAI app, anywhere, once sign-in is built (phases 3-4): one site at a time, or when an alert asks |
| 6-digit codes | Typed into the DeskAI "Needs you" alert | During tasks |
| Profile details (name, username…) | The tool's profile file on the Mini; it asks "use once or remember?" | During tasks |

**Adding a password, from anywhere (the owner: no sitting at the Mini):**

1. **A "Passwords" screen in DeskAI:** pick a site (or type its address), type the password in a hidden
   field, tap Save. It goes to its own locked-down agent endpoint, `POST /api/computer/credentials`, not
   the chat. The agent runs inside the owner's signed-in desktop on the Mini, so it can store the
   password in Credential Manager properly; an SSH session couldn't.
2. **Or at the moment it's needed:** a task reaches a sign-in page with no saved password, and a "Needs
   you" alert says "Edge needs your password for indeed.com". Tapping it opens the same screen, already
   set to that site.
3. **Afterwards:** the screen lists which sites have a password saved (and which sites each one may be
   used on), with Replace and Delete. A password can **never be read back**, not in the app and not
   through any endpoint.

The screen shows the request's screenshot first: the sign-in page with the password field outlined and
the site's address checked against the password's allowed sites (✓ or ⚠), so you see where it's going
before you type it.

Protections on that path:

- Accepted only over Tailscale (the owner's encrypted private network) or from the Mini itself, never
  from the open internet; the request also needs the app's token.
- The request body is never logged, never stored anywhere but Credential Manager, never passed to the AI
  or memory, and never echoed back in a response.
- The DeskAI screen blocks screenshots and screen recording (Android's secure-window flag), and the field
  is cleared as soon as it's sent.
- Each password is locked to the sites listed for it, as before.

**Edge sign-in:** the Mini's Windows isn't signed in to the owner's Microsoft account, so Edge there won't
sign itself in. The tool's Edge profile still turns automatic sign-in off, in that profile only, so this
holds even if that ever changes. The owner's own Edge is untouched.

## Decisions

1. **Name:** AgentComputerUse.
2. **Applications:** jobs and free giveaway sign-ups.
3. **Profile:** whatever the owner decides each time; it asks "use once" or "use and remember".
4. **Sign-ins:** the agent signs in by itself; the owner only gives 6-digit codes through DeskAI. For now
   (2026-10-06) the owner also types passwords in DeskAI when asked, and can pick the page's other ways to sign in
   ("Try another way"). Either way the password never reaches the AI.
5. **Test questions:** the made-up ones above, until the owner has real ones.
6. **Still open:** which kinds of tasks may submit without asking, decided later once each has worked
   several times. Job applications always ask.

## Sources

- [Browser Use on GitHub](https://github.com/browser-use/browser-use): MIT; drives Chrome over its debugging connection (CDP), which is why it isn't used here.
- [Windows-Use on GitHub](https://github.com/CursorTouch/Windows-Use): MIT; accessibility tree, no vision
  model needed; recommends a VM or Sandbox.
- [Microsoft UFO](https://github.com/microsoft/UFO) and the [UFO² paper](https://arxiv.org/html/2504.14603v1).
- [Turing Post: computer-use agents in 2026](https://www.turingpost.com/p/computer-use-ai-agents).
- [docs/GEMINI_FREE_TIER.md](../GEMINI_FREE_TIER.md): the free model limits this plan is built around.
- The laptop test on 2026-10-05 (Python 3.13.7, `uiautomation`, `mss`), and PyPI versions checked that day.
