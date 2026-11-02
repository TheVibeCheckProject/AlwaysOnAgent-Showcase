# Watches

"Let me know whenever Zoho gets a Pinterest email." A watch is a saved mail search the agent runs in the
background. When new mail matches, the owner gets a message in the chat with a push. Built 2026-10-06
(`core/watches.py`); the first version watches mail only.

## How it works

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

## Follow-ups (2026-10-11)

"If the landlord hasn't replied by Friday, remind me." A follow-up is a once watch with a `deadline` (a date means
5 pm that day, or a date and time) and a `note` (what the owner is waiting on). A matching email before the
deadline is told as usual ("it came in before the deadline, so it's done") and ends it. At the deadline with no
match, the owner gets "⏰ Follow-up: nothing from … yet (note). Want me to draft a nudge, or keep watching?" and it
ends. Quiet hours hold the reminder like any notice. A bad or past deadline is refused when it's made.

## Where things are

| What | Where |
|---|---|
| The watches and what each has told | table `watches` in `data/state.db` |
| The check | `core/watches.py` (`Watches.run_check`), started by the daemon's tick on its own thread (live agent only) |
| Chat tools | `watch_mail`, `list_watches`, `change_watch`, `stop_watch` (tool group **watches**, also loaded when a message names what an active watch matches) in `core/assistant_tools.py` |
| Tests | `tests/test_watches.py` |

## Later (same system, new sources)

- A web page changing ("tell me when this is back in stock"): needs a small AI call to judge whether a change
  matters.
- A calendar event coming up.
- A DeskAI screen listing the watches (chat is enough for now).
