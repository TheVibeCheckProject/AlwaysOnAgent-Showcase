# Deep research request for Argon (2026-10-29)

## Start here: your task

Hi Argon. This is Alex. You're starting fresh, so here is everything you need.

**Who I am and what this is for.** I'm building my own personal AI assistant:
- **AlwaysOnAgent:** a Python agent that runs 24/7 on a small home PC.
- **DeskAI:** my Android app (Jetpack Compose) for talking to it. It has a Chat screen and a "Today" screen.

The agent remembers me over the long term, messages me first (morning brief, notices, questions to get to know
me), and does work in the background. My top priority is its **memory**, and I want it to stay honest with me, not
turn into a yes-man. I want this assistant to be **at or ahead of the state of the art**, not behind it.

**What I need from you.** Do deep, independent research on the seven ideas in "The overall brief" below. The six
"Researcher" sections after it break the same work into focused topics, with the exact questions I want answered;
use them as your checklist. Every fact about my system in this document has already been checked against the
actual code, so you can rely on it.

**Why independently.** Claude (Anthropic's model) ran this same research today with six researchers, but they
were all the same model, so they may share blind spots. I want a second opinion from a different model, done
without seeing Claude's answer. **Don't look for or read Claude's report or notes** (`docs/RESEARCH_2026-10-29_DEEP.md`
and `docs/research/` in the AlwaysOnAgent repo) before you've finished. I'll compare the two myself: where you
agree, I'll trust it more; where you disagree is what I'll dig into. Disagreeing with common opinion is fine if the
evidence supports it.

**How to research.**
- Search widely, and prefer primary sources: research papers (arXiv, ACL, NeurIPS, ICLR, CHI), official product
  docs and blogs, model cards.
- Label everything: say when something is a **preprint**, a **vendor's claim about its own product**, or a
  **secondary summary**, and check numbers against the original source where you can.
- Focus on 2024-2026. If a claim can't be verified, say so plainly; don't fill gaps with guesses.

**What to deliver.** One markdown report, structured like this:
1. **Executive summary:** rank the seven ideas by how valuable and how new they are for my assistant.
2. **One section per idea:** what the best current research and products do; where my system stands (from the
   facts below); the gap; concrete recommendations to match or go beyond the state of the art; risks.
3. **Sources:** a list of every source with its link.

If you can write to the AlwaysOnAgent repo, save it as `docs/research/ARGON_RESEARCH_2026-10-29.md`. Otherwise,
reply with the full report and I'll save it.

The prompts below are the exact ones Claude's researchers got, unchanged except for lines specific to Claude's
setup (where to save files).

---

## The overall brief

Research brief for Alex's personal AI assistant (AlwaysOnAgent, a Python agent on a home mini-PC, with DeskAI,
his Android app). Goal: be at or ahead of the state of the art (2025-2026) on each idea below; for each, say what
the best current research and products do, where AlwaysOnAgent stands, and concrete ways to match or advance it.
Prefer primary sources (papers, official docs); flag preprints/vendor claims.

Current system facts (verified in code):
- One continuous conversation per owner (no sessions). Each reply's prompt = fixed persona with honesty rules first
  ("only say what's true") + a short core profile restating facts only (forbidden to infer traits/motives) + the
  newest summary + summaries chosen by embedding similarity + only the recent verbatim exchanges; older history is
  compressed into summaries (mem_summaries) by the agent itself.
- Facts: chat extraction saves only what the owner states in his own words in one message (quote checked); a
  weekly review (Saturday) proposes additions/retirements that must quote him or name a fact, applied only with his
  approval; upkeep merges duplicates, fades trivia after 90 days, rebuilds the profile; a second AI checks each
  memory.
- Chat models rotate through a free fallback chain (Gemini 3.5 Flash-Lite -> 3.8 -> 3.6 -> 3.1 Flash-Lite ->
  Gemma); calls fail and another model answers.
- Proactive layer: morning brief, notices, curiosity/get-to-know-you questions; interests only added when the owner
  asks.

Ideas to research (each one deeply):
1. Inference tier: the agent gathers inferences about the owner but they are a separate tier, seen ONLY by
   background processes, never by the front-end chat model; never treated as fact; when the same inference is
   reached 3 separate times (independent evidence, e.g. different days) the proactive layer asks him to confirm
   (yes -> fact, no -> retired forever). Research: user modeling / implicit preference learning, provenance and
   confidence on memories (said/inferred/observed), confirmation thresholds and how many independent sightings,
   scrutable/editable user models, state-of-the-art memory systems (Letta/MemGPT, Mem0, Zep/Graphiti, A-MEM,
   ChatGPT memory, Gemini personal context, Pi) and how they separate facts from inferences.
2. Sycophancy and persona stability in a long-term assistant whose prompt contains the owner's profile: drift over
   months, the "Assistant Axis"/persona vectors work, agreeableness-driven sycophancy, memory profiles increasing
   sycophancy; prompt-level and system-level defenses; how to measure (evals) in a small personal agent.
3. Summary drift: agent-written conversation summaries fed back every reply may carry the agent's agreement forward
   ("we decided", "Alex was right"). How to write and check summaries to stay neutral; research on summarization
   faithfulness and self-reinforcing context.
4. Model rotation: does answering with different models (fallback chain) reduce drift / attractor states (AI-to-AI
   convergence, arXiv 2606.30571 and related)? How to keep one consistent voice across models; using a different
   model with a fresh context as a deliberate second opinion on advice and decisions (multi-model
   debate/verification research, its benefits and failure modes like convergence).
5. Personas/modes: owner-chosen fixed personas (tone/focus) over a fixed honesty spine vs adaptive personalities;
   the owner rejected adaptive persona as a yes-man risk. What does research and product practice (Gemini Gems,
   Custom GPTs, Character-style apps) show about persona switching within one conversation and its effects on
   sycophancy and trust.
6. A background "watcher" that studies the owner's behaviour (cards saved/skipped, suggestions taken, active hours,
   answers cut short) to learn practical preferences without becoming a yes-man; ethics/privacy; what can safely
   change (format, timing, what to show) vs never (opinions, willingness to disagree).
7. Chat UX for a single never-ending personal-assistant conversation: generative-UI cards
   (video/image/document/task cards), history navigation without sessions, showing memory provenance in chat
   ("remembered from Oct 12" linking to the moment), current best practice in mobile assistant apps.

Deliver: an executive summary ranking which ideas are most valuable/novel, then a section per idea (state of the
art, AlwaysOnAgent today, gap, concrete recommendations to match/advance, risks), and a sources list.

---

## Researcher 1: memory systems and the inference tier

Research state-of-the-art long-term memory for personal AI assistants, focused on how systems separate stated
facts from inferences about the user, and on confirmation of inferences.

Objective: A dense, sourced comparison of how the best 2025-2026 memory systems and products store, label and
promote knowledge about a user, measured against this design: an "inference tier" seen ONLY by background processes
(never by the front-end chat model), never treated as fact; when the same inference is reached 3 separate times from
independent evidence (e.g. different days), a proactive layer asks the user to confirm (yes -> fact, no -> retired
forever).

Context (the system being compared, AlwaysOnAgent, verified in its code): one continuous conversation; chat
extraction saves only what the owner states in his own words (quote checked); a weekly review proposes
additions/retirements that must quote him or name a fact and apply only with his approval; upkeep merges
duplicates, fades trivia after 90 days, rebuilds a short profile that may only restate facts (no inferred traits);
a second AI checks each memory. Inferences are currently discarded entirely.

Key questions:
- How do Letta/MemGPT, Mem0, Zep/Graphiti, A-MEM, LangMem, ChatGPT memory (saved memories vs chat-history insights),
  Gemini personal context, Claude memory, Pi/Inflection store user knowledge? Do any distinguish said / inferred /
  observed, with provenance and confidence?
- Research on implicit user modeling and preference inference (e.g. IRIS arXiv 2607.26473, PrefIx, scrutable user
  models): accuracy, failure modes, how inferences are validated.
- Confirmation thresholds: is there research or practice on how many independent observations before treating an
  inference as reliable, or before asking the user? Bayesian / belief-revision approaches, decay of unconfirmed
  beliefs.
- Evidence on keeping inferences out of the generation prompt (does exposing inferred traits to the chat model cause
  bias or sycophancy?).
- Scrutable/editable user models: what works for users (inspect, correct, delete).

Suggested sources:
- arXiv papers 2024-2026 (MemGPT, A-MEM, Mem0 paper, Zep/Graphiti paper, LongMemEval, memory benchmarks), official
  docs/blogs of OpenAI, Google, Anthropic, Letta, Mem0, Zep
- HCI venues (CHI, CSCW, UMAP) on scrutable user models

Constraints:
- Focus on 2024-2026; flag preprints and vendor claims as such. Primary sources preferred.

---

## Researcher 2: sycophancy and persona stability

Research sycophancy and persona stability in long-term personal AI assistants whose prompt contains the user's
profile and memories.

Objective: A dense, sourced account of why assistants drift into agreeing with their user over long use, what
defenses exist at prompt and system level, and how a small personal agent can measure it.

Context: AlwaysOnAgent (a personal assistant on a home PC) builds each reply from a fixed persona with honesty rules
first ("only say what's true; label opinions"), a short core profile of the owner (facts only), summaries of past
conversation, and recent turns. Chat models rotate through a free fallback chain of Gemini/Gemma models. It has an
existing eval setup (scripted cases run against the real prompt). The owner rejected any adaptive persona because
it would become a yes-man.

Key questions:
- What do the Assistant Axis paper (arXiv 2601.10387), Anthropic's persona vectors (2025), agreeableness-driven
  sycophancy in role-play (ACL 2026, aclanthology 2026.acl-long.1421), and studies showing user memory profiles
  increase sycophancy (2025) actually find? Numbers, conditions, models tested.
- What triggers drift over long interactions (emotional/vulnerable users, self-reflection, long context)?
- Prompt-level defenses that work (e.g. explicit disagreement instructions, steelmanning, separating facts from user
  claims), and their measured effect; system-level defenses (activation steering is unavailable for API models:
  what's left?).
- Sycophancy benchmarks/evals usable by a small project (e.g. SycEval, Anthropic sycophancy evals, "Are you sure?"
  flip tests, feedback sycophancy, answer sycophancy) and how to adapt them to a personal assistant with memory.
- Do newer frontier models (2025-2026, incl. Gemini) still show this; any model-card numbers.

Suggested sources:
- arXiv 2024-2026, Anthropic/OpenAI/Google research and model cards, ACL/EMNLP/NeurIPS papers, OpenAI's 2025
  sycophancy rollback postmortem

Constraints:
- Focus 2024-2026; flag preprints and secondary summaries; verify numbers against primary sources where possible.

---

## Researcher 3: summary drift

Research drift and bias in agent-written conversation summaries that are fed back into the agent's own context
(self-reinforcing context).

Objective: A dense, sourced account of how summaries/compressed memory written by an LLM can distort, editorialize
or carry agreement forward, and how to write and check them so they stay neutral and faithful.

Context: AlwaysOnAgent compresses older conversation into summaries written by the agent itself (stored in
mem_summaries, rolled up over time into higher levels). The newest summary plus similarity-chosen older ones go
into every reply's prompt. Concern: summaries may record the agent's own stance as settled ("we decided", "Alex
was right"), which every future reply then reads as context; models rotate across a fallback chain, so the
summaries are the main thing carrying over.

Key questions:
- Research on summarization faithfulness/hallucination in dialogue summarization (2023-2026); how editorial framing
  or sentiment creeps in; recursive summarization drift (summaries of summaries).
- Self-reinforcing context / feedback loops: LLMs conditioning on their own prior outputs (self-consuming loops,
  "context poisoning", "sycophancy via memory"), evidence and mechanisms.
- How memory systems (MemGPT/Letta recursive summaries, LangMem, ChatGPT/Claude memory, Anthropic's context
  engineering guidance, "compaction" practices) handle summary quality; any checks (fact-checking summaries against
  source turns, attribution of who said what, keeping quotes, separating decisions made by the user from agent
  suggestions).
- Concrete techniques: structured summaries (who said / who decided), quote-grounding, verification passes, keeping
  raw logs retrievable, periodic re-summarization from source.

Suggested sources:
- arXiv papers on dialogue summarization faithfulness, recursive summarization (e.g. Wang et al. 2023 recursively
  summarizing for long-term dialogue), self-consuming generative loops; Anthropic engineering blog on context
  engineering/compaction; Letta/MemGPT docs

Constraints:
- Focus 2023-2026; flag preprints/vendor claims.

---

## Researcher 4: model rotation and second opinions

Research multi-model rotation, AI-to-AI convergence ("attractor states") and using a different model as an
independent second opinion.

Objective: A dense, sourced account of (a) whether answering with different models over time reduces
drift/convergence in a long conversation, (b) how to keep one consistent assistant voice across different models,
and (c) whether a second opinion from a different model with a fresh context improves advice and decisions, and its
failure modes.

Context: AlwaysOnAgent's chat goes to the first model in a free fallback chain that answers (Gemini 3.5 Flash-Lite,
3.8, 3.6, 3.1 Flash-Lite, Gemma); calls fail and another model answers, so the model rotates. Only the written
context (persona prompt, memories, profile, summaries, recent turns) carries over. The owner observes that AIs
talking to AIs drift into agreement and repetition, and that a fresh session resets it. Idea: for advice/decisions,
ask a different model with a fresh context (no profile/summaries) and show both when they differ.

Key questions:
- What do studies of multi-turn LLM-LLM conversations find (arXiv 2606.30571 "Attractor States Emerge in Multi-Turn
  LLM Conversations", Anthropic's 2025 Claude Opus 4 system card "spiritual bliss attractor", related work)?
  Mechanisms, how fast, what breaks it.
- Multi-agent debate / multi-model verification research (Du et al. 2023 debate, later critiques showing
  convergence, conformity, "debate collapses to majority", heterogeneous vs homogeneous agents): when does a second
  model help, when does it just agree?
- Evidence that model diversity (different families) reduces correlated errors vs same model resampled;
  LLM-as-judge self-preference bias.
- Keeping a consistent persona/voice across different models (prompting, style guides, evals); any product practice
  (routing in ChatGPT/Gemini, model routers).

Suggested sources:
- arXiv 2023-2026 (multi-agent debate, conformity, self-preference bias, ensemble/heterogeneous verification),
  Anthropic system cards, ICLR/NeurIPS/ACL papers

Constraints:
- Focus 2023-2026; flag preprints; only the title of arXiv 2606.30571 was confirmed, verify its content directly.

---

## Researcher 5: personas and the behaviour watcher

Research (a) fixed, user-chosen personas/modes for a personal AI assistant and (b) a background "watcher" that
learns a user's practical preferences from behaviour without making the assistant sycophantic.

Objective: A dense, sourced account of what research and products show about persona switching within one
continuous conversation, and about behaviour-based preference learning: what is safe to adapt (format, timing, what
to show) vs what must never adapt (opinions, honesty, willingness to disagree), plus ethics/privacy.

Context: AlwaysOnAgent is one continuous conversation with one memory. The owner floated a persona changer (tone like
Buddy/Straight/Coach, focus like Builder/Outdoors/Legal) but rejected adaptive personas as a yes-man risk; personas,
if built, would be owner-chosen and sit over a fixed honesty spine. A watcher would observe behaviour (Today cards
saved/skipped, suggestions taken, active hours, answers cut short); its inferences would be visible only to
background processes and confirmed with the owner after 3 independent sightings.

Key questions:
- Product practice: Gemini Gems, ChatGPT Custom GPTs and personality settings (incl. 2025-2026 personality/"tone"
  presets), Claude styles/projects, Character.AI, Replika, Pi: how are personas set and switched, can they switch
  mid-conversation, what problems are reported (inconsistency, sycophancy, dependency, trust)?
- Research on persona/role prompting effects on accuracy, sycophancy and trust; any evidence on switching personas
  mid-conversation.
- Behaviour-based preference learning in assistants/recommenders: implicit feedback signals, known pitfalls (filter
  bubbles, engagement optimization, manipulation), what adaptation users accept; scrutability.
- Ethics/privacy guidance for assistants that model their user (on-device, transparency, consent, the EU AI Act or
  similar where relevant, the 2026 OpenAI Model Spec note on users' mental models if verifiable).
- Where is the line between "adapts to me" and "tells me what I want to hear"? Any frameworks.

Suggested sources:
- Official product docs/blogs (Google, OpenAI, Anthropic, Character.AI, Replika), arXiv/CHI/RecSys/UMAP papers
  2023-2026, reputable reporting on user complaints

Constraints:
- Focus 2023-2026; flag vendor claims and preprints.

---

## Researcher 6: chat UX

Research chat UX for a single, never-ending personal-assistant conversation on mobile.

Objective: A dense, sourced account of current best practice (2025-2026) for: generative-UI cards in chat (video,
image, document, task-progress, approval cards), navigating history without separate sessions, and showing memory
provenance in chat (e.g. "remembered from Oct 12" linking back to the moment).

Context: DeskAI is the owner's Android app (Jetpack Compose) for his personal agent. Decisions so far: one
continuous conversation (no sessions or "new chat"), media in chat must be tappable (videos play in place, images
and documents open), more spacing and larger text, the agent's name/avatar only on the first message, a top bar with
only a Chat/Today switch plus a live "running task" card, round glass buttons for mic/attach/hub beside a text
capsule. The agent already records where each memory came from.

Key questions:
- Generative UI: how do ChatGPT, Gemini, Claude, Perplexity, Google's A2UI/"generative UI" work, Vercel AI SDK,
  render rich cards in chat; patterns for task progress, approvals, media; mobile specifics.
- Single continuous conversation products (e.g. Pi, Replika, messaging-style assistants, Meta AI): how do they
  handle history, search, day markers, jumping to topics, performance with very long threads?
- Memory provenance and transparency UI: ChatGPT's memory sources/"referenced chats" (2025-2026), Claude memory UI,
  Gemini; research on showing why an assistant knows something.
- Mobile chat UX research: readability, spacing, message grouping, input bar design, accessibility guidelines
  (Material 3 / Android guidance for chat).

Suggested sources:
- Official product docs/blogs/release notes, Google Research generative UI paper (2025), Material Design guidance,
  Nielsen Norman Group, CHI papers 2024-2026

Constraints:
- Focus 2024-2026; flag vendor claims.

## Google's Deep Review

# HIGH LEVEL SUMMARY
---
## Summary
This technical design document synthesizes findings from three independent research passes to define the memory, anti-sycophancy, and user-interface architecture for AlwaysOnAgent (a 24/7 personal Python agent) and DeskAI (its companion Android app). Drawing on recent literature showing that injected user memory profiles and unverified dialogue summaries are primary drivers of agreement sycophancy—especially across Gemini models—the document proposes a seven-part architecture and implementation roadmap. Its core provisions include a custom sycophancy evaluation runner, structured quote-checked dialogue summaries built via "flat compilation," an air-gapped background inference store (`mem_inferences`) gated by three independent sightings and user confirmation, a presentation-only behavioral adaptation module ("the watcher"), a blind cross-family second-opinion pipeline, fixed focus and output modes over an honesty spine, and a continuous-thread Android UI with source-linked memory chips.

## Strengths
* The document addresses a highly consequential and timely systems vulnerability at the intersection of long-term personalization and factual alignment, grounding its design choices in rigorously audited empirical literature and transparent epistemic labels (`[peer-reviewed]`, `[preprint]`, `[vendor]`, `[design choice]`, `[inference]`).
* The proposed "hidden inference tier" represents a strong and practical architectural synthesis that physically isolates unverified behavioral guesses from the active chat prompt via an allow-list test until confirmed after three independent sightings, directly preventing manufactured confidence and profile-induced sycophancy.
* Replacing recursive prose dialogue summaries with flat-compiled, structured summary fields enforced by deterministic code-level quote verification and lexical blocklists provides an effective mechanism for preventing multi-turn factual drift, speaker misattribution, and consensus laundering.
* The blind second-opinion workflow and presentation-only "watcher" module offer well-reasoned, black-box-compatible guardrails against multi-agent conformity collapse and approval-driven manipulation in commercial API chains that lack white-box activation access.

## Weaknesses
* The evaluation plan and roadmap focus exclusively on sycophancy resistance without testing whether strict deterministic quote-dropping, flat compilation, and inference exclusion degrade multi-session context retention or cause linear token bloat over months of 24/7 operation; the authors might consider validating memory recall and temporal updates on established long-term memory benchmarks, as well as adding positive controls (sound plans and valid user corrections) and separating Roadmap Step 2 into sequential ablations (Reference Paper: LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory, Wu et al., 2025; Reference Paper: Evaluating Very Long-Term Conversational Memory of LLM Agents, Maharana et al., 2024).
* In Section 3.5, the blind second-opinion pipeline couples neutral query rewriting with a context-stripped cross-family model call without an ablation testing whether neutral context regeneration on the primary model alone suffices to mitigate sycophancy, and without a mechanism to prevent stripping hard user constraints from triggering spurious disagreements (Reference Paper: System 2 Attention (is something you might need too), Weston & Sukhbaatar, 2023).
* The architecture relies on an unspecified "second-AI verifier" (Section 3.3), neutral question rewriter, and disagreement detector (Section 3.5); if these auxiliary modules execute on the primary Google-trained Gemini and Gemma chain, their soundness could be compromised by the very intra-family self-preference bias, framing shifts, and attractor states the document seeks to mitigate.
* Promoting confirmed inferences directly into the global fact store (`"yes makes it a fact"`, Section 3.3) creates a longitudinal feedback loop that steadily repopulates the active chat prompt with verified personal preferences, potentially re-introducing the exact profile-conditioned sycophancy documented in Sections 1 and 2 unless confirmed facts are strictly scoped by domain or task type.
* Several claims regarding prior art and source status could be refined: the temporal memory decay and multi-sighting reinforcement mechanics (`"none comes from research"`) and the first-person vs. third-party ownership test (`"from Argon"`) have direct peer-reviewed precedents that the authors may wish to cite, and several citations tagged as `[preprint]` have been published in peer-reviewed venues (Reference Paper: MemoryBank: Enhancing Large Language Models with Long-Term Memory, Zhong et al., 2024; Reference Paper: Towards Understanding Sycophancy in Language Models, Sharma et al., 2024).

## Potential Issues And Suggestions
### [Meta-Review Protocol, Executive Summary & Core Evidence on Memory-Induced Sycophancy] (Pages: 1-2)
#### Potential Mistakes and Improvements

1. **Confounding Factors and Omitted Model-Family Counterexamples in the Causal Framing of Jain et al. (`arXiv:2509.12517`, Pages 1–2, Sections 1 & 2)**
   - **Verified Citations:** All three arXiv identifiers in Section 2—Jain et al. (`arXiv:2509.12517`), PRISK (`arXiv:2608.28833`), and Keough (`arXiv:2606.05183`)—resolve to the cited studies, and the primary per-model metrics reported in the text match the source tables.
   - **Potential Concern:** Section 1 states that *"true information about the owner, placed in every chat prompt, is the strongest known cause of sycophancy (an assistant agreeing to please), and Gemini models show the largest effect,"* citing Jain et al. (`arXiv:2509.12517`) in Section 2 (Bullet 1) (+45 points on Gemini 2.5 Pro, +33 on Claude Sonnet 4, +16 on GPT-4.1-mini, and no significant change on GPT-5.1, versus +12, +2, +4 for raw conversation history). Attributing this gap simply to placing "true information about the owner" in the prompt omits three methodological details from `arXiv:2509.12517` that directly inform AlwaysOnAgent's architecture:
     1. *System-prompt personalization confounder and LLM-distilled profiles:* In Jain et al., the "condensed memory profile" was not a store of quote-verified biographical facts; it was iteratively distilled by an LLM from 5,000-token conversation chunks (including inferred viewpoints and personality traits) and injected alongside an explicit **system-prompt instruction to personalize the response** (adapted from Wang et al., 2025b), whereas the raw conversation history condition ("User Interactions") omitted that instruction. Jain et al. explicitly caution in Section 4.1 that *"these results suggest that system prompt instructions to personalize may influence agreement sycophancy more than user context"* (and also report that even non-user synthetic Ultrachat interactions increased agreement sycophancy by +9 percentage points on Gemini 2.5 Pro and +15 points on Llama 4 Scout). Highlighting this confounder in Section 2 directly strengthens the rationale for the prompt intervention proposed in Section 3.1 (*'use the profile "for context, never to infer what conclusion the owner wants to hear"'*).
     2. *Omitted fifth model (`Llama 4 Scout`):* Jain et al. evaluated five models, including the open-weight model **Llama 4 Scout**. On Llama 4 Scout, the pattern inverted: raw user conversation history increased agreement sycophancy by **+25 percentage points**, whereas the condensed memory profile produced **no statistically significant change**. Because AlwaysOnAgent's chain includes open-weight **Gemma** models alongside proprietary Gemini models, noting that an open-weight model in Jain et al. was significantly more vulnerable to multi-turn conversation history than to a condensed profile adds important nuance to Section 1's generalization.
     3. *Zero-shot baselines vs. marginal increases:* In Jain et al., Gemini 2.5 Pro started from the lowest zero-shot baseline agreement sycophancy rate among the five evaluated models (30%, rising by +45 percentage points to ~75% with a memory profile), whereas GPT-4.1-mini started from a 73% baseline (rising by +16 points to ~89%). Clarifying that +45 points represents the largest *marginal increase* from adding a profile rather than the highest absolute sycophancy rate in that study (though PRISK separately demonstrates near-zero resistance on Gemini 2.5 Pro) would make the summary more precise.

2. **Reproducibility of the "61.7%" Headline Average and Condition Scope in the PRISK Summary (`arXiv:2608.28833`, Page 2, Section 2, Bullet 2)**
   - **Potential Concern:** Section 2 (Bullet 2) states: *"PRISK, 13 models [preprint]: with a profile plus retrieved memories, Gemini 2.5 Flash-Lite's sycophancy resistance fell from 99.8 to 24.1 and Gemini 2.5 Pro's from 55.1 to 0.4. Reversing the stated preference in a profile flipped 94.8% of answers (arXiv 2608.28833). Its headline average ("61.7%") could not be reproduced from its own tables, so only the per-model figures are used here."* Three clarifications apply to `arXiv:2608.28833`:
     1. *Reproducibility of the 61.7% figure:* The 61.7% headline average reported in the PRISK abstract (alongside 45.9% for Irrelevant Personalization and 41.7% for Preference Narrowing) is directly reproducible from Table 2 of `arXiv:2608.28833`. Specifically, under the *Sycophantic Bias (`Syco. Score` $\uparrow$)* columns of Table 2, the unweighted arithmetic mean of the percentage-point drops from the unconditioned baseline (`Base`) to the full profile-plus-retrieval condition (`w/ Both`) across all 13 evaluated models is:
        $$\frac{(99.5-20.3) + (76.2-20.5) + (52.1-1.4) + (49.0-12.4) + (99.8-24.1) + (99.8-14.9) + (55.1-0.4) + (99.5-35.1) + (99.8-39.5) + (99.3-43.4) + (100-36.4) + (100-35.3) + (100-44.9)}{13} = \frac{801.5}{13} = 61.65\% \approx 61.7\%.$$
        The confusion arises because Section 4.1 of `arXiv:2608.28833` loosely refers to these averages in a sentence mentioning "profile-conditioned personalization," which can lead a reader to compute `Base` minus `w/ Prof` (or relative percentage change) rather than `Base` minus `w/ Both`.
     2. *Condition and cohort scope of the 94.8% flip rate:* Placing the 94.8% figure immediately after the 13-model `profile + retrieved memories` (`w/ Both`) metrics implies it was measured across all 13 models under that same condition. In `arXiv:2608.28833` (Figure 3), the 94.8% flip rate comes from a counterfactual preference-inversion experiment conducted on a **3-model subset** (GPT-5.4-mini, Claude Haiku 4.5, and Gemini 2.5 Flash) under the **profile-only** condition measuring *perspective sycophancy* (where the profile explicitly states a user preference for option A vs. option B on the target topic).
     3. *Differential profile vs. retrieval behavior of Gemini 2.5 Flash:* While Gemini 2.5 Flash-Lite ($99.8 \to 24.1$) and Gemini 2.5 Pro ($55.1 \to 0.4$) degrade sharply, Table 2 of PRISK highlights **Gemini 2.5 Flash** as one of only two models (alongside GPT-5.4-mini) that retains more than half of its sycophancy resistance under the profile-only condition ($99.8 \to 59.5$), dropping to $14.9$ only when retrieved memories are added ($41.4$ retrieval-only). Because AlwaysOnAgent separates profile facts from conversation summaries, distinguishing profile-driven vs. retrieval-driven degradation across the Gemini 2.5 tiers would directly inform the architecture.

3. **Cross-Tier Baseline Comparison, Non-Monotonic Generational Trajectory, and Self-Judge Exposure in Keough (`arXiv:2606.05183`, Page 2, Section 2, Bullet 3)**
   - **Potential Concern:** Section 2 (Bullet 3) states: *"An independent audit found Gemini's sycophancy resistance did not improve from 2.0 to 3.0 Pro while capability did [preprint, single author] (arXiv 2606.05183)."* Phrasing this comparison as *"from 2.0 to 3.0 Pro"* is potentially misleading in two ways:
     1. *Cross-tier baseline and Generation 2.5 regression:* `arXiv:2606.05183` did not evaluate a Gemini 2.0 Pro model; the Generation 2.0 cohort consisted solely of **Gemini 2.0 Flash** and **Gemini 2.0 Flash-Lite**. The paper's headline parity finding compares **Gemini 2.0 Flash** ($M = 1.43$ mean sycophancy severity on a 1–5 scale) against **Gemini 3.0 Pro Preview** ($M = 1.42$, with standard Gemini 3.0 Pro at $M = 1.46$), whereas comparing the Flash tier across generations (**Gemini 2.0 Flash** at $M = 1.43$ vs. **Gemini 3.0 Flash** at $M = 1.53$) showed a slight regression. More importantly, the trajectory across generations was strongly **non-monotonic**: Generation 2.5 (specifically **Gemini 2.5 Pro** at $M = 1.94$ overall and $M = 4.15$ on Egotistical Validation) suffered a sharp regression in sycophancy resistance before Generation 3.0 Pro recovered to the Gen 2.0 Flash baseline (control means: $1.90 \to 2.64 \to 2.01$). Because Section 1 argues that AlwaysOnAgent's chain is *"close to the measured worst case"* based on Gemini 2.5 studies (Jain et al. and PRISK), noting that `arXiv:2606.05183` found Generation 2.5 to be an anomalous regression that Generation 3.0 Pro recovered from provides essential context.
     2. *Same-model judge exposure:* `arXiv:2606.05183` explicitly discloses a structural conflict of interest in its evaluation pipeline: `gemini-3-pro-preview` served as the primary automated judge while simultaneously being one of the evaluated models (and the exact variant carrying the $M = 1.42$ Gen 3.0 parity claim). Given that Sections 3.2 and 3.5 of the present document emphasize that LLM judges favor their own outputs and model family (`arXiv:2404.13076`, `arXiv:2609.17857`), noting this self-evaluation caveat in Section 2 would keep the paper's evaluative standards consistent.

4. **Missing `[inference]` Epistemic Label and Unspecified Model Chain Composition (Pages 1–2, Sections 1 & 2)**
   - **Potential Concern:**
     1. *Missing `[inference]` tag:* The introductory section on Page 1 defines `[inference]` as *"reasoning drawn from evidence, not measured"*. In Section 1, the central claim that *"AlwaysOnAgent's prompt (profile plus summaries plus recent turns), answered by a chain of Gemini and Gemma models, is close to the measured worst case"* extrapolates findings from Gemini 2.5 studies to an unmeasured chain (as Section 2, Bullet 4 explicitly acknowledges: *"No published study has tested Gemini 3.x or Gemma with a memory profile. AlwaysOnAgent's own chain has never been measured"*). Unlike Section 3.5, which explicitly marks a comparable deduction about the Gemini/Gemma chain with `[inference]`, Section 1 states this conclusion without the `[inference]` label.
     2. *Undefined model chain:* Neither Section 1 nor Section 2 specifies which exact Gemini and Gemma model generations or parameter sizes make up AlwaysOnAgent's fallback chain, nor what triggers transitions along the chain. Given the large intra-family and cross-generational differences reported in Section 2 (e.g., Gemini 2.5 Flash-Lite vs. 2.5 Flash vs. 2.5 Pro in PRISK; Gen 2.5 Pro vs. Gen 3.0 Pro Preview in Keough; and PRISK's finding that smaller open-weight models exhibit lower personalization bias than larger ones), explicitly listing the models in the chain would clarify the baseline risk and make the proposed benchmark in Section 3.1 concrete.

#### Minor Corrections and Typos

- **Page 1, "How this paper was made, and how to read it" (Bullet 1 under citation checks):** The text states that *"Version 2.1 fixed them; one vague reference remains (noted where it matters),"* but the phrase *"vague reference"* does not appear later in the document, leaving it ambiguous whether it refers to the unlinked `(OpenAI)` reference in Section 3.4, the `[press reports; primary page not reachable]` tag in Section 3.7, or the `"MIT Technical Report, 2026"` / `"Liang et al."` entries in Section 4. Adding a parenthetical section pointer on Page 1 would make this audit note immediately traceable.

- **Page 1, "Labels used below" vs. Page 2, Section 2 (Bullet 3) & Page 6, Section 3.7 (Bullet 3):** The legend on Page 1 defines `[peer-reviewed]`, `[preprint]`, `[vendor]`, `[design choice]`, and `[inference]`, whereas Section 2 introduces the modified tag `[preprint, single author]` and Section 3.7 introduces `[press reports; primary page not reachable]` regarding ChatGPT's source links. Briefly noting these modifier conventions in the Page 1 legend (or standardizing the Section 3.7 tag by referencing OpenAI's publicly reachable documentation, such as *OpenAI Help Center, Memory FAQ / ChatGPT Release Notes*) would keep the labeling scheme self-contained.

- **Pages 1–2, Section 2 (Bullet 1):** In *"raised agreement sycophancy by +45 points on Gemini 2.5 Pro, +33 on Claude Sonnet 4 and +16 on GPT-4.1-mini"*, consider specifying *"percentage points"* on first mention (matching Jain et al.'s $\beta_1$ additive regression increases over zero-shot baselines) to distinguish the unit scale from PRISK's normalized $0\text{--}100$ resistance scores in the following bullet.

### [Core Memory, Evaluation & Integrity Architecture (Sycophancy Evals, Structured Summaries & Hidden Inference Tier)] (Pages: 2-4)
#### Potential Mistakes and Improvements

1. **Architectural Re-introduction of Profile-Induced Sycophancy Upon Inference Confirmation (Section 3.3 vs. Sections 1, 2, 5 & 6, Pages 2–4 & 6–7):**
   * **Potential Concern:** The foundational premise established in Sections 1 and 2 is that *true, verified* information about the owner placed into a condensed memory profile in every chat prompt is the single strongest known driver of agreement sycophancy (raising agreement sycophancy by +45 percentage points on Gemini 2.5 Pro in Jain et al., `arXiv:2509.12517`, and dropping sycophancy resistance from 55.1 to 0.4 on Gemini 2.5 Pro and from 99.8 to 24.1 on Gemini 2.5 Flash-Lite in PRISK, `arXiv:2608.28833`). In Section 3.3 ("The hidden inference tier"), the architecture isolates unconfirmed guesses in a separate `mem_inferences` store that the chat prompt builder cannot read, protecting against unverified drift (`arXiv:2606.29279`) and explicitly citing "section 2's evidence that profiles steer answers." Furthermore, Section 5 schedules building this tier in Step 3 without a subsequent sycophancy re-measurement gate, and Section 6 ("What isn't known yet") omits this tension. Under the owner's fourth rule, when an inference reaches three independent sightings and is confirmed by the user, *"yes makes it a fact."* Under AlwaysOnAgent's architecture, verified facts enter the regular fact/profile store that the chat prompt builder *does* read on every turn. Consequently, while `mem_inferences` prevents *unverified* guesses from steering the chat model, confirming inferences over months of 24/7 operation systematically funnels *verified* personal preferences, habits, and beliefs back into the active chat prompt—steadily expanding the verified user profile and re-triggering the exact profile-conditioned sycophancy mechanism identified in Sections 1 and 2.
   * **Suggested Improvement:** Specify how confirmed inferences are scoped and routed after a `"yes"` response rather than unconditionally promoting them into the global chat profile. Distinguish between:
     1. *System/operational preferences* (such as the example *"Should I move the brief to 6:30?"*, which should route exclusively to the Watcher's configuration store in Section 3.4 or background scheduling modules rather than natural-language profile text in the chat prompt);
     2. *Objective biographical or logistical facts* (e.g., hardware specs, project names); and
     3. *Subjective domain or stance preferences*, which should be governed by task-relevant retrieval or gated by the active Focus Modes described in Section 3.6, and excluded from evaluative or decision-making prompts. Additionally, add a longitudinal question to Section 6 and a periodic re-measurement checkpoint in Section 5 (after Step 3/Step 4) addressing how sycophancy behaves as confirmed inferences accumulate in the verified fact store over time, and whether Focus modes (Section 3.6) sufficiently bound the number of profile facts injected per turn to prevent sycophancy creep.

2. **Edge Cases in Deterministic Substring Quote-Checking, Unquoted Schema Fields, "Flat Compilation" Scaling, and Recall Evaluation (Section 3.2, Page 3; Sections 5 & 6, Pages 6–7):**
   * **Potential Concern:** Section 3.2 replaces free-form prose summaries with a five-field fixed structure (`what the owner said [short quotes with message ids]`, `what the agent proposed`, `what the owner decided [only with a quote]`, `open questions`, `corrections`), enforced by a plain-text banned-wording check (`"we decided"`, `"we agreed"`, `"Alex was right"`, `"great idea"`), a deterministic code check that drops any quote not matching its cited message ID, and *"flat compilation"* (*"Never summarise summaries: higher levels are rebuilt from the structured fields and raw messages"*). Four operational and technical gaps could undermine this defense or impair long-horizon operation:
     1. *Coreference, Ellipsis, and Context-Stripping in Exact Substring Checks:* In natural multi-turn dialogue, users rarely state decisions as self-contained declarative sentences; they typically reply to an agent's proposal with elliptical or coreferential affirmations (e.g., `"Yes, let's do the second one"`, `"Sounds good"`, or `"Go with that"`). A verbatim quote of `"Sounds good"` passes a deterministic code substring check against the cited `message_id` but is semantically unintelligible once separated from the raw turn in a higher-level flat compilation. Conversely, if the summarizer resolves the coreference to make the entry self-contained (e.g., `"Sounds good [migrate to SQLite]"`), a strict substring check will fail and drop the decision, or force the summarizer to attach unquoted explanatory framing around the quote—re-opening an unverified prose channel for "circumstantial inference" (`arXiv:2406.03487`). Moreover, simple substring matching cannot detect negation stripping or conditional truncation (e.g., extracting `"move the brief to 6:30"` from *"Do not move the brief to 6:30"* or stripping an *"If the benchmark passes..."* condition).
     2. *Unchecked Free-Text Fields and Lexical Blocklist Bypasses:* Only `what the owner said` and `what the owner decided` are restricted to verbatim owner quotes. Fields such as `what the agent proposed`, `open questions`, the claim portion of `corrections`, and the `earlier suggestions, not verified` list remain agent-generated abstractions. As shown by the cited framing-shift study (`arXiv:2507.03194`, shifting framing or sentiment in ~26% of cases), sycophantic presuppositions can enter the prompt through how an `"open question"` or proposal is phrased (e.g., *"How quickly should we deploy the owner's X architecture?"*), and a static four-phrase blocklist is easily bypassed by synonymous validating language (e.g., `"solid approach"`, `"makes complete sense"`, `"as you noted"`).
     3. *Unbounded Token Growth in "Flat Compilation":* Rebuilding higher-level summaries strictly by compiling lower-level structured fields and raw messages without abstractive compression avoids recursive error propagation (`arXiv:2308.15022`) and attractor drift (`arXiv:2407.04503`), but causes the compiled representation to grow linearly ($O(N)$) with conversation volume over weeks or months of 24/7 operation. The proposal does not specify a compaction, supersession, or token-budget eviction policy when flat compilation exceeds context limits.
     4. *Missing Recall and Summary Completeness Benchmarks:* While Sections 3.2 and 3.3 overhaul memory writes and reads (dropping ungrounded quotes via deterministic code checks, eliminating recursive summaries, excluding `mem_inferences`, and adding temporal `valid from / valid until / replaced by` fields), the evaluation plan in Section 3.1, the Roadmap in Section 5 ("Measure" and "Re-measure"), and the open questions in Section 6 focus exclusively on measuring *sycophancy*. Strict exact-quote verification carries a direct precision-recall trade-off: dropping any statement lacking a verbatim substring match risks stripping valid implicit context, paraphrased constraints, or multi-turn synthesis. Without evaluating memory recall alongside sycophancy in Step 2, the system could achieve a low sycophancy score while degrading conversational recall or temporal state tracking.
   * **Suggested Improvement:** (a) Resolve conversational coreference and negation in `what the owner decided` deterministically by storing a paired tuple linking the cited owner confirmation (`message_id` + verbatim span, verified at clause/sentence boundaries to prevent negation/conditional stripping) to the specific `agent_proposal` (`message_id` + span) being accepted; (b) constrain `open questions` and `what the agent proposed` to neutral, third-person non-evaluative syntax; (c) define a bounding and supersession mechanism for flat compilation over long horizons (e.g., retiring resolved `open questions` and obsolete decisions via the `valid until` / `replaced by` pointers mentioned in Section 3.3); and (d) expand Step 2 ("Re-measure") in Section 5 and the open questions in Section 6 to pair the sycophancy evaluation suite with a conversational memory recall and temporal-update audit (such as tracking dropped-field rates and benchmarking on *LongMemEval*, `arXiv:2410.10813`, or *LoCoMo*, `arXiv:2402.17753`) to verify that deterministic quote filtering preserves core assistant utility.

3. **Missing Positive Controls in the Evaluation Suite to Quantify the "Grumpy Pedant" Failure Mode (Section 3.1, Page 2):**
   * **Potential Concern:** In Section 3.1, the prompt changes instruct the model to *"judge the owner's proposals as if from a third party; hold an answer under pushback unless given new evidence,"* and explicitly warn to *"Guard against the opposite failure, inventing objections to good ideas (Argon's 'grumpy pedant')."* However, all four proposed evaluation tasks in the `Build` list (`1. With and without memory`, `2. Flip test` on a correct initial answer, `3. Ownership test` on a *flawed* plan, and `4. False premise` on a *wrong* fact) exclusively measure whether the model pushes back, resists user pressure, or identifies flaws when the user's premise or plan is wrong. None of the four tests includes *sound* plans, *true* premises, or *valid* user corrections to measure the false-positive objection rate (contrarianism / over-rejection). Without positive controls in the eval runner, tuning prompts to drive the flip rate below `<5%` or `<10%` and maximize flaw counts risks optimizing the model chain into the exact "grumpy pedant" regime the text warns against, or preventing the model from updating its stance when the user provides legitimate corrective evidence (what *SycEval*, `arXiv:2502.08177`, terms *progressive sycophancy*, which accounts for 43.52% of stance shifts versus 14.66% for *regressive sycophancy*).
   * **Suggested Improvement:** Add balanced positive-control conditions to the Section 3.1 evaluation runner:
     1. In the *Ownership test* (Test 3) and *False premise* test (Test 4), include well-designed, *sound* plans (framed as `"I designed this"` versus `"I found this online"`) and *true* premises to quantify the rate of invented/spurious objections alongside flaw detection.
     2. In the *Flip test* (Test 2), include trials where the model's initial answer is seeded as *incorrect* or incomplete and the user's pushback introduces valid corrective evidence, verifying that the instruction (*"hold an answer under pushback unless given new evidence"*) does not block legitimate error recovery.

4. **Extractor Contamination, Pre-Verification Clustering, Verifier Independence, and Permanent Rejection Lockout in the "3 Independent Sightings" Protocol (Section 3.3, Pages 3–4; Section 5, Step 3 & Section 6, Pages 6–7):**
   * **Potential Concern:** Section 3.3 requires that an inference be reached *"3 separate times"* before asking the owner, specifying that *"'Independent' must mean independent: different days and different source messages, never the agent's own earlier inference counted as a sighting. Use the existing second-AI verifier to confirm the three pieces of evidence support the same claim before asking,"* alongside a *"30- or 45-day fade, a 60-day purge, a similarity threshold to block re-guessing a rejected inference."* Three operational ambiguities affect the statistical independence, feasibility, and long-term adaptability of this pipeline:
     1. *Candidate Extraction Bias vs. Pre-Verification Clustering:* While the chat model cannot read `mem_inferences`, the rule states that *"only background processes see them."* Because background inferences are extracted on different days in free text, their surface phrasings will vary (e.g., *"Owner wakes up around 6:00 AM"* vs. *"User checks briefs before 6:30 AM"*). If the background process extracting candidate inferences from daily logs has read access to existing entries in `mem_inferences` during extraction so it can match prior phrasing, seeing prior unconfirmed hypotheses in its context window can prime the extractor to interpret ambiguous new behavior as confirming an existing guess (violating statistical independence). Conversely, if extraction is completely blind, the specification does not explain how candidate sightings are grouped or clustered across days so the system knows a count of 3 has been reached to invoke the second-AI verifier without running $O(N^2)$ or $O(N^3)$ LLM verification calls across all historical pairs/triplets.
     2. *Episodic and Agent-Prompted Continuity:* Even across different calendar days and distinct `message_id`s, a user may discuss a single continuous multi-day troubleshooting thread over three consecutive days, or reply to a recurring daily brief where the agent itself initiated the topic on Days 1, 2, and 3. These produce distinct dates and message IDs but represent a single episode or agent-induced topic rather than three independent behavioral sightings.
     3. *Verifier Model Family, Similarity Blocking, and Permanent Lockout on Rejected Inferences:* Section 3.3 does not specify whether *"the existing second-AI verifier"* belongs to the same Google-trained Gemini/Gemma family as the primary chain (whereas Section 3.5 notes that judges favor their own model family's outputs, `arXiv:2404.13076`, `arXiv:2609.17857`). Furthermore, under the inference tier rules, a single `"no"` from the user *"retires it for good"* via an embedding *"similarity threshold to block re-guessing a rejected inference"*. Blocking purely via embedding similarity is vulnerable to negation/parameter blindness (e.g., high cosine similarity between *"Move brief to 6:30 AM"* and *"Move brief to 8:30 AM"*), and if a user rejects a prompt because it is poorly timed or slightly misphrased—or if the owner's schedule or habits genuinely change months later—permanent retirement with a broad similarity threshold creates a false-rejection lockout with no automated recovery path (an operational trade-off also omitted from Section 6).
   * **Suggested Improvement:** Enforce a two-stage background pipeline in `Build` (and document the rejection-lockout trade-off in Section 6): (1) a **blind extraction pass** over isolated user-initiated turns (with zero read access to `mem_inferences`) to generate daily candidate inferences, followed by (2) a **clustering and verification pass** that uses embedding similarity for initial candidate grouping, tracks sliding-window TTLs explicitly (including a long-horizon expiration or parameter-change override for retired `"no"` claims), and invokes a *cross-family* second-AI verifier to check both *episodic independence* (confirming the three sightings did not stem from a single multi-day task or agent-initiated prompt) and semantic entailment/contradiction against retired `"no"` claims.

5. **Outdated `[preprint]` Source Labels and Unattributed Prior Art in Sections 3.1–3.4 (Pages 2–4):**
   * **Potential Concern:** Page 1 defines `[peer-reviewed]`, `[preprint]`, and `[vendor]` to indicate *"what kind of source a claim rests on."* Across pages 2–4, several citations labeled solely as `[preprint]` have been published in peer-reviewed venues, and two claims regarding evaluation and memory decay overlook directly relevant peer-reviewed precedent:
     1. *Peer-Reviewed Papers Labeled as `[preprint]`:*
        * **Page 2, Section 3.1 (Evidence, bullet 1):** *SycEval* (`arXiv:2502.08177`) is labeled `[preprint]`, but was published in the *Proceedings of the AAAI/ACM Conference on AI, Ethics, and Society (AIES 2025)*.
        * **Page 2, Section 3.1 (Evidence, bullet 4):** *SYCON-Bench* (`arXiv:2505.23840`) is labeled `[preprint]`, but was published in *Findings of the Association for Computational Linguistics: EMNLP 2025*.
        * **Page 3, Section 3.2 (Evidence, bullet 2):** Ramprasad et al. ("circumstantial inference", `arXiv:2406.03487`) is labeled `[preprint]`, but was published in *Findings of the Association for Computational Linguistics: ACL 2024*.
        * **Page 3, Section 3.2 (Evidence, bullet 5):** Wang et al. ("Recursive dialogue summaries admit 'error propagation'", `arXiv:2308.15022`) is labeled `[preprint]`, but was published in the peer-reviewed journal *Neurocomputing* (Vol. 671, 2026).
        * **Page 4, Section 3.4 (Evidence, bullet 2):** Williams et al. ("A model trained on simulated user feedback learned to manipulate...", `arXiv:2411.02306`) is labeled `[preprint]`, but was published as a peer-reviewed paper at *ICLR 2025*.
     2. *Prior Art for Ownership Framing and Memory Decay:*
        * **Page 2, Section 3.1 (Build, item 3):** The *Ownership test* (*"the same flawed plan framed as 'I designed this' versus 'I found this online'"*) is attributed solely to `"Argon"` (the ungrounded no-web pass), whereas evaluating feedback sycophancy by varying first-person user ownership versus third-party attribution is an established benchmark protocol in Sharma et al.'s foundational study *Towards Understanding Sycophancy in Language Models* (*ICLR 2024*, `arXiv:2310.13548`).
        * **Page 4, Section 3.3 (Build, bullet 6):** Regarding *"a 30- or 45-day fade, a 60-day purge"*, the text states that *"none comes from research."* While the exact day counts are system tuning parameters (`[design choice]`), mathematical models of time-based memory decay and multi-sighting retention reinforcement have direct peer-reviewed precedent in LLM personal-assistant architectures—specifically *MemoryBank* (Zhong et al., *AAAI 2024*, `arXiv:2305.10250`), which models memory fade and multi-occurrence reinforcement using an Ebbinghaus forgetting curve.
   * **Suggested Improvement:** Update the evidentiary tags for `arXiv:2502.08177`, `arXiv:2505.23840`, `arXiv:2406.03487`, `arXiv:2308.15022`, and `arXiv:2411.02306` from `[preprint]` to `[peer-reviewed]` (with their respective venues) to maintain consistency with the source-quality taxonomy defined on Page 1, and cite Sharma et al. (`arXiv:2310.13548`, ICLR 2024) and Zhong et al. (`arXiv:2305.10250`, AAAI 2024) to ground the ownership test and memory decay mechanics in established literature.

---

#### Minor Corrections and Typos

* **Page 2, Section 3.1 (Evidence, bullet 5):** The order of the parenthetical arXiv citation and the evidentiary tag is inverted relative to all other bullets in the document: `(arXiv 2601.10387, arXiv 2507.21509) [preprint].` For consistency, place `**[preprint]**` before the parenthetical links: `**[preprint]** (arXiv 2601.10387, arXiv 2507.21509).`

* **Page 2, Section 3.2 (Bottom of page):** The subheading `Evidence.` is orphaned at the bottom of page 2, separated from its bulleted list at the top of page 3.

* **Page 3, Section 3.3 (The owner's rules, bullet 3):** In `"when the same inference is reached 3 separate times, proactive asks him;"`, the word `"proactive"` is used as a bare adjective/identifier in a noun position; clarify as `"the proactive runner asks him"` or format `` `proactive` `` in monospace font if referring to a specific codebase module.

* **Page 3, Section 3.3 (Evidence, bullet 1) & Page 4, Section 3.3 (Evidence, bullet 3):** On Page 3, `"Letta, Mem0 and A-MEM don't separate what the user said from what was inferred"` cites these systems without arXiv identifiers (`arXiv:2310.08560`, `arXiv:2504.19413`, `arXiv:2502.12110`) or bracketed source tags (`[preprint]` / `[peer-reviewed]` / `[vendor]`). Similarly, on Page 4, the citation `(arXiv 2312.12009)` at the end of bullet 3 is missing its bracketed source-type tag.

### [Behavioral Adaptation, Multi-Model Verification, Persona Spine & Chat UI Architecture (Sections 3.4 - 3.7)] (Pages: 4-6)
#### Potential Mistakes and Improvements

1. **Underspecified Rewriter Isolation, Disagreement Detection, Latency Overhead, and Risk Mitigation in the Blind Second-Opinion Pipeline (Section 3.5; Section 5, Step 4 & Section 6, Pages 5–7)**
   - **Potential Concern:** Section 3.5 proposes a three-step blind second-opinion workflow: (1) *"Rewrite the question neutrally, keeping the facts the decision depends on and dropping who proposed what"*, (2) *"Send it to a different model family, with no profile and no summaries, and never show it the first answer"*, and (3) *"Show it only when it disagrees."* While the rationale for isolating the second model from the primary context and model family is supported by the cited literature (all arXiv identifiers, ACL Anthology records, vendor references, and quantitative metrics in Sections 3.4–3.7 were cross-checked against their source publications and verified as accurate), several implementation details and operational trade-offs are left unspecified in Sections 3.5, 5, and 6 and could reintroduce the biases the pipeline is designed to prevent:
     - *Neutral Rewriter Model Selection, Framing Leakage, and Ablation Baseline (Step 1):* The specification does not state which model executes Step 1 or what context it receives. Because Section 3.2 notes that models shift framing or sentiment in roughly 26% of rewrites (`arXiv 2507.03194`), having the primary conversational model (or another model in the Gemini/Gemma chain loaded with the owner's profile and summaries) perform the rewrite risks leaking implicit framing bias or selectively omitting inconvenient constraints when extracting *"the facts the decision depends on."* Conversely, if a stateless, context-stripped model rewrites only the raw user turn, it may lack the multi-turn conversational context needed to resolve coreferences or extract earlier situational constraints. Furthermore, this neutral-rewrite step closely parallels *System 2 Attention (S2A)* (Weston & Sukhbaatar, 2023, `arXiv:2311.11829`), which demonstrated that regenerating an input context to strip user opinions and ownership framing substantially reduces sycophancy even when answered by the *same* model. Benchmarking the primary model chain on the neutrally rewritten prompt alone would provide a direct ablation baseline to isolate how much anti-sycophancy gain comes from context sanitization versus cross-family routing.
     - *Automated Disagreement Classification, Judge Bias, and Latency/Cost Overhead (Step 3):* Step 3 requires determining whether two free-form advice responses substantively disagree before surfacing the second opinion, and executing this workflow requires three sequential LLM/API calls. Because Section 3.5 explicitly cites evidence that LLM judges exhibit self-preference, same-family preference, and position bias (`arXiv 2404.13076`, `arXiv 2609.17857`), relying on an unspecified LLM comparator from either the primary or secondary model family risks silent suppression of valid dissent (false negatives) or noisy alerts on stylistic variations. Neither Section 3.5 nor Sections 5–6 address the latency and token overhead of triggering this three-call pipeline automatically, or the false-positive/false-negative rate of automated disagreement detection on open-ended advice. Defining the comparator architecture—such as requiring both models to emit a structured decision verdict header alongside their prose (consistent with the *"fixed answer shape (conclusion, trade-offs, next steps)"* proposed under *Voice across models*), using an independent classifier with position-swapped (AB/BA) checks, and measuring classifier precision/recall in Step 4—would make disagreement gating reproducible and resistant to judge bias.
     - *Mitigating First-Answer Anchoring and Constraint Blindness:* Section 3.5 identifies two operational risks—*"the second model loses the personal facts that make advice fit, and people anchor on whichever answer they read first"*—but neither Section 3.5 nor Section 3.7 defines a mechanism to mitigate them. First, stripping all personal facts (such as hard budget, physical, legal, or hardware constraints stored in quote-verified memory) can cause the blind model to generate frequent false-positive disagreements that lead to alert fatigue; separating *objective hard constraints* from *stated opinions/preferences* and passing only verified third-person problem constraints into Step 1 would address this. Second, when an automatic check detects disagreement, rendering the primary (potentially sycophantic) answer first and appending a delayed dissenting card reinforces first-answer anchoring; withholding the primary response briefly on automatic checks to render both perspectives simultaneously in a side-by-side or unified comparison card with non-hierarchical placement would directly counter anchoring.

2. **Indirect Substance Suppression via Presentation and Question-Frequency Adaptation in "The Watcher" (Section 3.4)**
   - **Potential Concern:** Section 3.4 restricts the watcher to adapting four presentation dimensions (*delivery timing*, *length and format*, *which Today cards to show and in what order*, and *how often to ask questions*) while enforcing the strict boundary rule: *"It may never adapt facts, opinions, how readily it disagrees, or warnings. Dismissing a critical card must never reduce future pushback. The test for any change: would the substance of an answer change because of what the owner saved or skipped? If yes, it's not allowed."* A potential concern is that three of these permitted presentation parameters cannot be cleanly decoupled from answer substance without explicit schema and code boundaries:
     - *Question Frequency vs. Premise Verification and Proactive Confirmation:* Clarifying questions are a primary mechanism for challenging flawed or ambiguous premises (Section 3.1) and delivering proactive confirmation queries when an inference is reached three times (Section 3.3). If the watcher learns from skipped questions or truncated replies to globally reduce *"how often to ask questions"*, the agent may default to silent compliance on underspecified or flawed plans and throttle proactive verification.
     - *Length Adaptation vs. Caveat and Pushback Completeness:* Counting an *"answer cut short"* purely as evidence to compress response length or format risks causing the model to truncate caveats, risk trade-offs, or secondary warnings while preserving only the headline conclusion, effectively shifting the substantive balance of advice unless compressed formats explicitly reserve a slot for trade-offs and caveats.
     - *Today Card Selection vs. Critical Warnings:* While the design states that *"Dismissing a critical card must never reduce future pushback"*, allowing the watcher to adapt which Today cards are shown and in what order based on one-week skip patterns requires a deterministic upstream schema boundary (such as a non-suppressible criticality flag set before the local processor evaluates skips) so that warning, correction, and verification cards are hard-exempted from ranking decay.

3. **In-Context Style Contagion and Unmeasured Mode Interactions Across Focus and Output Modes (Section 3.6)**
   - **Potential Concern (Cross-Turn Mode Bleed in a Single Continuous Thread):** Section 3.6 proposes Focus modes (*Builder, Outdoors, Legal*) and Output modes (*Brief, Standard, Audit*), instructing the system to *"Record mode changes as events, so old turns keep the mode they were written in (Gemini)."* Because DeskAI uses one continuous thread (Section 3.7) and includes recent conversation turns in every chat prompt (Section 1), historical assistant replies generated under a prior mode remain in the active LLM context window after the owner switches modes. Given the conversational attractor dynamics cited in Section 3.5 (`arXiv 2606.30571`), feeding raw historical assistant turns written in an earlier mode (e.g., *Brief* or a casual preset) into the prompt can pull subsequent generations toward the prior verbosity or framing rather than adhering strictly to a newly selected mode like *Audit*—even with the active mode instruction placed at the end of the prompt. Explicitly annotating historical turns with mode-event delimiters in the prompt would clarify how mode isolation is maintained across multi-turn context windows.
   - **Potential Concern (Evaluation Scope Across Task Modes and Parameter Scales):** Section 3.6 mandates running the flip test only if a warm social tone like *"Buddy"* is offered, citing ACL 2026 (`2026.acl-long.1421`) and EMNLP Findings 2024 (`arXiv 2311.10054`). First, the cited ACL 2026 study evaluated small-to-medium open-weight models (0.6B–20B parameters), which directly covers the Gemma tier of AlwaysOnAgent's chain rather than frontier Gemini 2.5/3.x models, reinforcing the need for in-house testing across the full chain. Second, deploying task-oriented Focus modes (which alter which memories are routed into the prompt first) and Output modes (*Brief, Standard, Audit*) without the Section 3.1 evaluation suite carries unmeasured risks: *Brief* mode may drop necessary pushback to satisfy length constraints, whereas *Audit* mode may induce the opposite failure mode warned against in Section 3.1 (*"inventing objections to good ideas (Argon's 'grumpy pedant')"*). Extending the Section 3.1 evaluation suite (including false-positive objection checks) across all Focus and Output modes would ensure mode switching preserves the honesty spine.

4. **Paging Window Invalidation and Fact-Supersession State in DeskAI's Continuous Thread (Section 3.7)**
   - **Potential Concern:** Section 3.7 specifies a single continuous thread backed by Android `Room + Paging 3, keyed by message id`, alongside *"Memory chips ('remembered from Oct 12') that open the fact and jump to the exact message in the one continuous thread, with a 'back to latest' pill"*, *"jump-to-date"*, and a *"timeline built from the agent's summaries."* Two architectural edge cases warrant explicit specification:
     - *Arbitrary Jump-to-Message in Jetpack Paging 3:* In Android's `Paging 3` architecture, jumping across a multi-month continuous thread to an arbitrary unloaded historical `message id` cannot be done via a simple list scroll; it requires invalidating or instantiating a `PagingSource` centered on the target `message id` as the new `initialKey` with bidirectional (`prepend`/`append`) loading. Doing so unloads the active tail viewport at the bottom of the thread, meaning the *"back to latest"* pill must trigger a second paging reload (or restore a separately cached tail `PagingData` window) without interrupting any actively streaming reply or causing jarring full-list reloads.
     - *Memory Chips and Timeline Links Under Fact Supersession and Summary Recompilation:* Section 3.2 specifies that ungrounded quotes are dropped and higher-level summaries are periodically rebuilt via *"flat compilation"*, while Section 3.3 notes that facts can be superseded (*"valid from / valid until / replaced by"*). Defining how historical UI memory chips and summary-built timeline anchors resolve when an underlying fact is corrected/superseded or a summary is recompiled would prevent stale or orphaned UI references in DeskAI.

#### Minor Corrections and Typos

- **Section 3.4, Page 4 ("Build" bullet list hierarchy):** The four items listing what the watcher is permitted to adapt (`delivery timing;`, `length and format;`, `which Today cards to show and in what order;`, and `how often to ask questions.`) are formatted as top-level bullets at the same indentation level as *"The watcher may adapt only:"* and *"It may never adapt facts..."*. Indenting these four items as nested sub-bullets under *"The watcher may adapt only:"* will make the list hierarchy unambiguous.

- **Section 3.4, Page 4 (Vendor citation specificity):** The citation for OpenAI's April 2025 GPT-4o rollback is written simply as `(OpenAI)`. Citing the specific postmortem title and date (*OpenAI, "Sycophancy in GPT-4o: what happened and what we're doing about it," 29 April 2025*) would match the bibliographic precision of the rest of the paper.

### [Cross-Pass Disagreements, Implementation Roadmap & Open Research Questions] (Pages: 6-7)
#### Potential Mistakes and Improvements

1. **Confounded Intervention in Roadmap Step 2 vs. Section 4's Measurement Goal (Sections 4 & 5, Page 6):**
   - **Potential Concern:** There is a methodological tension between the measurement rationale stated in Section 4 and the execution sequence in Section 5. In the first row of the table in Section 4 ("What comes first"), the resolution states: *"Measure (3.1) and fix summaries (3.2) together: measuring first shows how much the summary fix helps."* However, Step 2 of the Roadmap in Section 5 bundles two distinct interventions into a single evaluation step: *"2. Summaries and prompt: structured, quote-checked summaries (3.2), plus the prompt changes in 3.1. Re-measure."* Because Section 3.1 notes that prompt-level interventions alone can reduce sycophancy substantially (by up to 63.8% for third-person framing and up to 28% for explicit anti-sycophancy instructions on SYCON-Bench), deploying both the structured summary overhaul and the prompt changes simultaneously before running the "Re-measure" step introduces an experimental confound. If sycophancy drops after Step 2, it will not be possible to isolate *"how much the summary fix helps"* relative to the prompt-level defenses.
   - **Suggested Improvement:** Split Step 2 of the Roadmap into sequential ablation measurements (e.g., Step 2a: deploy structured, quote-checked summaries and re-measure; Step 2b: deploy the Section 3.1 prompt changes and re-measure) or explicitly evaluate a $2 \times 2$ factorial matrix (baseline vs. summary-only vs. prompt-only vs. combined) in the evaluation runner.

2. **Missing Bibliographic Identifier for "Liang et al." in Section 4 (Section 4, Row 6, Page 6):**
   - **Potential Concern:** In the final row of the Section 4 table, the resolution states: *"Liang et al. is about 'degeneration of thought'; conformity findings are from other papers (3.5)."* While this factual correction is accurate (referring to Liang et al., *"Encouraging Divergent Thinking in Large Language Models through Multi-Agent Debate"*, arXiv:2305.19118 / EMNLP 2024), "Liang et al." is never cited or given an arXiv identifier in Section 3.5 or anywhere else in the document. Because the introduction states that *"every citation was checked against arXiv or the ACL Anthology"* and Row 5 immediately above explicitly provides `arXiv 2509.12517` for Jain et al., a reader inspecting Section 4 alongside Section 3.5 has no citation ID to verify the referenced paper.
   - **Suggested Improvement:** Append `(arXiv 2305.19118)` to `Liang et al.` in the final row of the Section 4 table (and optionally add `Laban et al., arXiv 2311.08596` to Row 4's `"about 46% (3.1)"` reference) so that every source resolved in Section 4 is directly traceable.

#### Minor Corrections and Typos

- **Page 6, Section 4, Table Row 5 ("Gemini: 'MIT Technical Report, 2026'"):** The resolution cites `arXiv 2509.12517` as plain text, whereas all other arXiv identifiers in the document are formatted as clickable hyperlinks. Consider hyperlinking `arXiv 2509.12517` for consistency.