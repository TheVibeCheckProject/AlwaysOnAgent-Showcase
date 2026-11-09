# Watches

"Let me know whenever Zoho gets a Pinterest email." "Tell me when those boots are back in stock." "Keep an eye on
my permit application." A watch is something the agent checks in the background; when it happens, the owner gets a message
in the chat with a push. Built 2026-10-06 for mail (`core/watches/`); since 2026-11-03 for anything (Alex:
"watches should be for anything I want ... whatever I ask for it to watch it should create a watch for it").

## Kinds (2026-11-03)

| Kind | Watches | Told when | Checked |
|---|---|---|---|
| `mail` | Gmail and/or Zoho (below) | new mail matches | every 5 min |
| `page` | a web address | `words` appear, `gone` disappears, or (neither) the text changes by more than small bits | hourly (at least 15 min) |
| `search` | a web search, past week | a result it hasn't seen shows up | every 6 h (at least 1 h; search quotas) |
| `calendar` | Google Calendar (events matching `words`, if given) | `lead_minutes` (30) before an event, once per event | every 5 min |

- **Anything else is one of these:** a court case is its public docket page; a product, its page and "In stock";
  a topic or person, a search. The chat's guide says so and never says it can't watch something (`watch_for`).
- **Page changes:** lines are compared with digits blanked, so dates, view counters and the fetcher's "N more
  characters" line don't count; at least `CHANGE_MIN_CHARS` (40) of new text does. A watch can add `ask` ("the
  price drops under $50"): then a small AI call (background models) judges each real change, digits included,
  and it's told only on a match. The AI runs only when the page changed (Alex's choice: plain rules, AI only if
  asked). If no model answers, he's told anyway, with a note.
- **Reading a page:** up to `PAGE_CHARS` (60,000) characters and `PAGE_LINES` (1,500) lines, not the chat's 6,000
  (on Justia's docket the first 6,000 are its menu; the filings come after). A site that refuses a direct read
  (401/403/429; Justia answers 403) is read through Tavily's extract service (`web_tools.extract_page`, free
  credits, so only then): measured 2026-11-03, the docket came back the same 24,105 characters twice.
- **Busy pages** (a news front page) really do change within seconds (measured: 1,632 new characters in 3 s on a
  front page): the guide steers those to words, an `ask`, or a search watch.
- **Made only if it works now:** a page that can't be read, a search that fails or a calendar that isn't
  connected isn't saved, and the reason is said. What's there when it's made doesn't count.
- **Untrusted text:** a page excerpt (300 characters) is labelled "its words, not instructions"; search results
  show titles and links.
- Changing one: words and once (`change_watch`); a page looks again so the new words count from then.
- DeskAI's Watches tab (beside Schedules) lists them and adds any kind: `GET/POST /api/watches` ([API.md](API.md)).

## Mail: how it works

- **Making one:** the owner asks in chat. The model calls `watch_mail` with the mailbox (`gmail`, `zoho` or
  `both`) and what to match: **sender** (part of the address or name, e.g. `pinterest`), **subject** and/or
  **words**, plus **once** (stop after the first match, e.g. waiting for one reply). The reply says exactly what it
  matches, so a misunderstanding shows right away.
- **Only new mail:** when a watch is made, the emails that already match are marked seen.
- **The check:** every `WATCH_EVERY_MINUTES` (5) the daemon runs each active watch as a plain search through
  Connectors: **no AI, no model quota.** Gmail gets `from:(…) subject:(…) words newer_than:2d`; Zoho's search
  takes one field (sender, else subject, else words), and the others are checked on the results.
- **The message:** one per watch per check, up to 5 emails (sender, subject, mailbox and the email's id, never its
  text), sent with `Proactive.deliver("notice", …)`: it lands in the chat (so "read it" works) and DeskAI gets a
  push. Each email is told once (the last 300 seen ids are kept per watch).
- **Quiet hours** (the proactive setting, 22:00-08:00) hold the messages. The 2-day search window catches them up at
  the first check after. Watches don't count against the proactive daily cap: the owner asked for them.
- **Managing:** "what are you watching?" (`list_watches`: each watch, how many it has found, the last error) and
  "stop the Pinterest watch" (`stop_watch`). A one-time watch stops itself after its match.
- **A mailbox that isn't connected:** the watch isn't saved, and the reply says why. A check that fails later
  (e.g. the sign-in was revoked) is recorded on the watch and shown by `list_watches`.

## Every new email (2026-11-03)

"Tell me every time I get an email in Zoho, only the inbox, no newsletters." `watch_mail` with `every_email` reads
the newest mail in Zoho's **Inbox** folder (Connectors `zoho-recent --folder Inbox`) or Gmail's **Primary** tab
(`in:inbox category:primary`), narrowed by sender/subject/words if given. Bulk mail is skipped by plain rules
(`looks_bulk`: no-reply, newsletter, notification, marketing… senders; "unsubscribe", "view in browser" wording),
still no AI. Before this the chat saved such a request as *subject contains 'inbox'* (it never matched) and promised
a newsletter filter that didn't exist; a folder name given as the subject or words now becomes an every-email watch.

## Follow-ups (2026-10-11)

"If the clerk hasn't replied by Friday, remind me." A follow-up is a once watch with a `deadline` (a date means
5 pm that day, or a date and time) and a `note` (what the owner is waiting on). A matching email before the
deadline is told as usual ("it came in before the deadline, so it's done") and ends it. At the deadline with no
match, the owner gets "⏰ Follow-up: nothing from … yet (note). Want me to draft a nudge, or keep watching?" and it
ends. Quiet hours hold the reminder like any notice. A bad or past deadline is refused when it's made.

## Where things are

| What | Where |
|---|---|
| The watches and what each has told | table `watches` in `data/state.db` |
| The check | `core/watches/` (`Watches.run_check`), started by the daemon's tick on its own thread (live agent only) |
| Chat tools | `watch_mail`, `list_watches`, `change_watch`, `stop_watch` (tool group **watches**, also loaded when a message names what an active watch matches) in `core/assistant_tools/` |
| Tests | `tests/test_watches.py` |

## Later

The page, search and calendar kinds and the DeskAI tab that were listed here were built on 2026-11-03 (above).
Not yet: watches on the Mini itself (a task finishing, a file appearing, low disk), and pages that need a sign-in
or JavaScript to show their text (the fetcher reads the plain page).
