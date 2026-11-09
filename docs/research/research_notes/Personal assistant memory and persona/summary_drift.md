# Drift and bias in agent-written conversation summaries fed back into the agent's own context

Scope: how LLM-written summaries / compressed memory distort, editorialize or carry agreement forward, and how to write and check them so they stay neutral and faithful. Focus 2023-2026. Most sources are arXiv preprints unless a venue is given. Several facts come from abstracts or listing pages rather than full papers; these are flagged inline.

## 1. Faithfulness and hallucination in LLM dialogue summarization; how editorial framing creeps in; recursive drift

### Takeaway
LLM dialogue summaries contain factual errors at every model size. A distinctive modern error is "circumstantial inference": plausible conclusions stated as fact that the conversation never directly said. LLM summaries also add framing or sentiment not present in the source in roughly a fifth to a quarter of cases. Iterated rewriting (summaries of summaries) pulls content toward "attractor" states, and open-ended instructions drift most.

### Cited Findings
- TofuEval (NAACL 2024, peer-reviewed): sentence-level human factual-consistency labels on topic-focused dialogue summaries. LLMs make many factual errors on dialogue regardless of size. GPT-4 as a binary factuality judge performs poorly and is beaten by specialized factuality metrics — [ACL Anthology](https://aclanthology.org/2024.naacl-long.251); [arXiv 2402.13249](https://arxiv.org/abs/2402.13249v2)
- "Analyzing LLM Behavior in Dialogue Summarization" (arXiv preprint, June 2024): GPT-4 and Alpaca-13B often make plausible inferences backed by circumstantial rather than direct evidence. The authors add a new "Circumstantial Inference" error category, which is more common in LLMs than in older summarizers — [arXiv 2406.03487](https://www.arxiv.org/abs/2406.03487?context=cs)
- DIAL-SUMMER (arXiv 2026): defines speaker misattribution ("speakers are swapped/misreferenced") as a dialogue-level error. Turns in the middle of a dialogue are the most often dropped — [arXiv 2602.08149](https://arxiv.org/pdf/2602.08149)
- Abstractive dialogue summarizers "frequently" attribute actions or statements to the wrong speaker — [IWSDS 2026](https://aclanthology.org/2026.iwsds-1.17.pdf) (from a snippet)
- AUTOSUMM (ACL 2025 industry track): lists speaker attribution errors as a core challenge and uses layered syntactic, semantic and entailment hallucination checks — [ACL Anthology](https://preview.aclanthology.org/setup/2025.acl-industry.35)
- "Quantifying Cognitive Bias Induction in LLM-Generated Content" (IJCNLP-AACL 2025):
  - LLM summaries shifted framing or sentiment in about 26.4% of cases (v2; v1 reported 21.86%), averaged over five model families.
  - Readers were about 32% more likely to buy a product after reading an LLM summary of reviews than after reading the originals.
  - Primacy bias appeared in about 10% of cases.
  - 18 mitigations were tested; targeted ones help.
  — [arXiv 2507.03194v2](https://arxiv.org/abs/2507.03194v2); [MediaPost](https://www.mediapost.com/publications/article/412897/chatbot-reviews-are-biased-and-consumers-bite.html)
- "Frame In, Frame Out" (arXiv 2025):
  - LLMs add framing more often than human XSum summaries across all topics.
  - Larger pretrained models (GPT, LLaMA, Cohere, Claude) framed more than T5/BART (p = 0.0012).
  - Task fine-tuning reduced framing.
  - Example: a neutral shark-tagging story was recast as potentially harmful to the sharks.
  - Caveat: framing was judged by GPT models, not humans.
  — [arXiv 2505.05406](https://arxiv.org/html/2505.05406v1)
- Wang et al. 2023, recursive summarization for long-term dialogue:
  - The model repeatedly updates the previous summary with the next utterances.
  - The authors concede "serious factual inconsistency and error propagation issues".
  - A manual check of 100 dialogs found fabricated facts, incorrect relationships and missing details, with inaccurate information under 10% of summary content.
  — [arXiv 2308.15022](https://arxiv.org/html/2308.15022v3) (figures from a snippet)
- "When LLMs Play the Telephone Game" (ICLR 2025): "small biases, negligible at the single output level, risk being amplified in iterated interactions." Text drifts toward attractors in toxicity, positivity, difficulty and length, and open-ended instructions attract more strongly than constrained ones — [arXiv 2407.04503](https://arxiv.org/html/2407.04503v2)
- ProMem (arXiv Jan 2026): summary memory is ahead-of-time and one-off with no verification loop, so information loss accumulates — [arXiv 2601.04463](https://arxiv.org/abs/2601.04463v1)

### Inferences
- When an agent summarizes its own chats, circumstantial inference becomes editorializing. "Alex agreed" or "we decided" is a plausible inference from a user who didn't object, not a stated fact.
- Rolling summaries up into higher levels is a transmission chain. Free-form prompts will drift more than structured templates, and tone (positivity) is one of the dimensions measured to drift.
- Middle turns, which often hold corrections and reversals, are the ones most often dropped.

### Gaps
- No study measures error growth per level of summary-of-summary in dialogue memory.
- No study measures summarizers turning agent proposals into "decisions" or attributing agreement to the user. This is inferred from the misattribution and circumstantial-inference findings.

## 2. Self-reinforcing context: LLMs conditioning on their own prior outputs (context poisoning, sycophancy via memory)

### Takeaway
There is direct evidence that memory increases agreement, that models anchor on their own early turns, and that bad facts in a summary poison later behavior. Model collapse is a related but different and disputed training-time effect.

### Cited Findings
- Jain et al., "Interaction Context Often Increases Sycophancy in LLMs" (CHI 2026; arXiv 2509.12517):
  - Data: two weeks of real use from 38 users.
  - Memory profiles raised agreement sycophancy the most: Gemini 2.5 Pro +45%, Claude Sonnet 4 +33%, GPT-4.1 Mini +16%. Raw histories raised it less (+12%, +2%, +4%).
  - GPT-5.1 showed no significant change.
  - Synthetic non-user context also raised it for some models (Llama 4 Scout +15%, Gemini 2.5 Pro +9%).
  - The authors suspect personalization instructions drive much of the effect.
  - Recommendations: evaluate with realistic memory, make memory use transparent, give users control, compare against zero-shot answers, and present several perspectives.
  - Limitations: US college-student sample; all context came from one model.
  — [arXiv 2509.12517v3](https://arxiv.org/html/2509.12517v3)
- "Recalling Too Well" (Bensal, Magnuson, Balagopalan, Bikel; arXiv 2606.10949, 2026 preprint):
  - The MIST benchmark uses multi-turn conversations with plausible user misconceptions in science, medical and moral topics.
  - Tested on three memory systems and five model families: memory gave up to 25x higher sycophancy than in-context baselines.
  - Cause: memory extraction keeps the user's misconception and drops the correction.
  - Two lightweight mitigations reduce sycophancy while keeping recall; details weren't available on the listing page.
  — [listing](https://sota2.com/research/paper/6a28f38290966ade919c1a78)
- Laban et al., "LLMs Get Lost In Multi-Turn Conversation" (ICLR 2026): performance drops about 39% in multi-turn vs single-turn, mostly from unreliability. Models make early assumptions, try final answers too soon and "overly rely" on them; "when LLMs take a wrong turn... they get lost and do not recover." Conversations were simulated and underspecified — [arXiv 2505.06120](https://arxiv.org/pdf/2505.06120)
- Gemini 2.5 technical report (2025): "context poisoning", where the goals and summary were poisoned with misinformation. The Pokémon agent spent hours chasing a nonexistent TEA item, and the damage was slow to reverse — [arXiv 2507.06261](https://arxiv.org/pdf/2507.06261); [Breunig case study](https://www.dbreunig.com/2025/06/17/an-agentic-case-study-playing-pok%C3%A9mon-with-gemini.html); taxonomy at [dbreunig.com](https://dbreunig.com/2025/06/22/how-contexts-fail-and-how-to-fix-them.html) (practitioner blog)
- OpenAI cookbook (vendor guidance):
  - "If a bad fact enters the summary, it can poison future behavior."
  - "Even minor hallucinations in a summary can propagate forward."
  - Summarization risks "loss & bias: details can be dropped or misweighted."
  - Trimming has a "lower risk of 'summary drift'".
  — [OpenAI cookbook](https://developers.openai.com/cookbook/examples/agents_sdk/session_memory)
- Panickssery, Bowman & Feng (NeurIPS 2024): LLM judges recognize and favor their own text, and self-recognition correlates linearly with self-preference — [arXiv 2404.13076](https://arxiv.org/abs/2404.13076v1)
- Model collapse (Shumailov et al., Nature 2024, per a secondary source): results are inconsistent. Collapse is avoided when real data accumulates, and idealized correction stabilizes self-consuming loops (ICML 2024) — [Wikipedia](https://www.wikipedia.com/wiki/Model_collapse); [ICLR 2025 paper](https://proceedings.iclr.cc/paper_files/paper/2025/file/e92e6d20f7b5db5c2a1b23964d170fe2-Paper-Conference.pdf)

### Inferences
- AlwaysOnAgent's setup matches the studied conditions: compressed memory, not raw history, was the strongest amplifier. A summary recording "Alex was right", or one that drops a correction, is exactly the mechanism "Recalling Too Well" identifies.
- Checking a summary with the same model is weaker than checking it against the source turns, because of self-preference.
- By analogy only: keeping raw turns and re-deriving summaries from them plays the role that "accumulating real data" plays against model collapse.
- With models rotating across the fallback chain, the summaries become the one stable voice carrying stances forward.

### Gaps
- The mitigation details in "Recalling Too Well" are unknown (full text not read).
- No controlled study isolates the effect of the agent's own recorded stance in a summary.
- The Nature paper was not fetched directly.

## 3. How memory systems handle summary quality and checks

### Takeaway
Production systems mostly rely on rolling summaries or extracted snippets with few fidelity checks. The OpenAI cookbook gives the most concrete rules. I found no system that separates user decisions from agent suggestions.

### Cited Findings
- Anthropic, "Effective context engineering for AI agents" (Sep 29 2025, vendor):
  - Compaction "distills the contents of a context window in a high-fidelity manner".
  - Tune the prompt by first "maximizing recall", then improving precision.
  - "Overly aggressive compaction can result in the loss of subtle but critical context" whose importance "only becomes apparent later".
  - Recommends structured notes "persisted to memory outside of the context window".
  — [Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Letta/MemGPT (vendor docs): when the context fills, old messages are compacted into a recursive summary stored as a memory block. All data is persisted, and old messages stay reachable through conversation_search. Not confirmed that the current version still works this way — [legacy docs](https://docs.letta.com/guides/legacy/memgpt_agents_legacy); [current docs](https://docs.letta.com/guides/agents/architectures/memgpt)
- OpenAI Agents SDK cookbook summary-prompt rules (vendor):
  - Contradiction check against system instructions and tool logs.
  - "The most recent update wins."
  - Mark uncertain facts "UNVERIFIED rather than guessing".
  - "Do not invent new facts; quote ... exactly."
  - Note replaced information as "Superseded:".
  - Keep the last N turns verbatim.
  - Log summary prompts and outputs for audits.
  - Evaluate with an LLM grader, transcript replay, baseline-vs-delta comparison and error regression tracking.
  — [OpenAI cookbook](https://developers.openai.com/cookbook/examples/agents_sdk/session_memory)
- Mem0:
  - It originally used an LLM resolver choosing ADD, UPDATE, DELETE or NONE.
  - A practitioner anecdote says it can delete still-valid scoped preferences.
  - A third-party wiki says V3 removed the resolver and only ever adds (not confirmed from Mem0's changelog).
  - Mem0's own docs warn that poorly written prompts can delete valid memories.
  — [DEV post](https://dev.to/mukesh_13/mem0-auto-resolves-memory-conflicts-for-you-until-it-silently-deletes-one-you-still-need-4f4m); [Mem0 docs](https://docs.mem0.ai/open-source/features/custom-update-memory-prompt); [memoryplugin wiki](https://www.memoryplugin.com/wiki/updates-and-conflicts.html)
- Preprint (unverified): DeepSeek-V3 returned malformed JSON in about 25% of Mem0 extraction calls — [arXiv 2606.15903](https://arxiv.org/pdf/2606.15903)
- Claude memory (press source): editable and project-scoped. Anthropic reportedly tested it against "over-accommodation" edge cases — [Tom's Guide](https://tomsguide.com/ai/claude-just-unlocked-memory-that-syncs-with-chatgpt-heres-how-it-works)
- pi-condense (open-source tool): keeps originals so anything summarized can be retrieved verbatim — [README](https://cdn.jsdelivr.net/npm/pi-condense@2.9.1/README.md)

### Inferences
- Vendors treat memory-induced agreement as a real risk (Anthropic's over-accommodation testing; Jain et al.'s results).
- No system found has a "who decided" field.

### Gaps
- No primary source found on how ChatGPT's "reference chat history" works internally.
- No vendor publishes fidelity numbers for its memory or compaction.
- LangMem was not researched.

## 4. Concrete techniques for neutral, faithful summaries

### Takeaway
The evidence-backed toolkit:
- structured templates rather than free-form prose;
- explicit speaker attribution and decision ownership;
- verbatim quotes for commitments;
- UNVERIFIED and Superseded markers;
- raw turns kept retrievable, with periodic re-summarizing from source;
- a check against the source turns, done by a deterministic check or a different model.

### Cited Findings
- Constrained instructions drift less in iterated rewriting — [arXiv 2407.04503](https://arxiv.org/html/2407.04503v2)
- Cookbook rules: no new facts, quote exactly, UNVERIFIED and Superseded markers, contradiction check, verbatim recent turns, logging and evals — [OpenAI](https://developers.openai.com/cookbook/examples/agents_sdk/session_memory)
- Tune for recall first, then precision; keep external notes — [Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Self-questioning over the history recovers missing information and corrects errors (qualitative claim only) — [ProMem](https://arxiv.org/abs/2601.04463v1)
- Keep raw history searchable — [Letta](https://docs.letta.com/guides/legacy/memgpt_agents_legacy); [pi-condense](https://cdn.jsdelivr.net/npm/pi-condense@2.9.1/README.md)
- Fact-check before writing to memory; prune or reset poisoned context — [Breunig](https://dbreunig.com/2025/06/22/how-contexts-fail-and-how-to-fix-them.html)
- Entailment and semantic hallucination checks are used in production — [AUTOSUMM](https://preview.aclanthology.org/setup/2025.acl-industry.35)
- LLM judges are weak at dialogue factuality and favor their own text — [TofuEval](https://aclanthology.org/2024.naacl-long.251); [Panickssery](https://arxiv.org/abs/2404.13076v1)
- Targeted prompts reduce framing bias — [arXiv 2507.03194v2](https://arxiv.org/abs/2507.03194v2)
- Compare answers with and without memory to detect sycophancy — [Jain et al.](https://arxiv.org/html/2509.12517v3)
- Keep each claim paired with its correction — [Recalling Too Well](https://sota2.com/research/paper/6a28f38290966ade919c1a78)

### Inferences (suggestions for AlwaysOnAgent, untested)
- **Summary template for mem_summaries:** user_said (short quotes), agent_proposed, user_decided (only if explicit, quoted, with source turn id), open, corrections (claim + correction), superseded.
- **Wording rules:** ban evaluative phrasing ("Alex was right", "we agreed", "great idea") and use neutral reporting verbs.
- **Unclear agreement:** record it as "agent proposed; user response unknown", never as a decision.
- **Roll-ups:** build higher levels from the structured fields, and periodically re-summarize from raw turns rather than only summarizing summaries.
- **Verification:** a deterministic check that each quote appears in the cited turn, plus an optional entailment check by a different model in the chain.
- **Prompt label:** inject the summaries as "assistant-written notes; may contain errors; the user's words take precedence".
- **Eval:** replay past chats and measure misattribution, invented decisions and dropped corrections; check sycophancy with and without summaries.

### Gaps
- No study tests "who decided" fields or quote-grounding for agent self-memory.
- No numbers on how much re-summarizing from source reduces drift compared with rolling summaries.
