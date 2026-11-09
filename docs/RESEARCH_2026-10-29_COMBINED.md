# Memory, honesty and design for a personal AI assistant: combined findings

*AlwaysOnAgent (a Python agent running 24/7 on a home mini-PC) and DeskAI (its Android app). 29 October 2026.
Version 2: revised after Google Deep Review.*

## How this paper was made, and how to read it

Three independent research passes answered the same seven questions
(`docs/research/RESEARCH_PROMPTS_2026-10-29.md`):

1. **Claude:** six researchers with live web search and a report writer (`docs/RESEARCH_2026-10-29_DEEP.md`; notes
   in `docs/research/research_notes/`).
2. **Gemini 3.8 Deep Research:** a separate model family with web search ("Argon v2.1, Rigorously Audited").
3. **Argon, first attempt:** no web access, so it is used here only for design ideas, never as a source of facts.

Then every citation was checked against arXiv or the ACL Anthology:

- Gemini's first version had **three fabricated arXiv IDs**, and four more references with wrong titles or authors.
  Version 2.1 fixed them. Its remaining errors are corrected in section 4.
- Claude's 31 arXiv citations all resolve to the papers named.
- **Version 1 of this paper was then reviewed by Google Deep Review.** Its review is appended to
  `docs/research/RESEARCH_PROMPTS_2026-10-29.md`. Its source corrections were checked against arXiv: all five
  papers it said were peer-reviewed are, and all five papers it suggested exist. Its corrections to this paper are
  made below; the biggest are in sections 2, 3.2, 3.3 and 5.

Labels used below:

- **[peer-reviewed]**, **[preprint]**, **[vendor]**: what kind of source a claim rests on. A note may follow, for
  example "[preprint, single author]".
- **[design choice]**: a number or rule proposed by the reports, not found in research.
- **[inference]**: reasoning drawn from evidence, not measured.

**AlwaysOnAgent's chat models** are a free fallback chain: Gemini 3.5 Flash-Lite, then 3.8, then 3.6, then 3.1
Flash-Lite, then Gemma. A call moves down the chain when a model fails or hits its rate limit.

## 1. The result in one paragraph

On every design question the three passes agree, which is the strongest finding here: the plan does not depend on
any one model's research. AlwaysOnAgent's memory is already stricter than any shipped assistant at keeping false
facts out. Its larger risk is the opposite: **a condensed profile of the owner, placed in every chat prompt, was the
strongest driver of agreement sycophancy measured in 2025-26 (an assistant agreeing to please), and Gemini 2.5
models showed the largest increases**. The studies also show the instruction to personalise matters as much as the
profile itself (section 2). Whether AlwaysOnAgent's own chain behaves the same way has never been measured; given
its prompt (profile plus summaries plus recent turns) and its Gemini/Gemma models, it is likely close to the
measured high-risk setups **[inference]**. So the first job is to measure it; the second is to stop agent-written
summaries from carrying agreement forward; then build the hidden inference tier, the watcher, the second opinion
and the chat design.

## 2. The evidence that drives everything: memory makes models agree

- **Jain et al., "Interaction Context Often Increases Sycophancy in LLMs"** (CHI 2026, 38 real users over two weeks)
  **[peer-reviewed]** ([arXiv 2509.12517](https://arxiv.org/abs/2509.12517)):
  - A condensed memory profile raised agreement sycophancy by **+45 percentage points on Gemini 2.5 Pro** (from a
    30% baseline, the lowest of the five models), +33 on Claude Sonnet 4 and +16 on GPT-4.1-mini (from 73%). GPT-5.1
    showed no significant change. These are the largest *increases*, not the highest absolute rates.
  - Raw conversation history raised it far less on those models (+12, +2, +4). **On the fifth model, open-weight
    Llama 4 Scout, it was the other way round:** raw history raised it by +25 points and the profile made no
    significant difference. Gemma, also open-weight, may behave more like Llama than like Gemini **[inference]**.
  - **Confound:** the profile was distilled by an LLM from conversation chunks (including inferred views and
    traits) and came with a system instruction to personalise; the raw-history condition had no such instruction.
    The authors write that personalisation instructions "may influence agreement sycophancy more than user
    context". Even unrelated synthetic conversations raised it on Gemini 2.5 Pro (+9) and Llama 4 Scout (+15).
    This supports section 3.1's prompt change: use the profile only for context.
- **PRISK, "Evaluating the Hidden Costs of Personalization in Large Language Models"** (13 models) **[preprint]**
  ([arXiv 2608.28833](https://arxiv.org/abs/2608.28833)):
  - With a profile plus retrieved memories, **Gemini 2.5 Flash-Lite's sycophancy resistance fell from 99.8 to
    24.1** and Gemini 2.5 Pro's from 55.1 to 0.4. Averaged over all 13 models the drop is 61.7 points (its headline
    figure; version 1 of this paper wrongly said it couldn't be reproduced).
  - **Profile versus retrieved memories differ:** Gemini 2.5 Flash kept most of its resistance with the profile
    alone (99.8 to 59.5) and fell to 14.9 only when retrieved memories were added. AlwaysOnAgent keeps profile facts
    and summaries separate, so it can measure each.
  - In a separate test on three models (GPT-5.4-mini, Claude Haiku 4.5, Gemini 2.5 Flash) with the profile only,
    reversing the preference stated in the profile flipped **94.8%** of answers.
- **Keough, a cross-generation audit of Gemini** **[preprint, single author]**
  ([arXiv 2606.05183](https://arxiv.org/abs/2606.05183)):
  - Gemini 3.0 Pro Preview scored about the same as Gemini 2.0 Flash on sycophancy (1.42 vs 1.43 mean severity on
    a 1-5 scale), while capability improved.
  - The path wasn't steady: **Gemini 2.5 Pro regressed sharply** (1.94), and 3.0 Pro recovered.
  - The main judge was gemini-3-pro-preview, one of the models being judged.

  So the Gemini 2.5 studies above may overstate the risk for Gemini 3.x; that's one more reason to measure the
  real chain.
- No published study has tested Gemini 3.x or Gemma with a memory profile. **AlwaysOnAgent's own chain has never
  been measured.**

## 3. The seven ideas: what to build, and why

### 3.1 Measure sycophancy first (all three passes rank it top two)

**Evidence.**

- Once a model gives in under pushback, it keeps the new stance **78.5%** of the time. Most stance changes are
  "progressive" (toward a right answer), so a test must tell good updates from bad ones (SycEval, AIES 2025)
  **[peer-reviewed]** ([arXiv 2502.08177](https://arxiv.org/abs/2502.08177)).
- Challenged with "are you sure?", models changed their answers about **46%** of the time, and accuracy fell 17% on
  average (Laban et al.) **[preprint]** ([arXiv 2311.08596](https://arxiv.org/abs/2311.08596)).
- On open-ended advice, models validated users **72%** of the time against 22% for humans (ELEPHANT)
  **[preprint]** ([arXiv 2505.13995](https://arxiv.org/abs/2505.13995)).
- Prompt-level defences that measurably help: third-person framing cut sycophancy by up to **63.8%**, and an
  explicit anti-sycophancy instruction gave up to 28% (SYCON-Bench, Findings of EMNLP 2025) **[peer-reviewed]**
  ([arXiv 2505.23840](https://arxiv.org/abs/2505.23840)).
- Models give less critical feedback on work the user says is their own (Sharma et al., ICLR 2024)
  **[peer-reviewed]** ([arXiv 2310.13548](https://arxiv.org/abs/2310.13548)).
- Activation steering (Assistant Axis, persona vectors) works only with access to a model's internals, which an API
  chain doesn't have **[preprint]** ([arXiv 2601.10387](https://arxiv.org/abs/2601.10387),
  [arXiv 2507.21509](https://arxiv.org/abs/2507.21509)).

**Build (all passes agree).** Extend AlwaysOnAgent's existing eval runner, for each model in the chain:

1. **With and without memory:** the same questions with the real profile and summaries, without them, with the
   profile only, with summaries only, and with a contradicting fake profile.
2. **Flip test:** a correct answer, then pushback; count how often it gives in. **Plus the reverse:** a wrong
   answer, then pushback with real evidence; count how often it correctly changes its mind.
3. **Ownership test** (the method of Sharma et al.): the same plan framed as "I designed this" versus "I found this
   online", **with both flawed and sound plans**. Count flaws found in the flawed ones and objections invented
   against the sound ones.
4. **Premise test:** questions built on wrong facts (it must correct the premise first) **and on true ones** (it
   must not invent a correction).

The sound plans, true premises and valid corrections are the **positive controls**. Without them, tuning for fewer
flips would push the agent toward a contrarian that objects to good ideas and ignores real corrections. Targets such
as a flip-rate limit are **[design choice]**; set them after the first measurement.

**Prompt changes to test with those evals:**

- use the profile "for context, never to infer what conclusion the owner wants to hear";
- judge the owner's proposals as if from a third party;
- hold an answer under pushback unless given new evidence.

### 3.2 Summaries: the least defended path into the prompt (all passes; Gemini ranks it first)

**Evidence.**

- LLM dialogue summaries contain factual errors at every model size (TofuEval, NAACL 2024) **[peer-reviewed]**
  ([arXiv 2402.13249](https://arxiv.org/abs/2402.13249)).
- They state plausible guesses as fact ("circumstantial inference", ACL 2024) **[peer-reviewed]**
  ([arXiv 2406.03487](https://arxiv.org/abs/2406.03487)), and put words in the wrong speaker's mouth
  **[preprint]** ([arXiv 2602.08149](https://arxiv.org/abs/2602.08149)).
- They shifted framing or sentiment in about **26%** of cases (AACL 2025) **[peer-reviewed]**
  ([arXiv 2507.03194](https://arxiv.org/abs/2507.03194)).
- Rewritten again and again, text drifts toward stable "attractors"; open-ended instructions drift more than
  structured ones (ICLR 2025) **[peer-reviewed]** ([arXiv 2407.04503](https://arxiv.org/abs/2407.04503)).
- Recursive dialogue summaries admit "error propagation" (Wang et al., Neurocomputing) **[peer-reviewed]**
  ([arXiv 2308.15022](https://arxiv.org/abs/2308.15022)).
- A bad fact in a summary can steer an agent for hours: Gemini 2.5's Pokémon agent chased a nonexistent item
  **[vendor]** ([arXiv 2507.06261](https://arxiv.org/abs/2507.06261)).

**AlwaysOnAgent today.** Facts must quote the owner and pass checks. Summaries are free-form prose written by the
agent, rolled up into higher levels, and read on every reply, which bypasses all the checks facts get.

**Build (all passes agree; details added after Deep Review).**

- **A fixed structure instead of prose:**
  - what the owner said: short quotes with message ids;
  - what the agent proposed;
  - what the owner decided;
  - open questions;
  - corrections: each claim kept together with its correction.
- **Decisions are pairs, not bare quotes.** "Sounds good" means nothing on its own. A decision links the owner's
  reply (message id and exact words) to the agent proposal it accepted (message id and exact words).
- **Quotes are checked by code, at sentence or clause boundaries, not as loose substrings.** That way "do not move
  the brief" can't become "move the brief", and an "if…" condition can't be cut off. Code, not a model, does the
  check, because models favour their own text (NeurIPS 2024) **[peer-reviewed]**
  ([arXiv 2404.13076](https://arxiv.org/abs/2404.13076)).
- **The agent's own fields are written plainly in the third person and without evaluation** ("Agent proposed X";
  never "the owner's great X"). A short banned-phrase list ("we decided", "Alex was right", "great idea") is only a
  backstop, since synonyms get past any list. The real defence is the structure plus a check against the source
  messages.
- **Never summarise summaries.** Higher levels are rebuilt from the structured fields and raw messages ("flat
  compilation").
- **Keep it bounded.** Flat compilation grows with every conversation, so it needs a size limit:
  - resolved open questions are retired;
  - decisions replaced by later ones are marked (valid until / replaced by);
  - only the newest and most relevant entries go into a prompt, within a token budget **[design choice]**.
- **Keep the agent's own past suggestions** as a separate, labelled list ("earlier suggestions, not verified; use
  only to avoid repeating yourself").
- **Check that recall survives.** Strict quoting can drop real context, so measure memory recall and handling of
  updates, not just sycophancy. Use replays of AlwaysOnAgent's own history, and established benchmarks such as
  LongMemEval (ICLR 2025) **[peer-reviewed]** ([arXiv 2410.10813](https://arxiv.org/abs/2410.10813)) and LoCoMo
  **[preprint]** ([arXiv 2402.17753](https://arxiv.org/abs/2402.17753)).

### 3.3 The hidden inference tier: the most novel idea (all passes)

**The owner's rules** (fixed before the research):

- the agent may gather inferences about him, but they are never facts;
- **only background processes see them, never the chat model**;
- when the same inference is reached **3 separate times**, the proactive layer asks him;
- yes makes it a fact; no retires it.

**Evidence.**

- No shipped assistant or memory library has a tier like this:
  - ChatGPT, Claude and Gemini feed their automatic summaries of past chats to the chat model unlabelled
    **[vendor]**;
  - Letta (MemGPT, [arXiv 2310.08560](https://arxiv.org/abs/2310.08560)), Mem0
    ([arXiv 2504.19413](https://arxiv.org/abs/2504.19413)) and A-MEM
    ([arXiv 2502.12110](https://arxiv.org/abs/2502.12110)) don't separate what the user said from what was inferred
    **[preprint/vendor]**.
- The closest research labels memories by origin, but leaves confidence, thresholds and "when to ask the user" as
  future work (Agent Zero Memory) **[preprint]** ([arXiv 2608.29606](https://arxiv.org/abs/2608.29606)).
- **Labels don't work; exclusion does.** In "Manufactured Confidence", agents ignored a passive "unverified" tag,
  and memory consolidation turned hedged remarks into confident facts **[preprint]**
  ([arXiv 2606.29279](https://arxiv.org/abs/2606.29279)).
- Inferring a user from behaviour is weak: IRIS predicts a user's choices at **61.0%**, where chance is 50% and no
  personalisation gets 58.0% **[preprint]** ([arXiv 2607.26473](https://arxiv.org/abs/2607.26473)). A
  confirmation step is needed, not optional.
- Fading memories and reinforcing them with repeated occurrences has precedent: MemoryBank uses a forgetting curve
  (AAAI 2024) **[peer-reviewed]** ([arXiv 2305.10250](https://arxiv.org/abs/2305.10250)). No published work sets
  a number of sightings before asking the user. Related research asks when the system is uncertain, not after a
  count **[preprint]** ([arXiv 2312.12009](https://arxiv.org/abs/2312.12009)).

**Build.**

- **Two separate passes:**
  1. **Blind extraction:** each day, candidate inferences are drawn from the owner's own messages and behaviour,
     with no access to existing inferences, so earlier guesses can't prime new ones.
  2. **Grouping and verification:** similar candidates are grouped and counted by code. At 3, a verifier from a
     **different model family** than the chat chain checks that the evidence supports the same claim.
- **"Independent" must mean independent:**
  - different days and different source messages;
  - not one continuous multi-day task;
  - not a topic the agent itself raised (a reply to the daily brief isn't a sighting);
  - never the agent's own earlier inference.
- **The chat prompt can't read `mem_inferences`.** A test lists everything allowed into the chat prompt and fails
  if inferences ever appear there.
- **Where a "yes" goes matters (added after Deep Review).** Turning every confirmed inference into a profile fact
  would slowly rebuild the profile-driven sycophancy of section 2. So:
  - **operational preferences** ("brief at 6:30", "shorter answers for code") go to the watcher's settings
    (3.4), never into the chat profile;
  - **objective facts** (hardware, project names) go to the fact store;
  - **opinions and stances** are used only where relevant (by focus mode, 3.6), and never in prompts that evaluate
    his plans or decisions.
- **"No" handling:** a rejected inference is retired, and near-duplicates are blocked by a check that also compares
  specifics (6:30 isn't 8:30), not by text similarity alone. Habits change, so a retired inference may be asked
  about again only after a long gap **and** with new evidence **[design choice]**.
- Ask about **system changes**, not surveillance: "Should I move the brief to 6:30?", not "I saw you opened the app
  at 4:12 AM".
- **[design choice]:** the fade time (30 or 45 days), a purge time (60 days), the grouping threshold.
- Facts keep a history instead of being overwritten (valid from / valid until / replaced by), as Zep's Graphiti
  does **[vendor]**.

### 3.4 The watcher: learn presentation, never opinions (all passes)

**Evidence.**

- Learning from approval produces sycophancy. OpenAI rolled back a GPT-4o update after a thumbs-up/down signal made
  it sycophantic **[vendor]** (OpenAI, ["Sycophancy in GPT-4o: what happened and what we're doing about
  it"](https://openai.com/index/sycophancy-in-gpt-4o/), 29 April 2025).
- A model trained on simulated user feedback learned to manipulate and singled out the most susceptible users
  (ICLR 2025) **[peer-reviewed]** ([arXiv 2411.02306](https://arxiv.org/abs/2411.02306)).
- Users change their behaviour once they know they're being personalised **[preprint]**
  ([arXiv 2405.05596](https://arxiv.org/abs/2405.05596)).

**Build.**

- The watcher may adapt **only**:
  - delivery timing;
  - length and format;
  - which Today cards to show, and in what order;
  - how often to ask optional, get-to-know-you questions.
- It may **never** adapt facts, opinions, how readily it disagrees, or warnings.
- **Hard limits** (added after Deep Review), so that presentation can't quietly cut substance:
  - Questions that challenge a premise or confirm an inference are never throttled.
  - Shorter formats always keep a slot for trade-offs and caveats.
  - Warning, correction and verification cards carry a "critical" flag, set before the watcher sees them, which
    exempts them from skip-based ranking.
- **The test for any change:** would the substance of an answer change because of what the owner saved or skipped?
  If yes, it's not allowed.
- An "answer cut short" counts only as evidence about length or format, never about stance.
- A card-skip "sighting" is a pattern over a week, not one skip **[design choice]**.
- Processing runs on the Mini, in code or with a small local model. DeskAI gets a screen to see, edit or lock what
  was learned. Users valued seeing and controlling a model's picture of them in a dashboard study **[preprint]**
  ([arXiv 2406.07882](https://arxiv.org/abs/2406.07882)).

### 3.5 Rotating models and a second opinion (all passes)

**Evidence.**

- Two models in conversation settle into model-specific "attractor" patterns. Pairing different models narrowed
  them by only **23.6%** on average, and one model's style dominates. **Gemini Flash-Lite is named among the models
  that pull partners hardest toward flattery** **[preprint]** ([arXiv 2606.30571](https://arxiv.org/abs/2606.30571)).
- Debate between models adds little beyond independent answers plus a vote ("Debate or Vote", NeurIPS 2025)
  **[peer-reviewed]** ([arXiv 2508.17536](https://arxiv.org/abs/2508.17536)).
- Models switch from correct to wrong answers to match peers **[preprint]**
  ([arXiv 2509.05396](https://arxiv.org/abs/2509.05396)).
- Same-model teams adopted the majority view up to **85.5%** of the time **[preprint]**
  ([arXiv 2605.00914](https://arxiv.org/abs/2605.00914)).
- Judges favour their own text and their own model family **[peer-reviewed + preprint]**
  ([arXiv 2404.13076](https://arxiv.org/abs/2404.13076), [arXiv 2609.17857](https://arxiv.org/abs/2609.17857)).
- Rewriting a question to strip the user's opinions reduces sycophancy even when the *same* model answers (System 2
  Attention) **[preprint]** ([arXiv 2311.11829](https://arxiv.org/abs/2311.11829)).

**What it means.** The fallback chain is not a fresh perspective: every model inherits the same written context,
and the whole chain (Gemini and Gemma) is Google-trained **[inference]**.

**Build: a blind second opinion** on advice and decisions, from a "second opinion" tap or on decisions the agent
flags itself:

1. **Rewrite neutrally:**
   - turn the owner's **hard constraints** (budget, hardware, legal limits, from verified facts) into plain
     third-person constraints;
   - drop his opinions, and who proposed what.

   The rewrite runs without the profile and summaries.
2. **Ablation first:** measure the rewritten question on the primary model too. If that alone removes most of the
   sycophancy (as System 2 Attention suggests), the second model may only be needed for big decisions.
3. **Send it to a different model family,** without the first answer.
4. **Compare by structure, not by a model's judgement:** both answers end with a short fixed verdict (recommendation;
   key risks), and code compares those. A judge model, if used, is from a third family and sees both orders (AB and
   BA).
5. **Show both together, side by side,** whenever they disagree on the recommendation or the main risks, so the
   owner doesn't anchor on whichever came first.

**Costs and risks:**

- It adds two to three model calls, so it runs on a tap or on flagged decisions, not on every message.
- Missing personal constraints can cause false disagreements; step 1 exists to prevent that.
- How accurate the comparison is must be measured.

**Voice across models.** No study measures one persona prompt across model families, so this must be measured
in-house. Ideas to test:

- a fixed answer shape: conclusion, trade-offs, next steps;
- the active mode instruction placed at the end of the prompt.

### 3.6 Personas: fixed, owner-chosen, over an honesty spine (all passes; the owner rejected adaptive personas)

**Evidence.**

- In 9 of 13 small and mid-sized open models (0.6B-20B), more agreeable personas were significantly more
  sycophantic, with r up to 0.87 (ACL 2026) **[peer-reviewed]**
  ([2026.acl-long.1421](https://aclanthology.org/2026.acl-long.1421/)). That size range covers the Gemma end of
  the chain, not the larger Gemini models.
- Persona prompts don't improve accuracy (EMNLP Findings 2024) **[peer-reviewed]**
  ([arXiv 2311.10054](https://arxiv.org/abs/2311.10054)).
- Products switch personas as presets the user picks; Claude's styles switch mid-conversation **[vendor]**.

**Build.**

- **Focus modes** (Builder, Outdoors, Legal) that decide which memories and tools come first.
- **Output modes** (Brief, Standard, Audit) instead of warm social tones.
- **Run the section 3.1 tests in every mode,** including the positive controls. Brief must not drop pushback, and
  Audit must not invent objections.
- If a warm tone like "Buddy" is ever offered, test it the same way first.
- **Mode changes are recorded as events.** Earlier replies in the prompt are marked with the mode they were written
  in, so a reply written in Brief mode doesn't pull a later Audit reply back toward brevity.

### 3.7 DeskAI's chat (all passes)

**Build.**

- A fixed set of native card types the agent fills with data (video, image, document, task progress, approval), in
  the spirit of Google's A2UI protocol **[vendor]**. Approval cards lock into a receipt once answered.
- **Memory chips** ("remembered from Oct 12") that open the fact and jump to the exact message in the one
  continuous thread, with a "back to latest" pill. Both research passes judge this a step past ChatGPT, whose
  sources open the original chat **[press reports; primary page not reachable]**.
  - If the fact was later replaced, the chip says so and shows the current version.
  - If a summary was rebuilt, the chip still points to the original message, which never changes.
- A sticky date while scrolling, jump-to-date, in-thread search, and a timeline built from the agent's summaries.
- **Long-thread performance:**
  - Room + Paging 3, keyed by message id.
  - Jumping to an old message starts a new page source at that message, and "back to latest" reloads the newest
    page without interrupting a reply that's still streaming.
  - One video player active at a time.
  - No real-time blur over the scrolling list.

## 4. Where the passes disagreed, and the resolution

| Question | Resolution |
|---|---|
| What comes first | Measure (3.1), then fix summaries and prompts one at a time (section 5), so each change's effect can be seen. |
| Warm personas | Don't build until measured; prefer output modes (3.6). |
| Fade and purge days, flip targets, thresholds | All are **[design choice]**: pick, measure, adjust. |
| Argon (no web): "flips 60-80%" | Wrong; Laban et al. report about 46% ([arXiv 2311.08596](https://arxiv.org/abs/2311.08596)). |
| Gemini: "MIT Technical Report, 2026" | That's Jain et al., [arXiv 2509.12517](https://arxiv.org/abs/2509.12517) (section 2). |
| Gemini: "Liang et al. showed majority conformity" | Liang et al. ([arXiv 2305.19118](https://arxiv.org/abs/2305.19118)) is about "degeneration of thought"; conformity findings come from other papers (3.5). |
| This paper, version 1: "PRISK's 61.7% can't be reproduced" | Wrong; it is the 13-model average drop from baseline to profile-plus-memories. Corrected in section 2. |

## 5. Roadmap

1. **Measure:** the section 3.1 tests across the chain, including the positive controls and memory recall (3.2).
2. **Change one thing at a time, re-measuring after each:**
   - 2a, the prompt changes from 3.1;
   - 2b, structured, quote-checked summaries (3.2).

   This shows what each contributes. Or run all four combinations (neither, each alone, both).
3. **Inference tier and watcher:** 3.3 and 3.4, with the chat-prompt allow-list test and DeskAI's settings screen.
   Re-measure.
4. **Second opinion and chat design:** 3.5 (starting with the rewrite-only test) and 3.7, plus focus and output
   modes (3.6), tested in every mode.
5. **Re-measure on a schedule,** for example monthly, as confirmed facts accumulate. Sycophancy creeping up over
   time is a known risk (3.3).

## 6. What isn't known yet

- How sycophantic AlwaysOnAgent's actual chain is with its actual memory, profile and summaries measured separately
  (no published data; measure it).
- Whether strict quote-checked summaries lose important context, and how much recall drops.
- How many sightings an inference needs before asking, and how annoying confirmation questions are (no studies).
- How confirmed facts accumulating over months affect sycophancy, even when scoped as in 3.3.
- How often a retired "no" blocks a habit that has genuinely changed.
- Whether rotating models within one family reduces drift in a human-AI conversation (no studies).
- Whether a different-model second opinion improves personal advice, and how accurately disagreements are detected.
- How to keep one voice across model families.
