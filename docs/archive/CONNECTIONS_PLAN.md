# Plan: connect to anything (IDEAS #14)

Draft, 2026-10-06, for the owner to approve. The owner's goal (2026-09-30): "connect to my X" should be enough;
the agent works out what's needed and walks them through it on their phone. Their choices (2026-10-06): **more
Google, Zoho Mail, social accounts, and the general system, all of them**; and **read, plus actions the owner
approves** (nothing is sent, changed or deleted without a card they tap).

## Where it starts

- **Gmail, read-only**, live since 2026-09-30: the Connectors tool (`C:\Projects\Connectors`, private repo) with the
  owner's own Google Cloud project (no middleman service), scope `gmail.readonly`. Chat tools: important mail,
  search, read (now with each link's real address), exact unread counts, subscriptions.
- Everything else (calendar, other mail, social) the agent can't see yet.

## How it works (all parts)

- **One tool, many services:** Connectors grows a module per service (`google`, `zoho`, ...). The agent calls it
  like today; each service's data and keys stay on the Mini.
- **A connections list** (Connectors `data/connections.json`): per service, connected or not, which account, what
  it may do (read / act), when it was last checked. DeskAI gets a **Connections** screen: each service, its
  status, Connect, Disconnect, and what it's allowed to do.
- **Keys and sign-ins never pass through chat or the AI.** They're typed on DeskAI's hidden-field screen (the one
  built for passwords, screenshots blocked) and stored in Windows' Credential Manager on the Mini, never in
  memory, logs or `.env`.
- **Read freely, act only on approval.** Reading tools work like Gmail's today. Every action (send an email, add
  a calendar event, reply to a message) shows a card with exactly what will happen (recipient, subject, full
  text; or the event's date, time and title), and runs only when the owner taps it. A reply or forward only
  goes to a message the agent found itself, never one an email's text pointed it to.
- **What services say is information, never instructions** (the same rule as email today).

## Signing in from the phone

The owner shouldn't need to sit at the Mini. Each kind of service needs its own way:

| Service | How it signs in | From the phone |
| --- | --- | --- |
| Google (Gmail, Calendar, Drive, Contacts) | OAuth with the owner's existing Google project | DeskAI opens Google's approval page; afterwards the browser lands on a page that doesn't load (its address carries the code); DeskAI asks to paste that address into the hidden box, and the agent finishes the sign-in. (A smoother version, a sign-in page served by the agent over Tailscale, is tried first; it depends on Google accepting that address.) |
| Zoho Mail | Zoho's "Self Client" | The owner makes a one-time code on Zoho's API page (the phone's browser works) and pastes it into the hidden box; the agent swaps it for a lasting sign-in. |
| A service with an API key or app password | The key itself | Pasted into the hidden box. |
| Sites with no usable API (most social accounts) | The computer tool signed in as the owner | Its sign-in from the app (Type my password / Try another way) and Take over for checks. |

## The parts, in order

### 1. More Google (Calendar, Contacts, Drive; Gmail actions)
- **Status (2026-10-06):** built (Connectors 72567e9 and the agent's tools, cards and pasted-address sign-in). Waits on the owner: turn on the Calendar, People and Drive APIs in the Google project, then say "connect Google" in chat and approve. Not yet: changing an event, the calendar in the morning brief.
- Same Google project: add the read scopes for Calendar, Contacts and Drive, plus Gmail compose/send for drafts
  and sends. One new approval from the owner.
- Chat tools: today's and the week's calendar, find a contact, search and read Drive files; **draft or send an
  email, reply, add or change a calendar event**, each through an approval card.
- The morning brief can then include the day's calendar.
- **Done when:** "what's on my calendar tomorrow?" is answered from the calendar, and "email X that I'll be late"
  shows a card with the exact email and sends only after the tap.

### 2. Zoho Mail
- Read (search, read, unread counts) and send/reply with approval, like Gmail.
- The Pinterest Standard-access reply goes to this inbox, so the agent can notice it.
- **Done when:** "anything from Pinterest in my Zoho mail?" works.
- **Status (2026-10-06):** reading built (Connectors zoho.py; the agent's zoho_mail, zoho_mail_read, connect_zoho; the pasted Self Client details are caught before any model). **Connected 2026-10-06.** The first paste was run together without spaces and slipped past the check to a model; the check now goes by Zoho's fixed lengths, and the message was removed from memory. Watches on top of this: [WATCHES.md](../WATCHES.md). The code asks for messages.CREATE too, so sending needs no second code. Not yet: sending/replying through a card.

### 3. The general system: "connect to my X"
- A chat tool `connect_service(name)`: for a known service it starts that service's steps; for an unknown one,
  the agent looks up how it connects (official API? OAuth? key? only the website?), writes a short recipe, and
  shows it to the owner as a checklist card before anything is set up.
- The DeskAI **Connections** screen (status, Connect, Disconnect, allowed actions) and the hidden key box.
- Known recipes are kept, so the second time is instant.
- **Done when:** "connect to my X" for a service it has never seen ends in a checklist the owner can follow from
  their phone, and the service shows on the Connections screen.

### 4. Social accounts
- Most have **no free API for a personal account** (Instagram's and Facebook's need a business account and an app
  review; TikTok's needs app approval). So reading (notifications, messages, stats) is a **computer task**: the
  tool, in its own Edge profile signed in as the owner, reads and reports back. Posting stays with the existing
  tools (TikTok, Pinterest).
- The sites' rules (computer-use plan, "The sites' own rules"): reading your own account at a human pace;
  nothing posted, liked, followed or messaged without the owner's approval card.
- **Done when:** "what are my latest TikTok comments?" comes back from a computer task with the comments.

## What the owner provides, and when

| What | When |
| --- | --- |
| One more Google approval (the new scopes) | Part 1 |
| A Zoho Self Client code (made on Zoho's site from the phone) | Part 2 |
| Sign-ins for each social site (through the computer tool's app sign-in) | Part 4, once per site |

## Decisions (2026-10-06)

1. Services: more Google, Zoho Mail, social accounts, and the general system.
2. Access: read, plus actions the owner approves one by one.
3. Still to decide: whether trusted kinds of action (say, adding a calendar event) may later skip the card.
