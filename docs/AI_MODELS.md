# AI models: who answers, and staying on the free tiers

Every AI call the system makes is on a free plan. The rules: use the best free model for the job, spread load
across the free allowances, and never add anything that can run up a bill. The owner once disputed a ~$2,000
Gemini bill, so the Gemini key is **free tier only**, with no billing account in AI Studio.

## Providers

| Provider | Key(s) in `.env` | Models used | Notes |
|---|---|---|---|
| **Google Gemini** | `GEMINI_API_KEY` | Flash-Lite, Flash and Gemma models (3.5 Flash-Lite first; details and limits in [GEMINI_FREE_TIER.md](GEMINI_FREE_TIER.md)) | First choice everywhere. Free allowances are **per model, per day**. |
| **Groq** | `GROQ_API_KEY` | gpt-oss-120b, gpt-oss-20b, qwen3.8-27b | very fast; prompt caching |
| **Cloudflare Workers AI** | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | gpt-oss-120b / 20b; Llama 3.3 70B (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`) for the chat's tool router only | 10,000 neurons a day per account, shared by every model (~45 chat messages, or ~800 router calls). A second token on the same account adds nothing |
| **Mistral** (free Experiment plan, training off) | `MISTRAL_API_KEY` | ministral-14b (chat), codestral (code) | medium, small and large models aren't on the free plan |
| **Ollama** (local, on the Mini) | `OLLAMA_URL`, `MEMORY_EMBED_MODEL`, `VISION_MODEL`, `OLLAMA_HOST`, `OLLAMA_MODEL` | `qwen3-embedding:0.6b` (memory search), `gemma3:4b` (image notes); any installed model for the `ollama` engine | free and private; see "Local models" below |
| **Antigravity** (`agy`) | the owner's Google sign-in | set per AgentWork project | coding agent for AgentWork and the `antigravity` engine; uses the Google account's plan allowance |
| **ElevenLabs** | in TiktokVideos' `.env` | voices | video narration (free Edge voices without it) |

## Local models (on the Mini, no cloud, no cost)

| Model | Runs in | Used for | Why this one |
|---|---|---|---|
| Qwen3-Embedding 0.6B | Ollama, kept loaded (639 MB file, ~2.4 GB of RAM loaded; measured on the Mini 2026-10-07) | memory search | Benchmarked against 7 others on 21 real-style questions (2026-10-04): best (18/21 first, 21/21 top 3; now 21/21 with the full search), ~76 ms a query. Bigger models did no better |
| bge-small-en-v1.5 (int8 ONNX) | inside the agent | choosing tool groups per message; ranking `web_research` passages | The best and fastest at routing on 51 real messages (10 ms) |
| gemma3:4b | Ollama (loaded when needed) | a scene note for each image shared in the chat | Exact text and good scenes (~40 s an image); moondream invented details, qwen3-vl:2b returned nothing |
| Qwen3-4B Instruct (`qwen3:4b-instruct`, 2.5 GB) | Ollama, as the route `ollama:qwen3:4b-instruct` (4 threads, 8k context, thinking off, JSON output) | background memory work: fact and recipe extraction, summaries, the profile (IDEAS #10). **Not chat. Off since 2026-10-10:** judged blind on 64 differing shadow answers it lost 26-5 to the cloud and did harm 16 times to the cloud's 1 | Private and independent of cloud quotas. `MEMORY_LOCAL_MODE`: `off` (default), `shadow` (the cloud's answer is applied, the local one logged beside it in `data/shadow/memory.jsonl`; judge with `scripts/eval_shadow.py`), `primary` (local first, cloud behind). Skipped while the CPU is over 70% (a render) or Ollama isn't answering. Erasing, and any extraction that deletes, always use the cloud |
| Tesseract 5.4 | the agent | OCR for images and scanned PDFs | |

The benchmarks and decisions are in [IDEAS.md](IDEAS.md) (Done: #15, #16); `scripts/eval_memory.py` re-measures
memory on the owner's real conversations.

## Web search (not AI models, but in the same free-only spirit)

`web_search` tries Tavily, Serper, Brave (free tiers, keys in `.env`), then DuckDuckGo and Wikipedia, which need
no key. `web_research` reads several pages in parallel and keeps the passages closest to the question.

## Routing (`core/llm.py`, `CloudLLM`)

A model is named `provider:model`. Each call walks a list in order and moves on when one is rate limited or
failing. The lists below are a summary of `core/llm.py` on 2026-10-07: **if they disagree, the code wins** (and
[AI_HANDOFF.md](AI_HANDOFF.md) section 3 is updated with it):

| List | Used for | Order |
|---|---|---|
| `CHAT_MODELS` | the chat assistant (with tools) | gemini-3.5-flash-lite, the Flash models (3.7, 3.6, 3.5, 3.8), gemini-3.1-flash-lite, gemma-4-26b-a4b-it, then Groq gpt-oss-120b, Cloudflare gpt-oss-120b, Groq qwen3.8-27b, Groq gpt-oss-20b, Mistral ministral-14b. Why this order: the comment above `CHAT_MODELS` in `core/llm.py` |
| `BACKGROUND_MODELS` | memory upkeep: fact extraction, summaries, the profile | gemini-3.5-flash-lite, gemma-4-26b-a4b-it, gemini-3.1-flash-lite, then Groq and Cloudflare (no Flash: its ~20 a day were chat's) |
| `TASK_MODELS` | the `cloud` engine's coding tasks | gemini-3.5-flash-lite, gemini-3.8-flash, Mistral codestral, then Groq |
| `ROUTER_MODELS` (`core/assistant_tools.py`) | the chat's tool router: which extra tool groups a message needs | Cloudflare Llama 3.3 70B, then Groq gpt-oss-120b; neither answering → the keyword picker. Not Flash-Lite: it's the chat's first model |

TiktokVideos writes its quotes itself (its `QUOTE_MODELS`, default three Gemini Flash-Lite models, with a
45-quote curated bank as the fallback) and reports each call's tokens back to the agent.

**Failover behaviour:**
- **Short rate limits:** it waits up to 20 s, using the provider's reset headers, then retries.
- **Longer rate limits:** that model is skipped for a cooldown (60 s if the provider gives no reset time).
- **Daily limits (resets):** exhausted daily quotas wait until the provider's reset window: Google Gemini waits until midnight US Pacific time (00:00 PT), and Cloudflare waits until 00:00 UTC.
- **Other errors:** the next model is tried.

## Measuring usage

Every call is logged in the `llm_usage` table (`core/llm_usage.py`): the time, provider, model and purpose
(chat, extract, summarize, profile, rollup, affirmation, …), plus prompt, cached and output tokens and the
duration. You can see it at `GET /api/memory/usage`, and the daily budget breakdown at `GET /api/memory/budget`.

What it showed for the week of 2026-09-23:
- At most ~107 calls on a busy day.
- No rate limits hit.
- Almost everything answered by Gemini 3.5 Flash-Lite.

Chat starts with **Gemini 3.5 Flash-Lite** since 2026-10-06 (before that, the Flash models came first; see below
and `docs/GEMINI_FREE_TIER.md`).

The week to 2026-10-04: chat 188 calls (948k prompt tokens, ~87% of all tokens), fact extraction 105, summaries
12, profile 11. **But 47 of the 56 chat replies on Oct 1-4 were answered by Flash-Lite**, because 3.8 Flash
was rate limited. Why, and moving background work off chat's model, is ranked idea #12 in [IDEAS.md](IDEAS.md).

Calls that give no answer are logged in the `llm_failures` table with the reason:
- `overloaded`: a 5xx error, after one retry;
- `timeout`;
- `rate_limit`: a per-minute limit;
- `quota_day`;
- `garbage`;
- `error`.

`GET /api/memory/budget` shows `chat_health`: the share of chat answered by the best model over 24 hours, and the
failures. The morning brief adds one line when less than 70% of chat came from the best model.

Google doesn't report a model's daily limit or what's left of it, so the agent learns the limit. When a model runs
out, the number of calls it answered that day is kept, and the highest count seen wins. The owner gets one message
when a model reaches 80% of that number, and another when it runs out. Only the live agent sends these, and calls
from other programs on the same key aren't counted.

## Free-tier optimizations

1. **Chat on the model that actually answers:** 3.5 Flash-Lite first (500 a day, ~1 s, 20/22 tool choice), the
   Flash models (~20 a day each, often overloaded) behind it, so replies don't wait on refusals. After two failed
   models in one reply, the rest of it skips the other Flash models.
2. **Chores off chat's way:** memory work uses 3.5 Flash-Lite, then Gemma 4 26B (14,400 a day), never the Flash
   models. Every Gemini call is paced under its model's per-minute limit (`MINUTE_LIMITS` in `core/llm.py`).
3. **Respecting daily resets:** models that exhaust their daily allowance are put on cooldown until their reset (midnight Pacific for Google, midnight UTC for Cloudflare).
4. **Daily budget breakdown:** `GET /api/memory/budget` exposes daily token usage and countdowns until provider quota resets.
5. **Multi-tier image generation (`/image`):**
   - **Tier 1 (Safe):** Cloudflare Workers AI Flux 1 Schnell (`@cf/black-forest-labs/flux-1-schnell`) using the configured Cloudflare token.
   - **Tier 2 (Safe):** Cloudflare Workers AI SDXL Base 1.0 (`@cf/stabilityai/stable-diffusion-xl-base-1.0`) fallback.
   - **Tier 3 (Fallback):** Pollinations AI with browser headers and automatic Sana/Flux model selection.
   - **Tier 4 (Universal Fallback):** AI Horde decentralized cluster for general queries.
   *(Note: Google Imagen 3 is strictly omitted and inactive because it requires paid billing, violating the zero-cost invariant).*
   - **Intelligent Prompt Enrichment:** Automatically enriches brief prompts via Gemini 3.8 Flash to maximize visual detail, composition, and lighting before diffusion (skipped for `--raw` prompts).
   - **Artistic Style Presets:** Curated flags for `--style anime`, `--style photo`, `--style 3d`, `--style painting`, `--style pixel`, `--style cyberpunk`, `--style fantasy`, `--style retro`, `--style cinematic`.
6. **Tool routing:** instead of the full tool catalog on every chat call, the core tools (~950 tokens) plus only the groups the message is about (~440 tokens on average, measured on 51 real messages); see [TOOLS.md](TOOLS.md#tool-groups-only-the-tools-a-reply-needs).
7. **Observation & Trace Compression (RTC):** Strips MIME boundaries, headers, web boilerplate, and ANSI escape codes before injecting tool outputs into turn history, reducing observation context overhead by 50%–70%.

## Adding a provider

- **Free plans only,** and never one that can bill without a hard cap.
- **Training:** check whether the provider trains on prompts, and switch it off where possible (Mistral: Admin →
  Privacy).
- **Wiring:** add the key to `.env`, the provider to `CloudLLM.PROVIDERS` and its endpoint and payload
  details, then `provider:model` routes to the lists above.
