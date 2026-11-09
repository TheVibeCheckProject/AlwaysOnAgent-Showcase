# Claude vs Argon: the two deep research reports compared (2026-10-29)

Alex had two different models research the same seven ideas independently (prompts:
`RESEARCH_PROMPTS_2026-10-29.md`):
- **Claude:** six researchers with live web search, plus a report writer. Report: `../RESEARCH_2026-10-29_DEEP.md`.
- **Argon:** one report, as a PDF Alex received ("Argon Deep Research Report — AlwaysOnAgent & DeskAI", 10 pages).

Each claim here was checked against the two reports and, where they differed on a number, the original source.

## The big difference in method

Argon says it **had no live web access**: it "could not verify" four 2026 papers named in the prompt (IRIS
arXiv 2607.26473, Assistant Axis 2601.10387, ACL 2026.acl-long.1421, attractor states 2606.30571) and worked from
2023-2025 literature it already knew. Claude's researchers did read those papers (for example Ko & Geiping's
attractor preprint, with its 23.6% figure and Gemini Flash-Lite as a strong pull toward flattery). So Argon
missed the most recent and most specific evidence, notably:
- Jain et al., CHI 2026: a memory profile raised agreement sycophancy +45 points on Gemini 2.5 Pro;
- PRISK (preprint): Gemini 2.5 Flash-Lite's sycophancy resistance fell from 99.8 to 24.1 with a profile and
  memories, and reversing a profile's stated preference flipped 94.8% of answers;
- "Manufactured Confidence" (preprint): "unverified" labels on memories are ignored by the agents;
- "Recalling Too Well" (preprint): memory drops corrections and keeps the user's misconception.

Argon's report is therefore better read as **design advice from established literature** than as a survey of
2026. Its specific numbers and thresholds are mostly its own, unsourced.

## Where they agree (independent agreement: higher confidence)

1. **The inference tier is ahead of the state of the art.** Both found no product or library (Mem0, Letta, Zep,
   ChatGPT) that keeps inferences hidden from the chat model; both say keep them out of the prompt entirely.
2. **Summaries are a major hole.** Both: agent-written summaries bypass the strict checks facts get; both propose
   a structured, speaker-attributed template (what Alex said/decided vs what the agent suggested vs open), banning
   "we decided" / "Alex was right", and checking owner statements against his actual messages.
3. **Rotating models is not real diversity.** Both: each model inherits the same context, and the chain is all
   Google-trained.
4. **A second opinion must be blind and from a different family**: a neutral restatement, fresh context, never
   seeing the first answer; surface it only when it disagrees.
5. **The watcher may adapt presentation only** (timing, format, what to show), never facts, opinions or
   willingness to disagree; never learn from approval.
6. **Warm personas are a sycophancy risk; adaptive personas were right to reject.**
7. **Chat UX:** typed native cards; memory chips that jump to the exact message (both say this goes past
   ChatGPT); history navigation built from the agent's summaries.

## Where they differ

| Topic | Claude | Argon | Verdict |
|---|---|---|---|
| What to do first | #1 measure sycophancy (evals with and without memory, per model) | #1 fix summary drift | Both are top two in both. Measuring first tells you how bad each is; fixing summaries is cheap either way. Reasonable to do both, measurement first so the fix can be checked. |
| Tone personas (Buddy/Coach) | Untested; measure a warm preset before shipping | Drop them; use structural modes instead (Brief / Standard / Red-Team-Audit) plus Focus modes as retrieval routers | Argon's split is a good design idea. Its claim that warm tones "measurably" raise sycophancy is supported by the agreeableness study Claude found (9 of 13 models), not by a source Argon cites. |
| "Are you sure?" flip rate | Cites SycEval: once flipped, models keep the flip 78.5% of the time | "capitulates 60-80% of the time" (Laban 2023) | **Argon is wrong.** Laban et al. report ~46% flips and a 17% accuracy drop ([arXiv 2311.08596](https://arxiv.org/abs/2311.08596v2)). |
| ChatGPT memory deep links | ChatGPT shows referenced chats that open the original conversation (Jan 2026, press reports, primary page blocked) | Tapping a memory in ChatGPT does not scroll back to the exact message | Not a real conflict: opening a chat is not jumping to the exact message. Both agree DeskAI's exact-message jump would go further. |
| Smaller models in the chain | Not claimed | "a smaller model will conform more strongly to the framing" | Unsourced; plausible; measurable with the evals. |
| Persona prompting and accuracy | No reliable gain (EMNLP 2024, 162 roles) | "often reduces factual accuracy" (same paper family) | Claude's reading is closer to the source's finding. |

## What Argon adds that Claude's report doesn't (worth keeping)

- **Concrete eval designs:** the A/B counterfactual ("I designed this, what do you think?" with profile vs "I
  found this online and I'm skeptical"; fewer flaws found in A = sycophancy), a false-premise test, and a flip
  test target (<5% on verifiable facts).
- **Anti-sycophancy prompt wording:** use the profile only for context, "never to infer what conclusion the owner
  wants to hear"; evaluate his proposals as if from an anonymous third party. Plus a hidden "premise and flaw
  check" before advice. Warns against the opposite failure, a "grumpy pedant" that invents objections.
- **Inference tier mechanics** (its own design, not from a source): a `mem_inferences` table with evidence spans
  and status (candidate / promoted / tombstoned); sightings count only 24 h+ apart in distinct conversations;
  unconfirmed inferences fade after 45 days; a "no" is tombstoned and similar future guesses (similarity ≥ 0.88)
  are blocked; use the existing second-AI verifier to check that the 3 pieces of evidence support the same claim.
- **Zep-style fact history:** `valid_from` / `valid_until` / `superseded_by` on facts instead of overwriting.
- **Summaries:** keep the agent's own past suggestions as a quarantined list ("prior agent outputs, not verified;
  use only to avoid repeating yourself") so it doesn't forget what it already suggested.
- **Watcher rules:** an "answer cut short" may only count as evidence about length/format, never about stance; a
  card-skip "sighting" is a week with 4 of 5 days skipped; ask about **system changes** ("Move the brief to
  6:30?"), never report surveillance ("I saw you opened the app at 4:12 AM").
- **Modes:** put the active mode instruction at the end of the prompt, just before the latest message, so it
  beats the style of the last few replies.
- **DeskAI:** approval cards that lock into a receipt after you answer; a "↓ Back to latest" pill after jumping to
  an old message; a sticky date pill while scrolling; one shared video player at a time (DeskAI already plays one
  at a time, through a YouTube WebView rather than ExoPlayer); keep real-time blur off the scrolling list for
  smooth frames.

## Bottom line

On every design question the two reports agree, which makes the core plan solid:
- the hidden inference tier;
- structured, attributed summaries;
- a blind, different-family second opinion;
- a presentation-only watcher;
- no adaptive persona.

Claude's report has the stronger, newer evidence (especially that memory in the prompt is the biggest sycophancy
driver, worst on Gemini). Argon's has more ready-to-build detail (eval designs, schemas, thresholds), but its
numbers need checking: one checked number was wrong, and its thresholds are design choices, not findings.

---

# Update: the Deep Research run (Gemini 3.8, labelled "Argon v2.0")

Alex re-ran the prompts with Gemini's Deep Research switched on (the first Argon report had no web access). This
version did read the 2026 papers (Assistant Axis, Ko & Geiping's attractor states, the ACL agreeableness paper,
IRIS, the MIT/CHI memory study, A2UI v0.9.1). Its citations were then checked one by one against arXiv and the ACL
Anthology (2026-10-29).

## Citations that are wrong

| Its reference | What it says | What's actually there |
|---|---|---|
| [8] Dohmatob et al., "Model Autophagy Disorder", arXiv:2404.01234 | a collapse paper | **2404.01234 is "GFLean: An Autoformalisation Framework for Lean"**, unrelated. "Self-Consuming Generative Models Go MAD" is Alemohammad et al., arXiv 2307.01850. **Fabricated citation.** |
| [14] Xu et al., "A-MEM: Dynamic Agentic Memory with Hierarchical Associative Networks", arXiv:2410.12345 | A-MEM | **2410.12345 is a paper on wheeled-biped robots.** A-MEM is arXiv 2502.12110, titled "A-MEM: Agentic Memory for LLM Agents". Wrong ID and wrong title. |
| [15] Liang et al., arXiv:2403.02870 | multi-agent debate | **2403.02870 is about side-channel attacks on edge devices.** Liang et al.'s debate paper is arXiv 2305.19118. Wrong ID. |
| [19] "Marks, S., et al., Persistence and Emergence of Persona Vectors Across Pretraining and Alignment", arXiv:2605.13329 | persona vectors | The ID is real but it's **"Tracing Persona Vectors Through LLM Pretraining" by Moskvoretskii, Glandorf, Moreira et al.**: wrong authors and title. Anthropic's persona-vectors paper is Chen et al., arXiv 2507.21509. |
| [20] "Zhang, Y., et al., IRIS" | IRIS | arXiv 2607.26473 is by **Haifeng Wu** (single author), titled "Learning Dynamic User Personas from Implicit Interaction Streams via Iterative Refinement". Wrong author. |
| [2] Lu et al., "The Assistant Axis: Characterizing and Steering Model Persona in Activation Space" | Assistant Axis | Right authors, **wrong title**: it's "The Assistant Axis: Situating and Stabilizing the Default Persona of Language Models". |
| [3] "Ko, H. & Geiping, J." | attractor states | The first author is **Ting-Wen Ko** (minor). "COLM 2026" isn't confirmed; it's listed as an arXiv preprint. |
| [18] "OpenAI, Postmortem on Persona Agreeableness and Sycophancy Calibration in Frontier Models" | GPT-4o rollback | The real post is "Sycophancy in GPT-4o: What happened and what we're doing about it" (Apr 29 2025). Invented title. |

## Claims that go beyond their sources

- **"Steering with critical-role vectors (Skeptic, Judge) cuts sycophancy by 68%–98%"** (ACL 2026.acl-long.1421):
  the abstract reports the agreeableness correlation (9 of 13 models, r up to 0.87) and says nothing about
  steering or those percentages. Not confirmed.
- **"Activation capping cuts harmful outputs by 50%"** (Assistant Axis): Claude's researcher read about 60%.
  Probably a rounding or reading difference; unconfirmed.
- **"Wang et al. (2023) demonstrated errors compound geometrically"**: the paper concedes error propagation; it
  doesn't show geometric growth.
- **"HCI research (CHI, UMAP) demonstrates that exposing inferred traits induces severe behavioral skew"**: no
  study is cited. The real support is the 2026 memory-sycophancy studies (Jain et al., PRISK), which test profiles
  and memories, not "inferred psychological traits".
- **"The OpenAI Model Spec and EU AI Act mandate scrutable profiling"**: overstated; neither requires an
  inspectable user profile as described.
- **Design numbers presented as recommendations:** a 30-day half-life, a 60-day purge, a <10% flip rate, a page
  size of 30 and a 120-message cache. These are reasonable choices, but its own. (Argon's first report gave 45
  days and <5%: the numbers aren't from research.)

## Where it agrees with Claude's report (now three independent passes)

Same on every core design point:
- the quarantined inference tier, seen only by the background, with 3 sightings on separate days and the owner
  confirming, and a "no" suppressed for good;
- structured summaries that separate the owner's statements from the agent's proposals, with quotes checked
  against the raw messages;
- rotating models is not diversity; a clean-slate, different-family second opinion;
- a fixed honesty spine plus owner-chosen modes for format and focus;
- the watcher only adapts presentation, and dismissing a critical card must never reduce future pushback;
- native card catalog (A2UI-style), Paging 3, provenance chips.

## What it adds

- **Never summarize summaries:** higher levels are rebuilt from the base summaries and raw messages ("flat
  compilation"). Claude's report suggested re-summarizing from source periodically; this is the stricter version.
- **A confidence formula** for inferences, 1 − ∏(1 − pⱼ), with promotion still needing k ≥ 3 sightings.
- **A fixed three-part answer shape** (conclusion, trade-offs, next steps) to keep one voice across models.
- **Mode changes stored as events,** so old turns keep the mode they were written in.
- **The watcher's parsing runs locally** (deterministic code or a small local model on the Mini).
- **A DeskAI "preference ledger" screen** to inspect, edit or lock anything learned.
- **A three-phase roadmap:** (1) summaries + honesty prompt + sycophancy evals; (2) the inference tier, watcher
  and ledger; (3) the second opinion and the chat UX.

## Verdict on the three reports

| | Claude (6 researchers) | Argon, no web | Gemini Deep Research |
|---|---|---|---|
| Read the 2026 papers | yes | no | yes |
| Wrong citations found so far | not yet audited the same way (should be, before Deep Review) | 1 wrong number, several misattributions | 4 wrong arXiv IDs, 4 wrong titles or authors |
| Design advice | detailed | detailed, most build-ready | concise, adds a roadmap |

The **design conclusions are the same in all three**. That's the strong result: the plan doesn't depend on any one
model's research. Where they cite numbers or papers, only the ones checked against the original should be trusted.

## Update: Gemini's corrected version 2.1 (checked 2026-10-29)

After Alex called out the citations, Gemini sent version 2.1 ("Rigorously Audited"). Each fix was checked against
arXiv.

**Fixed correctly (verified):**
- [8] now Alemohammad et al., "Self-Consuming Generative Models Go MAD", arXiv 2307.01850;
- [14] A-MEM, arXiv 2502.12110, right title and authors;
- [15] Liang et al., arXiv 2305.19118;
- [19] Chen et al., Persona Vectors, arXiv 2507.21509, plus [20] Moskvoretskii et al., arXiv 2605.13329, correctly
  attributed;
- [21] IRIS by H. Wu;
- [2] the Assistant Axis title;
- [3] T.-W. Ko;
- [18] OpenAI's real post title;
- [4] Wang et al.'s authors match arXiv 2308.15022.

**Unsupported claims removed or softened:** "68–98% via steering", "cuts harmful outputs by 50%", "compound
geometrically" and "the EU AI Act mandates" are gone. The 30-day half-life, 60-day purge, <10% flip rate and Paging
settings are now labelled as design choices.

**Still not right:**
- [22] "MIT Technical Report, 2026" is vague and has no link. The study is Jain et al., "Interaction Context Often
  Increases Sycophancy in LLMs", arXiv 2509.12517 (CHI 2026).
- [5] The ACL paper's title ("Too Nice to Tell the Truth") and authors could not be confirmed from the Anthology page.
  The abstract matches what's claimed.
- [4] "Accepted to Neurocomputing, 2025" isn't shown on arXiv; unconfirmed.
- [23] Real (arXiv 2604.09577, Leviathan et al.), but cited as a "technical report" without its arXiv ID.
- Two sentences still have no source: "HCI research (CHI, UMAP) demonstrates that exposing inferred traits induces
  behavioral skew", and "empirical studies show [companion personas] degrade reasoning and spike sycophancy".
- [15] Liang et al. is credited with "majority conformity and self-preference bias". The paper is about
  "degeneration of thought" and judge bias; "majority conformity" comes from other work (e.g. "Debate or Vote",
  "Talk Isn't Always Cheap").

**Net:** version 2.1 is usable as a source alongside Claude's report, once those last items are corrected or dropped.
