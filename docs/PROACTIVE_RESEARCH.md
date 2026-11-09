# Proactiveness: research, and the plan to get it right

Written 2026-10-13 by the laptop Claude session, at Alex's request: "research what other top companies rely on to
achieve proactiveness within their systems ... make a research file with the results and what our comprehensive
plan and code changes would be and why before we start changing anything."

Nothing in this plan is built yet, except **Phase 0** (section 6), which was built earlier the same day and is
uncommitted and undeployed. This document is the basis for deciding what to build; section 8 lists the decisions
that are Alex's.

How sure each finding is: most product details come from launch posts, help pages and press coverage, not from the
companies' internal designs, and some of it is second-hand. Where a source is second-hand or unverified, it says so.

---

## 1. The short version

The products that do this well share six habits:

1. **They work in the background and deliver on a schedule.** ChatGPT Pulse researches overnight and shows a
   handful of cards in the morning. Letta's "sleep-time" agents tidy memory while the user is away. The thinking
   happens off-screen, and only the result reaches the person.
2. **They keep a pool of possible things to say and voice only the best.** Research on "Inner Thoughts" (CHI 2025)
   gives an agent a running stream of thoughts, rates each on relevance, information gap, expected impact,
   urgency and originality, and speaks only above a threshold. People preferred it in 82% of conversations.
3. **They weigh the value of speaking against the cost of interrupting.** This is Eric Horvitz's (Microsoft
   Research) mixed-initiative rule: act only when the expected benefit beats doing nothing, and defer when the
   moment is bad. Notification studies agree: what makes people receptive is content that is interesting,
   relevant and actionable. Filtering out the 46% of notifications that weren't useful made the rest less
   disruptive.
4. **They learn from feedback.** Pulse has thumbs up or down on every card, plus a "curate" box ("show me more
   of X tomorrow"). ChatGPT memory ranks what stays top of mind by recency, frequency and interaction.
5. **They are finite, and they don't chase engagement.** Pulse shows 5 to 10 cards a day, then stops. Meta's test
   allows a bot one follow-up within 14 days of the user's last message, and nothing more if there's no reply.
   Research on AI companions found manipulative "don't go" tactics in 37 to 43% of goodbyes. Those tactics raise
   engagement, but they cost trust and make users more likely to leave.
6. **They show why, and leave the person in control.** Pulse cards show their sources. Copilot has a memory
   dashboard and per-feature opt-outs. Gmail's nudges can be switched off. In a CHI 2025 study, showing what the
   assistant was doing and why reduced how disruptive proactive help felt.

What our agent does today: messages are generated at send time, about one AI call per opportunity, with no pool,
no scoring, no feedback, and no overnight work. Questions come from a single "get to know them" prompt. The
results are the 18 questions sent since Oct 4, nearly all built on the same frame: "When you are …, do you usually
…?" Alex answered 14 of them, so the interest is there. The questions are dull.

**Recommendation:** build in two server-side steps, then one app step.

- **Away shift + candidate pool** (server only): research, open loops and curiosity run on a background worker
  whenever Alex is away from the app, so chat stays responsive. Picking from the pool happens whenever a message
  is allowed, and a long silence prompts a check-in.
- **Feedback and curation** (server, then DeskAI): replies and ignored messages adjust what gets sent, and chat
  commands let Alex steer it. A later app build adds 👍 / 👎 / "less like this", a small "why" line under each
  message, and a pulsing icon while background work is running.

Details are in section 7.

### Alex's direction (2026-10-13, after reading the first version)

> "Im actually okay with this happening more often than this. Like if i havent said anything in like 6 hours have
> the agent ask about it. If the app hasnt been opened in 2 hours, im okay with the agent doing research and
> background things then. I would use a worker agent for this, so i can still comunicate with the AI, but it needs
> to know that research is happening in the background. Like maybe just a flashing icon somewhere that shows its
> happening."

What changed in the plan as a result:

- **The night shift is now an "away shift"** (1a). It starts once the app has been closed for 2 hours, at any
  hour, not only at night.
- **A silence check-in** (1k). After 6 hours without a message from Alex (outside quiet hours), the agent reaches
  out with the best thing in its pool.
- **The away shift runs on its own background worker** (1l). Chat is never blocked, and the chat AI is told what
  the worker is doing.
- **A pulsing icon in DeskAI** (1m and Phase 2) shows while background work is running.
- **Higher volume:** proposed defaults of 10 messages and 4 questions a day, adjustable in chat.

Alex's earlier questions 2 and 5 (section 8) are answered by this.

---

## 2. Where we are today (measured on the Mini, 2026-10-13)

`proactive_messages`, Oct 4-13: 54 messages: notice 22, question 18, brief 10, memory 2, suggestion 1, review 1.

Replied to within 6 hours (a rough measure of engagement):

| Kind | Sent | Replied |
|---|---|---|
| question | 18 | 14 |
| notice | 22 | 18 |
| brief | 10 | 6 |

The last questions show the problem:

- "When you are working on your AI assistant code, do you have a preferred text editor or IDE…?"
- "When you are checking your email for your bills, do you usually like me to help track…?"
- "When you are watching videos from channels like Styropyro, do you have a favorite type…?"
- "When you are planning your morning routine, do you usually like to have your coffee…?"
- "When you are working on your golf game, do you prefer practicing at the driving range…?" (no reply)
- "Since you are teaching yourself AI, are there any specific programming languages…?" (no reply)

They have the same shape, they're mostly about how the agent can serve him, and they never follow up on anything
he actually said.

What the system has (`core/proactive/`, `docs/PROACTIVE.md`):

- a cycle every 30 minutes;
- quiet hours, a daily cap (6) and a question cap (2 a day, never while one is unanswered);
- the morning brief;
- cheap signals (important email, renewals, schedules), plus one AI call that decides whether to say anything;
- the question step;
- the weekly memory check (Saturdays) and the Sunday growth review;
- since 2026-10-13, the **AI research worker** (`core/research_worker.py`, self-update task-189). Every 3 days it
  searches for free AI credits, previews, free tiers and hackathons, and saves a digest as a canvas draft with a
  notice. It is started from the daemon's loop on its own thread (`ResearchWorker`), and also on demand with
  `POST /api/research/run` and the `research_ai_opportunities` chat tool. It is a narrow, fixed-topic example of the
  background research this plan generalizes, and it joins the away shift in step 1.10.

What it lacks, compared with section 1:

- no overnight or background work;
- no pool of candidates, and no scoring;
- no learning from replies or silence;
- no way for Alex to say "more of this" or "less of that", other than memory picking up a preference;
- no "why" on a message;
- repeat protection that only compares exact question text.

---

## 3. What the top companies do

### OpenAI: ChatGPT Pulse (Sept 2025, preview) and memory

- **How it works:**
  - Overnight, Pulse reads chat history, saved memory, feedback on earlier cards and (optionally) Gmail and
    Calendar.
  - It does "a round of asynchronous research" and shows the results the next morning as 5 to 10 cards.
  - The feed is deliberately finite: it ends instead of scrolling. Press quotes the product lead, Adam Fry, saying
    the daily limit is there to avoid social-media engagement patterns.
  - Cards expire after a day unless saved.
- **Feedback:**
  - Thumbs up or down on each card.
  - A "curate" request ("tomorrow, look into …"). Requests made before 10 pm are aimed at the next morning.
  - The help page says Pulse learns from feedback, card interactions and memory.
- **Safety:** every topic passes "layered safety checks" before it's shown, and a card can be reported.
- **Memory (Oct 2025):** ChatGPT ranks saved memories itself by recency, frequency and interaction, and keeps the
  important ones "top of mind". Users can see the ranking, change priorities and roll back. Complaints show the
  risk: low-value memories saved automatically, and old ones quietly pushed out.
- **Lesson for us:** do the work at night, deliver a few things, stop, ask for feedback, and make the feedback
  change tomorrow.

### Google: Now, Assistant, Gemini

- **Google Now (2012-2016):** proactive cards predicted from search and location. It was folded into "Feed", then
  Discover, which is mostly news. Google never published why. The commonly drawn lesson is that generic,
  prediction-only cards drifted into a content feed, and the useful personal parts moved into the assistant you
  talk to.
- **Scheduled actions (2025) and Goal Scheduled Actions (beta, 2026):** a prompt runs on a schedule. The goal
  version reviews each step's output and adjusts what it does next, for example watching a price and alerting
  when it drops.
- **Magic Cue (Pixel 10), reportedly becoming "Proactive Assistance":** surfaces the right detail from Gmail or
  Calendar inside the app being used, processed on the device. Users choose which apps feed it.
- **Personal Intelligence (Jan 2026):** connects Gmail, Photos, YouTube and Search to reason across them, for
  example linking an email thread to a video the user watched. It is off by default. One report says it labeled a
  user a golfer after the user had said they didn't play golf.
- **Lessons for us:**
  - Connecting sources is where the value is.
  - Saying something about the person that isn't true is the main failure. Grounding every statement in what the
    owner actually said guards against it.

### Microsoft: Copilot

- **Memory (Apr 2025):** Copilot notes preferences to give "proactive suggestions and timely reminders", such as
  birthdays or help before a presentation. Users control it through a memory dashboard and can opt out.
- **Proactive Actions (preview, Oct 2025):** nudges based on recent activity, such as offering a meeting summary
  or travel tips after browsing.
- **Research behind it:** Eric Horvitz's mixed-initiative principles (Microsoft Research):
  - act only when the expected value beats doing nothing;
  - weigh the cost of interrupting against the cost of deferring;
  - ask when unsure, but count the cost of bothering the person.
  - His BusyBody system learned a personal model of when interruptions are costly.
- **Lesson for us:** treat "should I say this now?" as a value-versus-cost decision, not a yes or no from a
  prompt.

### Apple: Siri Suggestions and Apple Intelligence

- **Approach:** "proactive intelligence" runs on the device for privacy.
- **Notifications:** Apple Intelligence ranks priority notifications to the top and summarizes the rest. A
  "Reduce Interruptions" Focus lets through only what may need attention now.
- **Lesson for us:** batch what isn't urgent into one digest. Our morning brief is that digest.

### Amazon: Alexa Hunches and Latent Goals

- **Hunches:** notice when a device's state differs from the usual pattern ("the light is on at bedtime"). Alexa
  then notifies, asks, or (if the owner allowed it) acts. Owners choose per kind.
- **Latent Goals:** suggests a natural next step after a request.
- **Complaints:** reviewers objected to unwanted asides, and opted out of Hunches and reminders.
- **Lesson for us:** let the owner choose per kind of proactive behaviour; unwanted asides are what get it turned
  off.

### Meta: AI Studio follow-ups (tested 2025, reported from leaked guidelines)

- **Rules:**
  - Bots may message first only if the user started the conversation and sent at least 5 messages within 14 days.
  - One follow-up only; if the user doesn't answer, the bot stops.
  - Avoid emotional or controversial topics unless the user raised them.
- **Lesson for us:** a single follow-up, then silence. The cap needs to stay strict, and the motive needs to be
  the owner's benefit, not retention.

### Gmail: Nudges

- **What it does:** flags emails you haven't replied to and sent emails nobody answered, chosen by machine
  learning from content and habits. It can be switched off per type.
- **Known limit:** it misses threads, and users can't steer it.
- **Lesson for us:** following up on open items is a proven proactive pattern, and our open loops are this idea
  applied to memory.

---

## 4. What the research says

| Work | Finding | What it means for us |
|---|---|---|
| Horvitz, *Principles of Mixed-Initiative User Interfaces* (CHI 1999); BusyBody; attention-sensitive alerting | Act when expected utility exceeds doing nothing; the cost of interrupting varies and can be learned; deferring is a real option | Score every candidate; defer what isn't urgent to the brief |
| Liu et al., *Proactive Conversational Agents with Inner Thoughts* (CHI 2025) | A covert stream of thoughts, each rated 1-5 on 8 heuristics (relevance, information gap, expected impact, urgency, coherence, originality, balance, timing); speak above a threshold; the urge rises with silence; preferred in 82% of conversations | Build a thought pool with scores, not one question made on the spot |
| Lu et al., *Proactive Agent* / ProactiveBench (ICLR 2025) | Proactive suggestions were labelled accepted or rejected by people; a reward model learned to judge them (F1 66%); false alarms and timing remain the hard part | Log every candidate with its outcome; that log is how ours gets tuned |
| Chen et al., *Need Help?* and Pu et al., *Assistance or Disruption?* (CHI 2025) | Proactive help made programmers faster but disrupted them; showing what the assistant was doing, and why, reduced the disruption; timing at natural pauses mattered most (the last part is second-hand) | Add a "why" line; send at good moments |
| Mehrotra et al. (CHI 2016) and follow-ups; Fischer et al. | Disruption depends on content, sender and what the person is doing; filtering out the 46% that weren't useful made the rest less disruptive; receptivity follows content that is interesting, relevant and actionable, more than timing | Fewer, better messages; let low-value candidates die |
| De Freitas et al., *Emotional Manipulation by AI Companions* (2025) | Manipulative tactics in 37-43% of companion apps' goodbyes; up to 14x engagement, but lower trust and more churn intent; the well-being app had none | Write a hard rule: no guilt, no FOMO, no "don't go"; measure usefulness, not engagement |
| Kaur et al., *Knowing Isn't Understanding* (2026) | Proactivity matters most for what the user doesn't know they don't know, and it needs behavioural limits on when, how and how much | Curiosity and research should surface things he wouldn't have asked about, within strict limits |
| CURIO (2025): curiosity reward for user modelling | Rewarding an agent for learning about the user made it better at uncovering and adapting to preferences | A good curious question is one whose answer teaches the agent something new; score it on that |
| Letta sleep-time agents (2025) | A second agent consolidates, de-duplicates and prunes memory while the main one is idle; reported gains in accuracy and cost (vendor figures) | An away shift (a background worker while the app is closed) for memory upkeep, open loops and research |
| Zep / Graphiti (2025) | A temporal knowledge graph: contradicted facts are marked invalid (`invalid_at`), not deleted | We already do this (`superseded`, `invalid_from`); open loops extend it to facts that are waiting on something |
| Park et al., *Generative Agents* (2023) | Retrieval by recency, importance and relevance; periodic "reflection" writes higher-level insights | Patterns the agent notices become questions to ask, never facts saved without his confirmation (memory never stores guesses) |
| Getting Things Done, "open loops" | Unfinished commitments keep pulling at attention until they're captured and reviewed | Our open loops: the agent tracks what he's waiting on so he doesn't have to |

---

## 5. Principles, and how we measure up

| # | Principle | Today | Gap |
|---|---|---|---|
| P1 | Think in the background; deliver on a schedule (Pulse, Letta) | Everything is made at send time | No background shift |
| P2 | Many candidates, voice only the best (Inner Thoughts) | One AI call per opportunity | No pool, no scores |
| P3 | Value against interruption cost; defer when not urgent (Horvitz, Apple) | Caps and quiet hours; urgency isn't considered | No urgency, no digest routing |
| P4 | Ground everything in what's actually known (Gemini's golfer mistake) | Phase 0 adds quote checks for open loops and curiosity | Notices and suggestions aren't grounded |
| P5 | Learn from feedback, explicit and implicit (Pulse, ChatGPT memory) | Only "less of this" saved as a memory | No reactions, no reply tracking, no back-off |
| P6 | Let the owner steer: curate, per-kind switches (Pulse, Alexa, Gmail) | proactive_settings: on/off, times, caps | No "look into X for me", no per-kind switches |
| P7 | Show why (Pulse sources, CHI 2025) | Open-loop notices name their source (Phase 0) | Other kinds don't; the app shows no "why" |
| P8 | Finite and not engagement-driven (Pulse, Meta, De Freitas) | Caps; no question while one is unanswered | Not written down as a rule; no measure of usefulness |
| P9 | Follow up on open items (Gmail Nudges, GTD) | Phase 0: open loops | Research runs during the day; should run at night |
| P10 | Never repeat yourself (Inner Thoughts "originality") | Exact-text check; Phase 0 checks topic and name in the last few | Styropyro was asked Oct 11 and again in the Phase 0 samples |
| P11 | Curiosity that teaches the agent something (CURIO) and surfaces unknown unknowns (Kaur) | Phase 0 curiosity: grounded and specific | Not scored on what it would teach; no pattern-based wondering |
| P12 | Measure it (ProactiveBench) | Nothing logged except what was sent | No candidate log, no weekly numbers |

---

## 6. Phase 0: built today, not yet committed or deployed

1. **Memory screen fix:** `GET /api/memory` sends only current facts in `facts`; replaced and faded ones go in
   `past`. Keep this exactly as it is.
2. **Open loops** (`core/open_loops/`): facts waiting on something are tagged, checked against newer facts,
   chat, mail and public web research, and replaced with a quote check and a second opinion. When the research
   shows news without the outcome, Alex is told once and asked. On the Mini, starting from memory where the
   decision was unknown, it found the public permit page and recorded "permit approved Oct 5". Keep it, and move the research
   to the away shift (Phase 1).
3. **Curiosity v1** (`core/proactive/`): one specific, grounded question, sometimes with something looked up.
   - Keep the grounding check, and replace the single-shot generation with the pool in Phase 1.
   - Its sample run asked about Styropyro again after Oct 11, because the name check looked only at the last 3
     questions. Fixed before deploying, by checking names against every question ever asked (checklist step
     0.1). Phase 1's check by meaning (1f) then replaces it.
4. **Summaries note:** conversation summaries are marked as dated history. Keep.

Deploy Phase 0 now or together with Phase 1: a decision for Alex (section 8). Item 1 is safe and useful on its
own.

---

## 7. The plan

### Phase 1: away shift and candidate pool (server only; no DeskAI build)

**1a. Away shift** (`core/away_shift/`, new; Alex: "If the app hasnt been opened in 2 hours, im okay with the
agent doing research and background things then").

When it starts:
- the app has been closed for 2 hours (1j says how the agent knows), at any time of day or night;
- the last session started at least 2 hours ago;
- the day's budget isn't used up.

At night the app is normally closed, so a session also runs every 2 hours or so while Alex sleeps.

While it runs:
- it works on its own worker (1l);
- if Alex opens the app mid-session, the session finishes its current step and the remaining steps;
- the icon (1m) shows that it's still working.

It sends nothing. It only prepares candidates, in this order, within each session's budget:

1. **Memory upkeep:** the existing `consolidate()`, moved here from idle time.
2. **Open loops:** tag, then check with research. The research moves out of the daytime check; the daytime keeps
   only quick evidence (chat, newer facts, mail) so an answered question is still picked up within the hour.
3. **Interests:** a short list of things Alex cares about that change over time, such as a channel's new
   uploads, a course's events, or news about his own project.
   - The list is taken from preference and project facts plus anything he asks it to watch (1d).
   - Up to 3 web searches; whatever is new and specific becomes a candidate.
4. **Patterns** (the "reflection" from *Generative Agents*):
   - One AI call over the week's activity, for example the hours he writes, or projects mentioned often or
     dropped.
   - It produces hypotheses to *ask about*, never facts to store. Example: "You've been up past 2 am most nights
     working on the assistant. Is that when you focus best?"
   - **How a pattern question may be put** (decided 2026-10-13, after an outside review suggested making them
     all about usefulness). They come in two forms, and never as a comment on his behaviour:
     - **Genuinely curious and never judgmental:** "Is late night when you focus best?", never "you've been up
       too late".
     - **An offer, when there's a real one:** "Want me to hold the morning messages until you're up?"
   - It's not all offers. Alex's complaint was that the questions are mundane, and the old ones were already
     mostly offers of service ("do you usually like me to help track…"). The scoring prompt rejects judging
     wording.
5. **Curiosity pool:** one AI call proposes about 6 candidate thoughts from facts, recent messages, patterns and
   interests, each with its "about" quote, so it's checked against what he said.

Not every step runs in every session. Memory upkeep and open loops run each session. Interests, patterns and the
curiosity pool run when the pool has fewer than about 6 unsent candidates, or once a day at most for patterns.

*Why:* this is what Pulse and Letta do: the research and thinking happen while he's away, and the moments he's
around only pick. It's also cheaper: one batched call instead of an AI call every time the cycle asks a question.

**1b. Candidate pool** (`proactive_candidates` table; `core/proactive/`). Every possible message becomes a row
with these fields:

- `source`: signal, open_loop, curiosity, interest or pattern;
- `kind`;
- `text`;
- `why`: one line, such as "you mentioned the trail cam on Oct 5" or "checked the public docket while you were away";
- `about`;
- `urgency`: now, today or whenever;
- `scores`;
- `expires_at`;
- `status`;
- `message_id`.

Today's email, renewal and schedule signals become candidates too (urgency "now" for time-sensitive ones).

**1c. Scoring** (the Inner Thoughts heuristics adapted, with Horvitz's value-against-cost rule).

This was revised after the outside review: free background models inflate absolute scores, giving everything a 4.
So the parts that can be measured are measured by the code, and the AI only ranks.

Measured by the code, never asked of the AI:
- **novelty:** embedding distance from every question ever asked and every proactive message of the last 30
  days (1f);
- **recently discussed:** embedding distance from Alex's own messages since the candidate was made, and in the
  last 24 hours;
- **variety:** a penalty when the last message sent came from the same source (two curiosity questions in a row,
  two interest findings in a row);
- **grounding:** pass or fail. The "about" quote or the signal must be found (as now), or the candidate never
  enters the pool.

Ranked by the AI: one call puts the pool's candidates **in order**, best first, by:
- relevance to Alex;
- expected value (useful, actionable, or something he'd enjoy);
- urgency.

A forced ranking can't inflate the way 1-to-5 scores do.

Combined: rank position, novelty, the recently-discussed penalty and variety make the score. There are hard
floors: below a minimum novelty, a candidate is dropped whatever its rank. A candidate is sent only above a
threshold that:

- falls slowly as time passes since the last proactive message, as in Inner Thoughts;
- rises after messages he ignored (1e).

Routing (Apple's digest idea; Horvitz's deferral):

- "now": sent in the next cycle, within the cap;
- "today": goes into the morning brief, or the next cycle if the brief has already gone;
- "whenever": waits for a good moment, which by default is within an hour of Alex being active in chat. Otherwise
  it waits until its expiry (curiosity 7 days, news 2 days) and then quietly lapses.

**1n. Never cut into a conversation; re-check before sending** (added after the outside review: "Alex opens the
app and immediately asks a pressing, unrelated question … if the dispatcher fires its queued candidate during or
right after that conversation, it feels jarring").

- **A lull first.** Nothing from the pool goes out while a conversation is going: not within 10 minutes of the
  last message either way, and not while a reply is being written. The only exceptions are "now" items that are
  truly time-sensitive (a reminder due, an email that needs an answer today).
- **When he comes back,** "whenever" curiosity candidates are held until that lull.
- **A freshness check just before sending:**
  - the candidate is compared, by embedding, against everything he has said since it was made;
  - if he has already talked about that subject, it's dropped;
  - if it builds on something now out of date (an open loop that closed, a fact that was replaced), it's dropped.
- **No new AI call** at send time: the check is embeddings and table lookups only.

*Why:* a well-chosen message at the wrong moment still feels tone-deaf. The CHI 2025 programming studies found
timing was the biggest difference between help and disruption.

**1o. A refresh schedule for searches** (added after the outside review: 3 searches × 5 to 8 sessions would
overshoot a 15-a-day cap).

- **Each open loop and interest records `researched_at`.** A web search runs for it only when one of these holds:
  - it hasn't been researched for 48 hours (24 hours from 3 days before its check_after date, when news is more
    likely);
  - a cheap signal flags it: a new email matching its mail words, Alex mentioning it, or a newer fact about it.
- **Cheap checks always run first,** every session, and cost nothing: newer facts, chat, events and the mail
  search.
- **A session with nothing due makes no web searches at all.** The 15-a-day cap stays as a ceiling, not the
  expected use. With today's handful of loops and interests, real use should be a few searches a day.
- **The outside review proposed "search only when a change is flagged".** That goes too far for public web news:
  there's no way to know a docket or a channel changed without looking. A schedule is the workable version.

*Why:* fewer and better messages is what the notification studies show reduces disruption. The pool also makes
"silence is a valid outcome" true by design, not by prompt.

**1d. Curate by chat** (the `proactive_settings` tool, extended):

- "keep an eye on new Styropyro videos" adds an interest;
- "stop asking about golf" adds an exclusion;
- "no more look-ups" turns off look-up sharing.

Per-kind switches: open loops, curiosity, research sharing and pattern questions, each on or off.

*Why:* Pulse's "curate", Alexa's per-kind controls and Gmail's switches. The owner steers.

**1e. Implicit feedback and back-off:**

- A reply within 6 hours marks a message answered; no reply marks it ignored.
- Per source and topic, the reply rate adjusts the threshold.
- Two curious questions in a row ignored halves questions_per_day until he replies.
- Following Meta's rule, an unanswered question is never followed up; it lapses.

*Why:* learning from behaviour without asking him to do anything.

**1f. Repeat check by meaning:** every past question and its subject are compared by embedding against new
candidates, using the memory's search model. Anything too close to something asked before, on any date, is
dropped. This replaces the last-3 and last-8 checks.

*Why:* it fixes the Styropyro repeat and anything else of that kind.

**1g. Ground notices and suggestions** the same way open loops and curiosity are: each must name the signal or
fact it came from, and that source must exist.

**1h. Guardrails, written into code and docs (P8):**

- no guilt, no urgency that isn't real, no "don't leave" messages;
- no messages to get him back into the app;
- nothing about health, money or family unless he raised it that week;
- searches never contain personal details.

**1i. Measure it:**

- every candidate is kept with its outcome (sent, deferred, lapsed, dropped and why; answered, ignored);
- the Sunday review gets a line like "proactive: 9 sent, 6 answered, 1 👎; 31 ideas not sent (low score 22,
  repeats 5, excluded 4)".

*Why:* ProactiveBench's lesson. You can't tune what you don't measure.

**1j. Knowing whether the app is open** (`interfaces/hud/events_api.py`, `core/events.py`).

- DeskAI keeps the live event stream (`GET /api/events`) connected only while it's on screen (`TaskEvents.kt`:
  "while the app is in the foreground"). So the app counts as **open** while a stream is connected.
- The agent records `app_last_seen` when a stream connects and when it closes, and keeps it in `system_state` so a
  restart doesn't reset the clock.
- "Closed for 2 hours" means no stream for 2 hours.
- **Requests don't count** (decided at step 1.1). DeskAI's push service fetches each new message in the
  background (`DeskAIMessagingService.kt`), so a request doesn't mean Alex opened the app. The stream does:
  `MainActivity` opens it in `onStart` and closes it in `onStop`.
- No app change is needed for this part.

*Why:* it is a reliable, existing signal. The alternative of the phone reporting "opened" and "closed" would need
an app build and can miss events.

**1k. The silence check-in** (Alex: "if i havent said anything in like 6 hours have the agent ask about it").

When it happens: Alex has sent nothing for 6 hours, and it isn't quiet hours.

What it sends: one check-in, taken from the pool: the highest-scored question or finding. It's never "just
checking in" or "are you there?". It has to carry something real: a curious question, a follow-up on something
he's waiting on, or something the away shift found.

Ordinary questions still follow the rule against asking while one is unanswered. The check-in is the one exception
the silence allows, and it backs off:
- if the check-in gets no reply, the next waits 12 hours, then 24;
- any message from Alex resets this.

The research is clear that pestering someone who isn't answering is how products lose trust (Meta's single
follow-up; the companion-app study). This keeps the reach-out Alex asked for without turning into that.

*Why:* it is Alex's explicit request, kept within the guardrails of 1h.

**1l. A background worker that chat knows about.**

The worker:
- The away shift runs on its own thread (`AwayShift` in the daemon). It has its own executor, separate from both
  the task queue (Alex's jobs) and the memory worker (chat's extraction), so it never delays a reply or a queued
  task.
- Its AI calls use the background models, as memory upkeep already does, so chat keeps the best models' free
  requests.

What chat knows:
- While a session runs, the chat context gets one line, for example "Background work in progress (started 14:05):
  checking what you're waiting on (2 of 4), then looking into Styropyro's new uploads".
- Afterwards it gets "Found in the background today: …" (the candidates not yet sent).
- So "what are you working on?" or "did you find anything?" gets a true answer. The existing honesty checks
  (`unbacked_claim_note`) apply: chat can't claim research it didn't do.
- A new chat tool, `background_status`, returns the session's steps and what it found, for when Alex asks for
  details.

*Why:* Alex: "so i can still comunicate with the AI, but it needs to know that research is happening in the
background".

**1m. Activity events for the icon** (`core/events.py`, `GET /api/events`, `GET /api/activity`).

- The away shift publishes `event: activity` on the same live stream:
  - `{"state": "started", "label": "Researching in the background"}` when a session starts;
  - progress lines as each step begins;
  - `{"state": "finished", "found": 3}` when it's done.
- `GET /api/activity` returns the current state, so a freshly opened app knows whether to show the icon without
  waiting for an event.
- The server side is in Phase 1, so it can be tested. The icon itself is in Phase 2.

**Files for Phase 1:**
- `core/away_shift/` (new: triggers, session steps, budget, the worker);
- `core/proactive/` (pool, scoring, routing, silence check-in, back-off, guardrails; higher default caps);
- `core/open_loops/` (research moves to the away shift);
- `core/daemon/` (the away shift worker; it takes over idle-time `consolidate`);
- `core/events.py` and `interfaces/hud/events_api.py` (presence: `app_last_seen`; `activity` events);
- `interfaces/hud` (`GET /api/activity`; inbox returns `why`; `GET /api/proactive/candidates` for inspection);
- `core/memory/` (the background-work line in chat context);
- `core/assistant_tools/` (`background_status`; curate in `proactive_settings`);
- `core/growth_review.py` (the weekly line);
- docs: PROACTIVE.md, API.md, CHANGELOG.md, AI_HANDOFF.md;
- tests: presence and the 2-hour trigger, the session runs while chat answers, the silence check-in and its
  back-off, activity events, scoring and routing, repeat check by meaning, guardrails, curate.

**Cost per day**, all on free tiers, with sessions every 2 or more hours while Alex is away (realistically 5 to 8
a day):

- **AI calls:** about 3 to 6 per session, on the background models, which have the larger free limits.
- **Web searches:** capped at 15 a day. Tavily's free tier is 1,000 a month, and this would use up to about 450,
  next to the agent's other searches. Past the cap, or if Tavily runs out, it falls back to Serper, then
  DuckDuckGo.
- **Second opinions:** only when something would change memory.
- The search cap and the session gap are settings, so they can be lowered if the free limits get tight.

The cycles while he's around make fewer AI calls than now, because they pick instead of generating.

### Phase 2: in the app (a DeskAI build)

- **The background icon:** a small icon in the chat's top bar that pulses while the away shift is working. It is
  driven by the `activity` events and `GET /api/activity` (1m). Tapping it shows what the agent is doing ("Checking
  what you're waiting on: 2 of 4") and what it has found so far. Without this build, nothing breaks: chat already
  knows (1l) and can tell him.
- **Reactions** on proactive messages: 👍 / 👎 / "less like this". `POST /api/inbox/{id}/feedback`, stored and
  used in scoring (1c) and exclusions (1d). This is Pulse's thumbs.
- **A "why" line** under each proactive message, in small text: "Because you mentioned … on Oct 5" or "Checked
  while you were away: …". This is the transparency that reduced disruption in the CHI 2025 study.
- **Memory screen:** "Past versions" (the `past` list) and "Waiting on" (`open_loops`) sections.
- **Settings:** the per-kind switches from 1d.

### Phase 3: later, once the log has a few weeks of data

- Tune the score weights and threshold from the candidate log: which scores predicted answered against ignored.
- Learn good moments from when he actually replies, the way BusyBody learned interruption costs.

---

## 8. Decisions for Alex

Answered:
- **Background research:** yes, whenever the app has been closed for 2 hours, on a background worker that chat
  knows about.
- **Volume:** more often is fine, so the proposed defaults are 10 messages and 4 questions a day, plus the 6-hour
  silence check-in.

Still open:
1. **Phase 0:** deploy now (the Memory screen fix helps immediately), or wait and ship it with Phase 1?
2. **Pattern questions:** OK for it to wonder about patterns in your activity, like late nights or a project going
   quiet? It would only ask, never store a guess. Some people find this caring and some find it watchful.
3. **Look-ups in curious questions:** sometimes share something it found, with a link? Or keep questions plain?
4. **The proposed numbers:** 10 messages and 4 questions a day; check-in after 6 hours of silence, backing off to
   12 and 24 hours when unanswered; a session at most every 2 hours while away; up to 15 searches a day. Change
   any of them?
5. **The app build (Phase 2):** the pulsing icon, plus reactions and the "why" line. Do it straight after Phase 1?

---

## 9. Implementation checklist

Alex's rule: "create a check list and make sure they are done before the next one starts … zero code breaks with
this edit and update." Each step names every file it touches and a **gate**. The next step starts only after the
gate passes, and the checkbox is ticked here when it does.

### The gates (used by every step)

- **G-unit:** the step's own tests pass.
- **G-full:** the full AlwaysOnAgent suite passes on the laptop: `python -m pytest tests -q`, exit code 0. It
  includes the two guards that catch doc drift:
  - `test_api_contract.py`: every API route is in `docs/API.md`, and DeskAI's copy of `docs/API.md` matches;
  - `test_docs_truth.py`: relative links in docs resolve, and entry files point to the handoff.
- **G-import:** `python -c "import supervisor, core.daemon; from interfaces.hud import app"` runs without an error. This
  catches a broken import that a test might not reach.
- **G-compat:** whatever an older DeskAI build relies on keeps working:
  - JSON responses only gain fields; no field is renamed or removed;
  - new live-stream events use a new event name (`event: activity`). Older builds act only on `event: task`
    lines (`AlwaysOnAgentClient.kt` line 481) and ignore the rest;
  - new endpoints are new paths.
- **G-mini:** `scripts/deploy_mini.py` reports the full suite passing on the Mini, a fresh uptime, and no ERROR or
  Traceback lines in `logs/worker.log`. It never runs within an hour of the 08:00 or 20:00 TikTok posts.
- **G-app:** DeskAI unit tests pass and the release build succeeds:
  `gradlew testDebugUnitTest assembleRelease`, then the APK is checked (size and version) before shipping.

### Phase 0: ship what's built (server; no app build)

- [x] **0.1** Curiosity's name check against every question ever asked, not just the last 3.
  - Files: `core/proactive/` (`_same_subject_lately`), `tests/test_proactive.py`.
  - Gate: G-unit (a Styropyro question on Oct 11 blocks one now), G-full, G-import.
- [x] **0.2** Review the full Phase 0 diff, then commit it in AlwaysOnAgent.
  - Files:
    - `core/open_loops/` (new), `core/proactive/`, `core/daemon/`, `core/memory/`, `core/web_tools/`;
    - `interfaces/hud/memory_api.py`;
    - `tests/test_open_loops.py` (new), `tests/test_proactive.py`;
    - `docs/API.md`, `docs/CHANGELOG.md`, `docs/AI_HANDOFF.md`, `docs/PROACTIVE.md`,
      `docs/PROACTIVE_RESEARCH.md` (new).
  - Gate: G-full, G-import, G-compat (`/api/memory` only gains `past` and `open_loops`; `facts` keeps its shape).
- [x] **0.3** Commit DeskAI's copy of the API doc.
  - Files: `DeskAI/docs/API.md`.
  - Gate: `test_api_contract` passes with both repos side by side.
- [x] **0.4** Push both, then deploy to the Mini.
  - Gate: G-mini.
- [x] **0.5** Live checks on the Mini:
  - `/api/memory`: `facts` has no superseded fact (#35 is in `past`, #38 in `facts`);
  - the first proactive cycle logs open-loop tagging without warnings;
  - nothing is sent that shouldn't be: memory has nothing open, so no open-loop message is expected.
  - Gate: all three seen.

### Phase 1: away shift and candidate pool (server; no app build)

Build order: the things others depend on come first. Every step must pass G-unit, G-full and G-import before the
next.

- [x] **1.0 Logs reach `logs/worker.log`.** Found at step 0.5: the open-loop step ran (the database showed it),
  but its log lines, and the proactive cycle's and the weekly memory check's, went to loggers that aren't wired
  to `logs/worker.log`, so warnings were invisible.
  - Files: `core/open_loops/`, `core/proactive/`, `core/memory_review.py`: each logger becomes a child of the
    daemon's `AlwaysOnAgent` logger.
  - Gate: G-unit (a test that their records reach the daemon's handler), G-full, G-import.
- [x] **1.1 Presence** (1j).
  - Files:
    - `core/events.py`: `Presence` (open streams, last seen; `is_open()`, `away_seconds()`, `restore()`);
    - `interfaces/hud/events_api.py`: counts a stream open and closed; saves `app_last_seen`;
    - `tests/test_live_events.py`.
    - (`interfaces/hud/auth.py` is no longer touched: requests don't count, see 1j.)
  - Also G-compat: the task event stream is byte-for-byte unchanged.
- [x] **1.2 Activity events and `GET /api/activity`** (1m).
  - Files:
    - `core/events.py` (`publish_activity`);
    - `interfaces/hud/events_api.py` (formats by event type, so task events stay `event: task`; the new endpoint);
    - `docs/API.md` and DeskAI's copy (the endpoint and the event);
    - `tests/test_live_events.py`.
  - Also G-compat.
- [x] **1.3 The candidate pool table and store.**
  - Files: `core/candidates/` (new: the `proactive_candidates` table, add, expire, list, mark);
    `tests/test_candidates.py` (new).
- [x] **1.4 Novelty and recently-discussed, measured in code** (1c, 1f).
  - Files:
    - `core/candidates/`: `Similarity`, `novelty`, `discussed`, `distinctive_words`, `backfill_subjects`;
    - `core/proactive/`: `proactive_messages` gains `subject`;
    - `tests/test_candidates.py`.
  - **Calibrated on the Mini's model** (qwen3-embedding:0.6b), which changed the design:
    - **Whole questions don't separate.** The two Styropyro questions scored 0.41, while two unrelated
      "When you are …" questions scored 0.52.
    - **So subjects are compared instead.** Every candidate and every sent message carries a short subject.
    - **Subject against subject:** the same subject scored 0.64 to 0.77, different subjects 0.63 at most. A repeat
      is 0.64 or more.
    - **Subject against Alex's messages:** real discussion scored 0.66 to 0.73, but short replies reached 0.62.
      So "already discussed" counts only messages of 6 words or more at 0.65, plus a rule matching names
      (capitalized words). Plain words gave false matches.
  - **Past messages get subjects once,** in one AI call (`backfill_subjects`), run by the away shift (1.10).
- [x] **1.5 Ranking** (1c).
  - Files: `core/candidates/` (one AI ranking call; the score from rank, novelty, penalties and floors; the
    threshold); `tests/test_candidates.py`.
- [x] **1.6 Routing, the lull and the freshness check** (1b, 1n).
  - Files:
    - `core/proactive/`: `run_cycle` picks from the pool; now / today / whenever; the 10-minute lull; the
      freshness check before sending; "today" items go into the brief; `proactive_messages` gains `why` and
      `candidate_id`;
    - `tests/test_proactive.py`: the existing tests are rewritten to feed the pool. Their rules are kept and each
      checked again: the cap, quiet hours, no question while one is unanswered, never repeat.
- [x] **1.7 Silence check-in and its back-off** (1k).
  - Files: `core/proactive/`; `tests/test_proactive.py` (6 hours of silence, no reply means 12 then 24 hours,
    any message resets, never during quiet hours, never empty).
- [x] **1.8 Implicit feedback** (1e).
  - Files: `core/candidates/` (answered and ignored marks; per-source reply rates; threshold adjustment);
    `core/proactive/` (back-off on ignored questions); tests.
- [x] **1.9 Open loops: research on a schedule, off the daytime path** (1o).
  - Files: `core/open_loops/` (`researched_at`; the 48-hour and 24-hour cadence; flagged by cheap signals; the
    daytime check uses only cheap evidence); `tests/test_open_loops.py`.
- [x] **1.10 The away shift worker** (1a, 1l).
  - Files:
    - `core/away_shift/` (new: the 2-hour trigger, sessions at least 2 hours apart, the steps, the daily search
      cap in `system_state`, activity events, its own executor);
    - `core/daemon/` (starts it; idle-time `consolidate` moves into it; the `ResearchWorker` start moves into it);
    - `core/research_worker.py` (task-189): becomes one of the away shift's steps. It keeps its 3-day interval, its
      queries and its canvas digest, but its searches count against the day's search cap and it publishes activity
      events. `POST /api/research/run` and the `research_ai_opportunities` tool keep working on demand. Its digest
      notice becomes a pool candidate, not a direct message;
    - `tests/test_away_shift.py` (new): not while the app is open; starts 2 hours after it closes; the cap holds;
      a session with nothing due makes no searches; the chat path and the proactive cycle aren't blocked while a
      session runs;
    - `tests/test_research_worker.py`: still passes unchanged for the worker's own logic; the new tests cover it
      running as a step.
- [x] **1.11 Chat knows about background work** (1l).
  - Files:
    - `interfaces/hud/chat.py` (the background line in the system context);
    - `core/assistant_tools/` (`background_status`: handler, spec, and a `TOOL_GROUPS` entry, which
      `test_tool_routing.py` requires);
    - `core/memory/` (`unbacked_claim_note`: claims of background research are checked against the session
      record);
    - `docs/TOOLS.md`;
    - `tests/test_assistant_tools.py`, `tests/test_tool_routing.py`, `tests/test_honest_claims.py`.
- [x] **1.12 Curate and per-kind switches; new defaults (10 messages, 4 questions)** (1d).
  - Files:
    - `core/proactive/` (`DEFAULTS`, `change()` validation, interests, exclusions, switches);
    - `core/assistant_tools/` (`proactive_settings` spec and handler);
    - `docs/TOOLS.md`, `docs/PROACTIVE.md`;
    - `tests/test_proactive.py`, `tests/test_assistant_tools.py`.
- [x] **1.13 Grounded notices and suggestions; guardrails** (1g, 1h).
  - Files: `core/proactive/` (the decide step names its signal and the code checks it; the guardrail rules in
    prompts; the exclusion filter); `tests/test_proactive.py`.
- [x] **1.14 Inbox `why`, feedback endpoint, candidate inspection.**
  - Files:
    - `interfaces/hud/companion.py`: `GET /api/inbox` gains `why`; `POST /api/inbox/{id}/feedback` is ready for
      Phase 2;
    - `interfaces/hud/` (a new router or `companion.py`): `GET /api/proactive/candidates`;
    - `interfaces/hud/models.py` (the feedback request);
    - `docs/API.md` and DeskAI's copy;
    - tests.
  - Also G-compat.
- [x] **1.15 The weekly numbers.**
  - Files: `core/growth_review.py`; `tests/test_growth_review.py`, `tests/test_weekly_report.py`.
- [x] **1.16 Docs.**
  - Files: `docs/PROACTIVE.md` (the new design), `docs/API.md` and DeskAI's copy, `docs/TOOLS.md`,
    `docs/AI_HANDOFF.md`, `docs/CHANGELOG.md`, and this file's checkboxes.
  - Gate: G-full, including `test_docs_truth` and `test_api_contract`.
- [x] **1.17 Dry run, then ship.**
  - The dry run uses this code and a copy of the Mini's database, in a temporary folder on the Mini (outside the
    repo; as on 2026-10-13). It runs one away session and several cycles with sending replaced by printing.
    - Check: the candidates are specific and grounded; there are no repeats; the search count stays within the
      cap; the check-in is substantive.
  - Then commit, push and deploy.
  - Gate: G-mini, then watch the first real away session (the activity events, the log) and the first check-in.

### Phase 2: the app (a DeskAI build; it starts only after 1.17 is live)

- [x] **2.1 Models.**
  - Files: `app/src/main/java/com/example/data/model/AlwaysOnModels.kt` (`ActivityState`; `why` and feedback on
    inbox messages; `past` and `openLoops` in the memory overview, all optional with defaults).
- [x] **2.2 Client.**
  - Files: `data/remote/AlwaysOnAgentClient.kt`:
    - `streamTaskEvents` also hands `event: activity` to a new callback, with a default that does nothing, so
      existing callers are unchanged;
    - `getActivity()`, `postInboxFeedback()`;
    - the inbox and memory parsing read the new optional fields.
- [x] **2.3 Live state.**
  - Files: `data/remote/TaskEvents.kt` (forwards activity to a `StateFlow`); `ui/screens/ChatViewModel.kt`
    (activity state; the feedback action); `MainActivity.kt` (only if `TaskEvents` wiring needs it).
- [x] **2.4 The pulsing icon.**
  - Files: `ui/screens/ChatScreen.kt` (the top-bar icon, pulsing while active); `ui/dialogs/BackgroundWorkSheet.kt`
    (new: the current step and what's been found).
- [x] **2.5 The "why" line and reactions on proactive messages.**
  - Files: `ui/components/MessageBubble.kt`; `data/repository/ChatRepository.kt` (inbox sync keeps `why`, the kind
    and the message id).
- [x] **2.6 Memory screen sections.**
  - Files: `ui/dialogs/MemorySheet.kt` ("Waiting on" and "Past versions").
- [x] **2.7 Tests.**
  - Files: `app/src/test/java/com/example/` (new parsing tests for activity events, inbox `why` and the memory
    overview). `TaskFoldingTest.kt` and `RichTextParserTest.kt` must still pass.
  - Gate: G-app.
- [x] **2.8 Ship.**
  - Files: `app/build.gradle.kts` (versionCode and versionName), `DeskAI.apk`, `DeskAI-update.apk`,
    `web_dist/DeskAI.apk`, `web_dist/version.json` (only together with the APK), `ALIGNMENT_DIALOG.md`; push as
    example-user.
  - Gate: G-app; the live manifest shows the new version; an older build still works against the agent (G-compat
    was kept throughout Phase 1).

### Not touched by any phase

- TiktokVideos;
- the agent's task queue and engines (`core/task_engines/`, the daemon's task executor);
- schedules (`core/schedules.py`);
- watches (`core/watches/`), which are only read, as a cheap signal;
- backups;
- the self-update flow.

If a step turns out to need one of these, the step stops, and this list is updated before going on.

---

## 10. Sources

Products:
- OpenAI, *Introducing ChatGPT Pulse*: https://openai.com/index/introducing-chatgpt-pulse/ (blocked to our fetch;
  details from the help page and coverage below)
- OpenAI Help, *ChatGPT Pulse*: https://help.openai.com/en/articles/12293630-chatgpt-pulse
- Tom's Guide on Pulse: https://www.tomsguide.com/ai/chatgpt-pulse-is-here-now-ai-starts-the-chat-and-curates-your-feed
- Storyboard18 (finite feed, Adam Fry): https://www.storyboard18.com/digital/openai-launches-pulse-to-turn-chatgpt-from-reactive-to-proactive-81594.htm
- The Neuron on Pulse: https://www.theneuron.ai/explainer-articles/chatgpt-pulse-makes-ai-your-personal-research-assistant-while-you-sleep
- TechRadar, ChatGPT memory management (Oct 2025): https://www.techradar.com/ai-platforms-assistants/chatgpt/chatgpt-is-smarter-now-that-its-learned-to-forget-a-huge-memory-upgrade-is-coming
- Google, Gemini scheduled actions: https://blog.google/products-and-platforms/products/gemini/scheduled-actions-gemini-app/
- Goal Scheduled Actions (beta): https://piunikaweb.com/2026/02/28/google-gemini-goal-scheduled-actions-beta/
- Magic Cue to "Proactive Assistance": https://9to5google.com/2026/07/09/pixel-magic-cue-gemini/
- Gemini Personal Intelligence: https://blog.google/innovation-and-ai/products/gemini-app/personal-intelligence ;
  https://aibusiness.com/agentic-ai/gemini-scours-apps-personal-intelligence
- Google Now history: https://en.wikipedia.org/wiki/Google_Now
- Microsoft, *Your AI Companion* (Copilot, Apr 2025): https://blogs.microsoft.com/blog/2025/04/04/your-ai-companion/
- Copilot Fall Release 2025 (second-hand): https://webvine.com.au/copilot-fall-release-2025-human-centred-ai-that-feels-human/
- Apple Intelligence availability (priority notifications, summaries): https://www.businesswire.com/news/home/20241028308485/en/Apple-Intelligence-is-available-today-on-iPhone-iPad-and-Mac
- Alexa Hunches and Latent Goals: https://voicebot.ai/2020/11/11/amazon-alexa-latent-goals-feature-will-predict-customer-objectives-and-proactively-suggest-follow-on-actions-and-even-aid-in-skill-discovery/ ;
  https://www.t3.com/au/news/amazons-alexa-hunches-can-switch-your-heating-off-for-you-if-you-forget
- Meta's proactive bots (TechCrunch): https://techcrunch.com/2025/07/03/meta-has-found-another-way-to-keep-you-engaged-chatbots-that-message-you-first/
- Gmail Nudges: https://mailmeteor.com/blog/gmail-nudges

Research:
- Horvitz, *Principles of Mixed-Initiative User Interfaces* (CHI 1999), summarized in: https://www.cl.cam.ac.uk/teaching/1819/P230/IWML-Lecture-3.pdf
- Horvitz et al., BusyBody: https://erichorvitz.com/busybody_cscw.htm ; attention-sensitive alerting: https://erichorvitz.com/attend.htm
- Liu et al., *Proactive Conversational Agents with Inner Thoughts* (CHI 2025): https://arxiv.org/abs/2501.00383
- Lu et al., *Proactive Agent* (ICLR 2025): https://arxiv.org/abs/2410.12361
- Chen et al., *Need Help? Designing Proactive AI Assistants for Programming* (CHI 2025): https://arxiv.org/abs/2410.04596
- Pu et al., *Assistance or Disruption?* (CHI 2025): https://arxiv.org/abs/2502.18658
- Mehrotra et al., *My Phone and Me* (CHI 2016): https://discovery.ucl.ac.uk/id/eprint/1502364/
- Intelligent notification systems survey: https://arxiv.org/abs/1711.10171
- De Freitas et al., *Emotional Manipulation by AI Companions* (2025): https://arxiv.org/abs/2508.19258
- Kaur et al., *Knowing Isn't Understanding* (2026): https://arxiv.org/abs/2602.15259
- CURIO, curiosity reward for user modelling (2025): https://arxiv.org/abs/2504.03206
- Deng et al., proactive dialogue surveys: https://arxiv.org/abs/2305.02750 ; ACM TOIS 10.1145/3715097
- Letta, sleep-time compute: https://letta.com/blog/sleep-time-compute
- Zep, *A Temporal Knowledge Graph Architecture for Agent Memory*: https://arxiv.org/abs/2501.13956
- Park et al., *Generative Agents* (2023): https://arxiv.org/abs/2304.03442
- Open loops (GTD): https://super-productivity.com/blog/gtd-inbox-capture-system/
