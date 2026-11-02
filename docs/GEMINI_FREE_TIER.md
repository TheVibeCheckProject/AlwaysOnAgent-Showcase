# Gemini free tier: what your key gets

As of 2026-10-06 (first written 2026-10-05). Your Gemini key has no billing account, so nothing on it can be charged: when a limit is hit,
Google refuses the request instead of billing it.

## The short answer

Each full **Flash** model gets only **20 requests a day** for free. **Flash-Lite** gets **500**. **Pro** gets
none. One chat message that uses tools (search, read, answer) spends 3 to 8 requests, so a Flash model is used
up after a handful of messages, and chat then falls to the next model.

- Every model has its own allowance: the four Flash models together give about 80 requests a day.
- Calls that fail as "overloaded" appear to count against the 20 too (3.7 Flash hit its limit on 2026-10-05
  with no successful call).
- Limits are per Google project, so everything using this key (the agent, ImageGen, TiktokVideos) shares them.
- Allowances reset at midnight Pacific time: 2:00 AM your time.

## Models on the free tier

| Model | Free requests a day | Context (input tokens) | On your key (tested 2026-10-05) | Agent uses it for |
| --- | --- | --- | --- | --- |
| Gemini 3.5 Flash-Lite | 500 | 1,048,576 | Works; 20/22 tool choice, ~1 s | **Chat 1st (since 2026-10-06)**; memory, tasks, proactive messages 1st |
| Gemini 3.8 Flash | 20 | 1,048,576 | Works | Chat, 2nd choice |
| Gemini 3.6 Flash | 20 | 1,048,576 | Works | Chat, 3rd choice |
| Gemini 3.7 Flash | — | — | **Redirected to 3.8 Flash** by Google (deprecations page, 2026-10-09) | Not used (since 2026-10-10) |
| Gemini 3.5 Flash | — | — | **Redirected to 3.6 Flash** by Google (deprecations page, 2026-10-09) | Not used (since 2026-10-10) |
| Gemini 2.5 Flash-Lite | — | — | Still listed, but answers **HTTP 404** | Not used (TiktokVideos dropped it 2026-10-10) |
| Gemini 3.1 Flash-Lite | 500 | 1,048,576 | Works (was overloaded) | Chat 6th; background chores 3rd; TiktokVideos affirmations backup |
| Gemini 3 Flash (`gemini-3-flash-preview`) | 20 | 1,048,576 | Not tested yet (the 2026-10-06 test used a wrong name) | Not used |
| Gemma 4 26B (`gemma-4-26b-a4b-it`) | **14,400** (30 a minute, 16K tokens a minute) | 262,144 | Works through Google's own API only (with tools since 2026-10-06); 2-5 s; on Oct 6 afternoon Google answered 11 of 12 calls with HTTP 500 | Background chores 2nd; chat 7th (last Google model) |
| Gemma 4 31B (`gemma-4-31b-it`) | **14,400** (30 a minute, 16K tokens a minute) | 262,144 | Works through Google's own API only; accurate but 30-60 s | Not used |
| Gemini 3.1 Pro / Pro latest | 0 (no free tier) | 1,048,576 | Refused: "limit: 0" | Not used |
| Gemini 2.5 Flash / Pro / Flash-Lite | — | — | Refused: "no longer available to new users" | Not used |

"Flash latest" points to 3.8 Flash and "Flash-Lite latest" to 3.5 Flash-Lite. There is no 3.7 Flash-Lite.

Google no longer prints the free-tier numbers in its docs; the live figures for your project are on the
[AI Studio rate limit page](https://aistudio.google.com/rate-limit) (sign in with the Google account that owns
the key). The 20 a day for Flash is confirmed by Google's own error on your key
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit 20); the 500 for Flash-Lite is from a September 2026
reading of AI Studio ([scriptbyai](https://www.scriptbyai.com/gemini-api-free-tier-limits/)).

## Per-minute limits and real use (AI Studio, 2026-10-06)

Peaks over the last 28 days, against each limit:

| Model | Requests a minute | Tokens a minute | Requests a day |
| --- | --- | --- | --- |
| 3.5 Flash-Lite | **18** / 15 (over) | 135K / 250K | 375 / 500 |
| 3.5, 3.6, 3.8, 3.7 Flash | 2-4 / 5 | under 20K / 250K | **23-41** / 20 (over) |
| 3.1 Flash-Lite | 1 / 15 | 2K / 250K | 4 / 500 |
| Gemma 4 26B and 31B | 1 / 30 | under 100 / 16K | 2 / 14,400 |

- Daily counts above 20 on Flash mean refused and failed calls count too.
- Flash-Lite going over 15 a minute happens when the background chores (memory, summaries, proactive messages)
  fire at the same time as chat: some calls are refused.
- Gemma's catch is its 16K tokens a minute: about two full chat requests a minute, but plenty for the chores.

## Benchmark: which free models can do the agent's work (2026-10-06)

`scripts/eval_models.py` on the Mini: 31 memory-writing cases and 16 second-opinion cases from the owner's own
chats, and 22 chat messages where the right tool is known. Through Google's own API (the agent's OpenAI-style
connection gives Gemma a server error 500), paced under each model's limits.

| | 3.5 Flash-Lite | 3.1 Flash-Lite | Gemma 4 26B | Gemma 4 31B |
| --- | --- | --- | --- | --- |
| Memory writing (31) | not run | 26 | **29** | 27 |
| Second opinions (16) | not run | 12 | **14** | 10 |
| Tool choice (22) | **20** (0 errors) | 10 (most misses: Google's "unavailable") | 19 | 15 (17 errors: 25 calls took over 40 s) |
| Typical answer | **0.8 s** | slow, often hung | 2-5 s (median 3.1 s) | median 39 s |

3.5 Flash-Lite's tool run (Oct 6 evening) missed "what's the weather this weekend" (searched memory) and "fix the typo on the website's about page" (asked which page instead of starting the job).

Gemma thinks out loud before answering: it needs about 4,000 tokens of room (with 400 it was cut off), and its
thinking parts are dropped. **Not benchmarking the Flash models** (Alex's call, 2026-10-10): Oct 3-10 3.8 Flash
answered 9 times and failed 25 (12 overloaded, 10 out of its daily allowance, 3 timeouts) while 3.5 Flash-Lite
answered 80 chats, so better answers wouldn't make it the main model, and a test run alone (~22 calls) is more
than its 20 a day. The Flash models stay as backups behind 3.5 Flash-Lite.

## How the agent's chat picks a model now

1. **3.5 Flash-Lite** (first since 2026-10-06, the owner's choice from the numbers: Oct 3-6 it answered 53 chat
   turns to the Flash models' 11, while the Flash models failed 57 times and on Oct 6 made replies wait ~3.5
   minutes in total before Flash-Lite answered anyway; its tool choice is 20/22 at ~1 s)
2. 3.8 Flash, 3. 3.6 Flash (3.7 and 3.5 Flash were dropped 2026-10-10: Google now routes them to these two)
4. 3.1 Flash-Lite (its own 500 a day; Google retires it on 2027-05-07)
5. Gemma 4 26B (14,400 a day, but 16K tokens a minute is only ~3 chat calls; a failure comes back in ~1 s)
6. Then Groq and Cloudflare (gpt-oss-120b) and the rest. Groq only gets requests under its 8K-token cap; a
   full chat request is skipped there before sending (it answered 413 every time).

After two failed models in one reply, the rest of that reply skips the other Flash models (2026-10-06).

**The background chores** (memory, summaries, the agent's own messages): 3.5 Flash-Lite, then Gemma 4 26B, then
3.1 Flash-Lite, then Groq. They no longer fall back to 3.8 Flash, so they don't spend chat's 20 a day. Every
Gemini call waits (at most 12 s) for room under its model's per-minute limit.

Changes on 2026-10-05 that save the Flash allowance for your chat:

- An overloaded Gemini model is no longer retried. The agent moves straight to the next Flash, which is faster
  and doesn't spend a second request.
- The agent's own messages (morning brief, notices, questions) now use Flash-Lite first, like its memory work.
- ImageGen and TiktokVideos use Flash-Lite first and touch 3.8 Flash only as a backup.

## If you want more Flash-level chat

- **Check your live numbers** on the [AI Studio rate limit page](https://aistudio.google.com/rate-limit); Google
  changes free quotas without notice.
- **Groq's gpt-oss-120b** (free, already in the list after Flash-Lite) could be moved ahead of Flash-Lite if it
  answers better. That is worth a test on real chats before switching.
- **Gemma 4 26B** is free with tools, a large context and 14,400 requests a day; it's fast enough for the
  background chores (see the benchmark), and moving them there would leave Flash and Flash-Lite for chat.
- **Not used: NVIDIA's free API** (`integrate.api.nvidia.com/v1`, about 40 requests a minute and 1,000-5,000
  credits). Its trial terms forbid personal data (2.6a, 4.3), let NVIDIA use prompts to improve its models
  (3.3 iv), and are for trial use, not production (1.2, 1.4); the agent's chat carries personal memories and email.
  [NVIDIA API Trial Terms](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf).
- Flash limits only go up with a billing account on the key, which this setup avoids on purpose.

## Sources

- Google, [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) (updated 2026-09-02): limits are per
  project; daily limits reset at midnight Pacific; the numbers are shown in AI Studio.
- Google, [Pricing](https://ai.google.dev/gemini-api/docs/pricing) (updated 2026-10-01): which models are free
  of charge (3.5-3.8 Flash, 3.5 and 3.1 Flash-Lite, Gemma 4) and which aren't (3.1 Pro Preview).
- [scriptbyai.com, Gemini API free tier limits](https://www.scriptbyai.com/gemini-api-free-tier-limits/)
  (September 2026, from AI Studio): 20 a day per Flash model, 500 a day per Flash-Lite model.
- Tests on your key from the Mini, 2026-10-05: which models answer, Google's 429 details, Gemma tool calls.
- AI Studio's rate-limit page for the project (screenshots, 2026-10-06), and the benchmark above.
