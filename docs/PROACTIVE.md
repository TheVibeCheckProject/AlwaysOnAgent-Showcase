# Proactiveness: the agent comes to the owner first

The agent used to speak only when spoken to. Now it also reaches out: a morning brief, things worth knowing,
suggestions, follow-ups on things the owner is **waiting on** (looked up on its own when they're public),
**curious questions**, and **background research** while the owner is away. The owner asked for "a little more than
only what matters", especially for natural curiosity, and for research to happen in the background while keeping chat
responsive.

Code:
- `core/proactive/`: the proactive engine, routing, freshness checks, silence check-in, curate switches, and guardrails.
- `core/candidates/`: `CandidatePool`, novelty and repeat detection by subject embeddings, AI ranker, scoring, and implicit feedback.
- `core/away_shift/`: background worker running sessions when the app has been closed for 2 hours.
- `core/open_loops/`: tracks memories waiting on external outcomes and coordinates research schedules.
- `interfaces/hud/companion.py`: `GET /api/inbox` (with `why`), `POST /api/inbox/{id}/feedback`, `GET /api/proactive/candidates`.
- `interfaces/hud/events_api.py`: `GET /api/activity`, presence tracking (`app_last_seen`), live SSE `event: activity`.
- `core/assistant_tools/`: `proactive_settings` (general settings, per-kind switches, curated interests/exclusions) and `background_status`.

Tests: `tests/test_proactive.py`, `tests/test_candidates.py`, `tests/test_away_shift.py`, `tests/test_live_events.py`.

## What it sends

| Kind | When | Example |
|---|---|---|
| `review` | Sundays, after the numbers report (not from this cycle: `core/growth_review.py`) | the week's growth review, with a `canvas` draft card in the inbox and candidate pool metrics |
| `memory` | Saturdays from 11:00, on the proactive thread after the cycle (`core/memory_review.py`); outside quiet hours, not counted in the daily cap | the weekly memory check: new facts to remember and out-of-date ones to retire, numbered; answered in DeskAI's checklist or in chat ("keep 1 and 3") |
| `brief` | `brief_time` "auto" (the default since 2026-10-27): when Alex shows up, 18 h after the last one, written ahead while he's away; or once a day after a time he sets. Goes to the Today screen's header (`brief_to` "today", the default since 10-28) or the chat ("chat") | what's still to come today, important email, TikTok posts done in the last 24 h (with their times), weather, model health line, and pooled "today" candidate items |
| `notice` | something worth knowing came up | "An email from GoDaddy says your domain renews Friday" · "You said you'd call the venue today" |
| `suggestion` | an idea worth offering | "Pinterest impressions jumped after Tuesday's pins; want more like those?" |
| `notice` (open loops) | a memory that was waiting on something settled, found in chat, mail or public pages (`core/open_loops/`) | "🧠 I updated my memory: • The city's decision on his building permit → “DECISION approving the application for a building permit” (from a web page: …)" |
| `question` (open loops) | the research found something happened but not how it came out, or nothing settled it past its check date | "🔎 You were waiting on …. I looked it up: … It doesn't say how it came out. Do you know?" · "Last I knew, you were waiting on …. Did that come through?" |
| `question` (curious) | wondering about one specific thing the owner said or cares about | "What kind of footage do you usually catch on your trail cam at the Pine Lake cabin?" · "Turns out Drake Anthony has been uploading since 2006. What project of his caught your eye first? (link)" |

## Architecture: Candidate Pool & Away Shift

### 1. The Candidate Pool (`core/candidates/`)
Rather than generating messages on the fly at send time, candidate ideas are written to `proactive_candidates` by
generators (signals, open loops, curiosity, away shift research, pattern observations).

- **Urgency:** `now` (time-sensitive, e.g. imminent renewal or due reminder), `today` (folded into the brief), `whenever` (sent at a good moment or silence check-in).
- **Novelty & Repeat Checks:** Every candidate carries a short subject. New candidates are compared by subject embedding against all past questions and recent owner messages. Repeats (similarity ≥ 0.64) or subjects Alex has discussed lately (≥ 0.65 in messages of 6+ words or matching names) are dropped before ranking.
- **Ranking & Scoring:** Background AI ranks candidates (`RANK_PROMPT`). Score is computed from rank, novelty, and variety penalty (when the last sent message was from the same source). Candidates scoring below threshold are held.
- **Expiry:** Candidates past their source expiration (`EXPIRES`) lapse automatically.

### 2. The Away Shift (`core/away_shift/`)
When Alex closes DeskAI for 2 hours (measured via live stream presence in `core/events.py`), the away shift starts
background sessions on its own background executor:
- **Steps:** check open loops due for research, discover opportunities (`ResearchWorker`), backfill past message subjects, and run consolidation.
- **Cadence & Caps:** Sessions run at least 2 hours apart, making 0 searches when nothing is due, capped at 15 web searches/day (`proactive_daily_search_cap`).
- **Activity Stream:** Publishes `event: activity` live events (started, step progress, finished) and updates `GET /api/activity`.
- **Chat Awareness:** System prompt context informs chat of background progress. Chat tool `background_status` returns step logs and findings.

### 3. Routing & Sending (`core/proactive/`)

**Timing follows Alex, not the clock (2026-10-27).** Alex has no regular sleep pattern ("these quiet hours are not
important to me"). 35% of his messages were 22:00-08:00, and the 08:30 brief got 2 replies in 9. So:
- **Quiet hours are off** by default; any he sets are kept.
- **The brief** (`brief_time` "auto") is due 18 h after the last one (`BRIEF_EVERY`). While he's away it's written
  ahead (`STATE_BRIEF_STAGED`, up to 4 h old) and sent the moment he's around. A set time (HH:MM) keeps the old
  clock rule.
- **The outbox:** `deliver()` is the one gate for every sender. What isn't urgent, sent while he isn't around (app
  closed for 1 h and no message in 1 h; `events.PRESENCE`), waits in `proactive_outbox`. The cycle releases one at a
  time when he is, 45 min apart (`OUTBOX_SPACING`), so there's no burst of pushes when he comes back. A held message
  over 4 days old is dropped. Urgent: watch alerts, follow-up answers, the pool's own sends.
- **Opening the app** (`events.OWNER_ARRIVED`) runs the cycle at once, so the brief and held messages arrive with him.
- **After an outage** (a gap of 1 h or more in the heartbeat) one notice says when it was off and which scheduled
  runs were missed (`Proactive.offline_notice`).

Cycles run every 30 minutes in the daemon, and on arrival:
1. **Quiet Hours & Caps:** Blocked during quiet hours if Alex set any, or when daily cap is reached (default 10 messages, 4 questions).
2. **The 10-Minute Lull:** To prevent interrupting active conversations, "whenever" candidates are held until 10 minutes have elapsed since the last chat message either way. Time-sensitive "now" candidates bypass the lull.
3. **Freshness Check:** Just before sending, the candidate's subject is checked against everything Alex has said since it was created; if already discussed or stale, it is dropped with zero new AI cost.
4. **Silence Check-in:** If Alex hasn't sent a message for 6 hours (outside quiet hours), the agent sends the highest-scored candidate from the pool. If unanswered, it backs off to 12 hours, then 24 hours. Any message from Alex resets the backoff.
5. **Implicit Feedback:** A reply within 6 hours marks a candidate answered; no reply marks it ignored. Threshold automatically adjusts per source based on reply rates. Two ignored questions in a row halve questions_per_day until Alex replies.

### 4. Guardrails (Code & Prompts)
- No guilt, no false urgency, no "we miss you" or "don't leave" retention tactics.
- Never nudges the owner simply to get them back into the app.
- Never asks about sensitive matters (health, medical, salary, financial, family) unless raised by the owner that week.
- Web searches never contain personal details (email addresses, phone numbers).
- Named signals in notices are grounded against actual signals, dated facts, or recent messages.

## Delivery, Transparency & Feedback

- **No content in pushes:** Push notifications say only that a message is waiting (`type: agent_message`, `message_id`). The app fetches content with `GET /api/inbox?after=<id>`.
- **Transparency (`why`):** Every proactive message returns a `why` field explaining what sparked it (e.g. quote, signal, or research finding).
- **Reactions (`POST /api/inbox/{id}/feedback`):** The app supports 👍 (`thumbs_up`), 👎 (`thumbs_down`), and `less_like_this`. Negative reactions immediately add the topic to `proactive_exclusions`.
- **Inspection (`GET /api/proactive/candidates`):** Inspects pending, sent, dropped, and lapsed candidates with full status, score, and outcome counts.
- **Weekly Numbers:** The Sunday growth review reports proactive metrics (sent, answered, thumbs down, ideas not sent, and drop reasons).

## The Owner's Controls

The agent handles natural steering in chat ("keep an eye on Styropyro videos", "stop asking about golf", "no more look-ups", "send brief at 07:00") via `proactive_settings`:
- **General:** `enabled`, `brief_time`, `quiet_hours`, `daily_cap` (0-20), `questions_per_day` (0-10), `location`.
- **Per-kind Switches:** `open_loops`, `curiosity`, `research` (look-up sharing), `patterns`.
- **Curated Topics:** `add_interest` / `remove_interest`, `add_exclusion` / `remove_exclusion`.
- Stored in `system_state`: `proactive_enabled`, `proactive_brief_time`, `proactive_quiet_hours`, `proactive_daily_cap`, `proactive_questions_per_day`, `proactive_open_loops`, `proactive_curiosity`, `proactive_research`, `proactive_patterns`, `proactive_interests`, `proactive_exclusions`.

## Generating less, finding more (2026-10-27, the background audit)

- **Generator back-off:** an away-shift generator (curiosity, interests) whose last batch was all dropped waits 6 h,
  then 24 h, unless Alex wrote 5+ messages or a new fact was saved. The cycle's own curious question follows the same
  rule (`_question_gen_due`); it used to be written every 30-minute cycle. Generators get the subjects already covered
  as a "don't suggest" list.
- **Interests:** a finding is new by its link, not its topic (`interest` is in `SOURCES_THAT_MAY_REPEAT`). It runs at
  most every 12 h, and never on the owner's private matters or the research worker's topic.
- **YouTube channels:** followed through their free feeds (`core/feeds.py`, every 6 h, no search, no AI). Managed with
  `proactive_settings` (`follow_youtube` / `unfollow_youtube`); default `@styropyro`.
- **Open loops:** research backs off ×2 per empty run, up to weekly (`quiet_runs`, `CADENCE_MAX`).

## Don't Undo

- No content in pushes.
- Every proactive sender goes through `Proactive.deliver()` (the outbox gate); none sends around it.
- Never exceed daily cap or question cap.
- Never send an ordinary question while one is unanswered.
- Curious questions must be grounded in quotes Alex actually said.
- Open loops change memory only with verified quotes and second AI confirmation.
- Silence is a valid outcome of every cycle.
