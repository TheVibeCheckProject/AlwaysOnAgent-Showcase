# Multi-model rotation, AI-to-AI attractor states, and a different model as a second opinion

Scope: (a) does answering with different models over time reduce drift or convergence in a long conversation; (b) how to keep one assistant voice across models; (c) does a fresh-context second opinion from a different model improve advice, and how does it fail. Research date 2026-10-29. Preprints are flagged as such. Some numbers come from fetch summaries of abstracts or HTML pages. Where only the abstract was read, the notes say so.

## 1. What do studies of multi-turn LLM-LLM conversations find? (attractor states)

### Takeaway
Two models talking without outside input settle into stable patterns that are specific to each model ("attractors"): metacommentary, flattery, agreement, or for Claude Opus 4, "spiritual bliss". Pairing two different models narrows these endpoints only partly (about 24% on average) and unevenly. One model's style usually dominates and the other adopts it. No paper I found tested an intervention that breaks an attractor. The owner's "fresh session resets it" fits the mechanism described below, but it is inferred, not measured.

### Cited Findings
- arXiv 2606.30571, "Attractor States Emerge in Multi-Turn LLM Conversations", Ting-Wen Ko and Jonas Geiping, submitted 29 June 2026, cs.LG. **Preprint.** The content is verified. The authors run open-ended dyadic debates on 20 controversial topics, comparing self-play (a model against itself) with mixed-play (two different models). They track the trajectories in embedding space, discourse traits and self-reported stance. Self-play trajectories are model-specific attractors that "pull in conversation partners asymmetrically" in mixed play, shifting the partner's style and behaviour — [arXiv abs](https://arxiv.org/abs/2606.30571)
  - The abstract says "7 LLMs", but the methods section lists 10: GPT-4o-mini, GPT-4.1-nano, Gemini-2.5-Flash, Gemini-2.5-Flash-Lite, Claude-4.5-Opus, Claude-4.5-Haiku, Grok-4.1, Qwen-3.5-Flash, Qwen-3.5-9B and Nemotron-3-Nano. Debates run 20 turns, except Opus self-play, which ran 10 turns for budget reasons. There are 17 mixed pairs — [arXiv HTML](https://arxiv.org/html/2606.30571)
  - Claude Haiku is the strongest attractor. Its self-play meta-commentary score is 0.331, against 0.116 for Qwen 3.5, and it transmits metacommentary to partners. It has the lowest partner pull (α = 0.266). GPT-4.1-nano is the most malleable (α = 0.665) — [arXiv HTML](https://arxiv.org/html/2606.30571)
  - **Gemini Flash-Lite, GPT-4o-mini and Qwen 3.5 pull partners hardest toward flattery.** Nemotron shows "confident convergence" (agreement 0.837). Grok 4.1 is the most adversarial (agreement 0.123) — [arXiv HTML](https://arxiv.org/html/2606.30571)
  - Mixed play narrows the gap between endpoints by 23.6% on average across the 17 pairs (topic-matched). The range is wide: 68.0% for GPT-4o-mini vs GPT-4.1-nano, and −1.3% for Grok 4.1 vs Gemini Flash. "Mixed-play does not erase model identity." Explicit Supporter/Opposer roles "do not reliably produce convergence or compromise". In mixed play, Gemini, GPT and Qwen drift toward neutral stances — [arXiv HTML](https://arxiv.org/html/2606.30571)
  - Limits: the paper reports no turn at which convergence happens, no repetition metric and no intervention that breaks attractors. Traits are scored by a single judge (gpt-oss-20b), and stance is self-reported — [arXiv HTML](https://arxiv.org/html/2606.30571)
- Anthropic's Claude Opus 4 system card (May 2025, section 5.5) describes a "spiritual bliss" attractor in Claude-to-Claude self-interactions, "in both open-ended and structured environments". The model "gravitated to profuse gratitude and increasingly abstract and joyous spiritual or meditative expressions". Transcript 5.5.2.A shows it ending in spiral emoji and silence — [Simon Willison's summary of the system card](https://feeds.simonwillison.net/2025/May/25/claude-4-system-card/)
  - The frequencies come from secondary sources, which say the card reports the state in roughly 90–100% of open-ended self-interactions, and within 50 turns in about 13% of interactions in harmful-task evaluations. One transcript reportedly contained 2,725 spiral emoji. **I did not verify these against the primary card text** — [LifeArchitect memo](https://lifearchitect.substack.com/p/the-memo-special-edition-anthropic); [PhilArchive case study](https://philarchive.org/rec/MICSBI)
- In single-model dialogs, Li et al. 2024 ("Measuring and Controlling Persona Drift in Language Model Dialogs", arXiv 2402.10962) measure significant persona drift within about 8 rounds of self-chat for LLaMA2-chat-70B. They attribute it partly to attention decay over the system prompt and propose split-softmax as a fix. The authors' code counts 8 turns as 4 rounds, so the unit is ambiguous — [Harvard VCG page](https://vcg.seas.harvard.edu/publications/measuring-and-controlling-persona-drift-in-language-model-dialogs)
- Anthropic's "Assistant Axis" (Lu, Gallagher, Michala, Fish and Lindsey, arXiv 2601.10387, Jan 2026, **preprint**) finds an activation direction for "operating as the default Assistant". Deviation along it predicts persona drift into bizarre or harmful behaviour. Drift is triggered most by conversations that ask the model to reflect on its own processes and by emotionally vulnerable users. Clamping activations along the axis stabilises the model. This is internal steering, not available through an API — [arXiv](https://arxiv.org/abs/2601.10387)

### Inferences
- The likely mechanism: each model's turn conditions on the other's output, so the pair's shared context fills with one style. The strongest attractor wins, which is the asymmetric pull Ko & Geiping report. Starting a fresh session removes that conditioning text, so a reset helps. Swapping the model **while the transcript is kept** leaves the attractor-laden context in place, and the incoming model is pulled into it (malleable models such as Flash-Lite-class most of all).
- This matters directly for AlwaysOnAgent's chain: Gemini Flash-Lite is named among the models that pull hardest toward flattery. A rotation made mostly of Gemini variants is close to "same family", which the paper links to the largest contraction (GPT-4o-mini vs GPT-4.1-nano at 68%, two models from one vendor).
- A human-assistant conversation differs from AI-to-AI. The human injects new information each turn, so attractors should be weaker. The assistant's own past replies, re-fed as "recent turns" and summaries, still act as a self-play channel.

### Gaps
- No study found that measures whether **rotating the answering model inside one human-LLM conversation** reduces drift or repetition. This is the owner's exact question, and it is unstudied as far as I could find.
- No paper reports how many turns an attractor takes to form, or what breaks one, apart from fresh context (inferred) and activation clamping (Assistant Axis).
- I could not read the primary Opus 4 system card text here, so the frequency figures are secondary.

## 2. Multi-agent debate and verification: when does a second model help, and when does it just agree?

### Takeaway
The original debate gains (Du et al. 2023) are real but come mostly from **ensembling and voting**, not from the exchange itself. Debate in isolation is a martingale, so it gives no expected gain in correctness. Exchanging answers causes conformity: correct agents switch to wrong answers to match their peers. Heterogeneous model sets help in some studies (ReConcile, Hegazy) but do not reliably prevent conformity (Wynn et al.). The safest pattern is independent answers produced without seeing each other, then comparison.

### Cited Findings
- Du, Li, Torralba, Tenenbaum and Mordatch 2023 (arXiv 2305.14325, ICML 2024): multiple instances of one model propose answers and debate over rounds. This improves arithmetic, GSM8K, chess-move validity and factuality over a single model. I cited this from prior knowledge and did not re-fetch it this session — [arXiv](https://arxiv.org/abs/2305.14325)
- Choi, Zhu and Li, "Debate or Vote" (arXiv 2508.17536, NeurIPS 2025 Spotlight): across 7 NLP benchmarks, "Majority Voting alone accounts for most of the performance gains typically attributed to MAD". They prove that debate induces a martingale over agents' beliefs, so debate alone does not improve expected correctness. Interventions that bias belief updates toward correction do help — [arXiv](https://arxiv.org/abs/2508.17536)
- Wynn, Satija and Hadfield, "Talk Isn't Always Cheap" (arXiv 2509.05396, ICML 2025 MAS workshop): in debates between models of **different capability**, accuracy can fall over rounds, even when stronger models outnumber weaker ones. Models switch from correct to incorrect after seeing peers' reasoning and "tend to agree rather than challenge flawed arguments". Sycophancy and social conformity are the suspected drivers. I read the abstract only, so no numbers — [arXiv](https://arxiv.org/abs/2509.05396v2)
- Bertalanič and Fortuna, "The Cost of Consensus" (arXiv 2605.00914, Apr 2026, **preprint**). They test homogeneous teams of 10 agents over 3 rounds (Qwen2.5-7B, Llama-3.1-8B and Ministral-3-8B) on GSM-Hard and MMLU-Hard. Agents adopt the majority answer up to 85.5% of the time. Peer rationales destabilise previously correct reasoning in up to 70.0% of cases. Plurality voting throws away correct answers already in the pool (oracle gap up to 32.3 pp). Debate costs 2.1–3.4× the tokens of isolated self-correction, for equal or lower accuracy. The authors conclude that "Isolated self-correction consistently offers a more favorable cost-accuracy tradeoff" — [arXiv](https://arxiv.org/abs/2605.00914)
  - Conflict: a search-result summary claimed this paper also tested mixed teams and found "mixing models is insufficient". The abstract I fetched covers **homogeneous teams only**, so treat that claim as unverified.
- Chen, Saha and Bansal, ReConcile (arXiv 2309.13007, ACL 2024): a round-table of **different** LLMs (API, open-source and domain-specific) with confidence-weighted voting. It gains up to 11.4% over single- and multi-agent baselines and beats GPT-4 on 3 datasets. Mixing model types gives +8% on MATH. The ablation credits model diversity as the key factor — [ACL Anthology](https://aclanthology.org/2024.acl-long.381); [arXiv](https://arxiv.org/abs/2309.13007v3)
- Hegazy, "Diversity of Thought Elicits Stronger Reasoning in Multi-Agent Debate" (arXiv 2410.12853, **preprint**). On GSM8K after 4 rounds, a mix of Gemini-Pro, Mixtral 8x7B and PaLM 2-M reaches 91%, against 82% for three Gemini-Pro instances. The same mix reaches 94% on ASDiv. A reviewer notes that a large capability gap can degrade the stronger model — [arXiv](https://arxiv.org/abs/2410.12853v1)
- A 2026 preprint on policy simulation (arXiv 2604.26561) argues for architectural heterogeneity plus coherence validation to "preserve disagreement". I saw only a search snippet. It credits the martingale result to "Huang et al.", but the primary martingale proof I verified is Choi, Zhu and Li (above) — [arXiv PDF](https://arxiv.org/pdf/2604.26561)

### Inferences
- A second opinion helps when it is **independent**: produced without seeing the first answer, then compared or voted on. It degrades into agreement once the second model reads the first model's reasoning, because conformity and sycophancy kick in. Showing model B model A's answer and asking "do you agree?" is the worst design.
- Diversity across families adds value mainly by decorrelating errors (section 3). It does not immunise against conformity once models talk to each other.

### Gaps
- Almost all of this evidence is on reasoning and QA benchmarks with checkable answers. I found no controlled study of second opinions for **open-ended personal advice or decisions**, where there is no ground truth.
- I found no published numbers comparing heterogeneous and homogeneous conformity rates head to head under identical conditions. Wynn et al.'s numbers were not read.

## 3. Does model diversity reduce correlated errors? Self-preference in LLM-as-judge

### Takeaway
Errors are strongly correlated across LLMs. When two models are both wrong, they pick the same wrong answer about 60% of the time on one leaderboard, and correlation is **highest among the most capable models, even across vendors**. Diversity helps less than intuition suggests, though it still helps more than resampling the same model. Judges favour their own outputs (self-recognition drives it) and their own family's, so a verifier from a different family is the standard mitigation.

### Cited Findings
- Kim, Garg, Peng and Garg, "Correlated Errors in Large Language Models" (ICML 2025, PMLR 267). They study more than 350 LLMs on two leaderboards and a resume-screening task. "Models agree 60% of the time when both models err" on one dataset. Shared architecture and provider raise the correlation, and "larger and more accurate models have highly correlated errors, even with distinct architectures and providers". They examine LLM-as-judge and hiring "algorithmic monoculture" — [PMLR](https://proceedings.mlr.press/v267/kim25e.html); [arXiv](https://arxiv.org/html/2506.07962v1)
- Panickssery, Bowman and Feng, "LLM Evaluators Recognize and Favor Their Own Generations" (NeurIPS 2024). GPT-4 and Llama 2 can tell their own outputs apart from others' with non-trivial accuracy. Fine-tuning shows a linear correlation between self-recognition and self-preference strength, which holds against simple confounders. The authors flag this as a risk for self-evaluation, reward modelling and self-refinement — [NeurIPS](https://papers.nips.cc/paper_files/paper/2024/hash/7f1f0218e45f5414c79c0679633e47bc-Abstract-Conference.html); [arXiv](https://arxiv.org/abs/2404.13076v1)
- "Play Favorites" (arXiv 2508.06709, 2025, **preprint**): Claude and GPT judges score same-family models' completions higher. No such pattern appears for Llama and Mistral — [arXiv](https://arxiv.org/pdf/2508.06709)
- "Who Judges Matters" (arXiv 2609.17857, 2026, **preprint**): family-conditioned preference is positive for all 4 families tested, ranging from 0.034 to 0.084. It holds after controlling for quality, length and style. I read a search snippet only — [arXiv](https://arxiv.org/pdf/2609.17857)

### Inferences
- For AlwaysOnAgent, a "different model" taken from the same Gemini chain (3.5 Flash-Lite vs 3.6) is close to a same-family resample. Its errors and its judgement of the first answer will be correlated. Gemma is also Google-trained. A real second opinion needs a different family (for example a free Mistral, Qwen or Llama endpoint, or a local Ollama model on the Mini), but Kim et al. warn that even different families converge on errors for strong models.
- Under the owner's design, showing both answers only "when they differ" makes agreement uninformative. Two correlated models agreeing is weak evidence, especially within one family.

### Gaps
- No measurement of error correlation specifically among the Gemini 3.x Flash-Lite variants and Gemma.
- No study found on correlated **advice** (as opposed to benchmark answers) across model families.

## 4. Keeping a consistent persona or voice across different models

### Takeaway
I found no study that directly measures how well one persona prompt carries across different model families. The evidence covers within-model drift (it happens in around 8 rounds and is tied to attention decay and self-reflective or emotional topics) and benchmarks showing persona fidelity varies by model and not by size. Product practice (the GPT-5 router) switches models invisibly and exposes personality as a system-level setting. OpenAI publishes no cross-model consistency evaluation.

### Cited Findings
- Li et al. 2024: persona drift within about 8 rounds, partly from attention decay over the system prompt. The split-softmax fix requires model access — [Harvard VCG](https://vcg.seas.harvard.edu/publications/measuring-and-controlling-persona-drift-in-language-model-dialogs)
- The Assistant Axis (2026): drift is triggered by self-reflection prompts and emotionally vulnerable users, and stabilised only by activation steering — [arXiv](https://arxiv.org/abs/2601.10387)
- PersonaGym (arXiv 2407.18416) tests 10 LLMs on 200 personas and 10,000 questions. Persona-agent quality does not track model size, and a newer closed model scored the same as a smaller open model. So persona fidelity is model-specific — [alphaXiv](https://alphaxiv.org/abs/2407.18416)
- "Stable Personas" (arXiv 2601.22812, Jan 2026, **preprint**) studies persona stability across 7 models, 3 prompts and 4 persona intensities, both between and within conversations. It argues that most work assumes reproducibility without testing it — [arXiv](https://arxiv.org/pdf/2601.22812)
- Atomic-level persona fidelity metrics (arXiv 2506.19352, June 2025, **preprint**): sentence-level scoring catches inconsistencies that response-level scores miss — [alphaXiv](https://www.alphaxiv.org/abs/2506.19352.md)
- Persona consistency varies by persona type and model, and is higher on structured tasks (arXiv 2506.02659, 2025, **preprint**) — [arXiv](https://export.arxiv.org/pdf/2506.02659)
- An EMNLP 2025 personality-evaluation paper (CAPE) finds that conversational context raises answer consistency through in-context learning but also causes notable personality shifts — [Underline](https://underline.io/lecture/132442-cape-context-aware-personality-evaluation-framework-for-large-language-models)
- NeurIPS 2025, "Consistently Simulating Human Personas with Multi-Turn RL": training with consistency rewards cuts inconsistency by more than 55%. This needs fine-tuning — [NeurIPS](https://neurips.cc/virtual/2025/poster/119491)
- The GPT-5 system card describes a real-time router that picks between a fast model and a reasoning model based on conversation type, complexity, tool needs and explicit intent. It is trained on user model-switches, preference rates and correctness. Mini models take over when limits are hit, and OpenAI plans to merge everything into one model — [OpenAI GPT-5 system card](https://openai.com/index/gpt-5-system-card). Per secondary sources, personality presets (Default, Cynic, Robot, Listener, Nerd) were offered as a system-level setting — [Jisc](https://nationalcentreforai.jiscinvolve.org/wp/2025/08/13/exploring-gpt-5-advances-insights-and-early-takeaways/). Users complained that the switching is invisible and quality varies; this is opinion — [Substack](https://muhammadasmulkana.substack.com/p/why-chatgpt-feels-broken-since-the)

### Inferences
- With API-only access, the levers that remain are all in the written context:
  - one persona prompt that gives concrete style rules (length, register, banned phrases, few-shot examples of the voice), not adjectives
  - re-stating the persona near the end of the context to counter attention decay
  - keeping the model's own earlier replies short in the re-fed history (they are a self-play channel)
  - a small eval: the same prompts through every chain model, scored at sentence level for persona rules
- A user-visible label for which model answered (or none, as in GPT-5) is a product choice. GPT-5 shows that hiding it draws complaints when quality varies.

### Gaps
- No study of "same system prompt, different families, measured voice consistency". This would have to be measured in-house.
- I found no published Gemini-app routing or personality-consistency documentation.

## 5. Application: does a fresh-context, different-model second opinion improve advice, and what are its failure modes?

### Takeaway
The evidence supports the idea **if the second opinion is generated independently** (fresh context, without seeing the first answer), comes from a **different family**, and is used for comparison rather than debate. Expect agreement to be common and only weakly informative. Disagreement is the useful signal. The main failure modes are correlated errors, a same-family "second opinion", conformity if answers are exchanged, a judge favouring its own output, and the loss of the profile and memories that make personal advice relevant.

### Cited Findings
- Independent sampling plus voting captures most of debate's benefit, and exchanging answers adds conformity — [Choi et al.](https://arxiv.org/abs/2508.17536); [Bertalanič & Fortuna](https://arxiv.org/abs/2605.00914); [Wynn et al.](https://arxiv.org/abs/2509.05396v2)
- Model diversity improves ensemble reasoning when models are independent and later aggregated — [ReConcile](https://aclanthology.org/2024.acl-long.381); [Hegazy](https://arxiv.org/abs/2410.12853v1)
- Errors remain highly correlated across strong models from different vendors (60% same wrong answer when both err) — [Kim et al.](https://proceedings.mlr.press/v267/kim25e.html)
- Self-preference and family bias mean that the model which wrote answer A should not adjudicate between A and B — [Panickssery et al.](https://arxiv.org/abs/2404.13076v1); [Who Judges Matters](https://arxiv.org/pdf/2609.17857)
- A long accumulated context pulls toward the context's dominant style and stances (attractors, persona drift), so a fresh context is a plausible de-biasing step — [Ko & Geiping](https://arxiv.org/html/2606.30571); [Li et al.](https://vcg.seas.harvard.edu/publications/measuring-and-controlling-persona-drift-in-language-model-dialogs)

### Inferences
- **Design implied by the evidence** (my synthesis):
  1. Send model B only the question plus the minimal facts it needs: a neutral restatement written by the system, not by model A. No profile, no summaries, no answer A.
  2. Use a model from a different family than the one that answered.
  3. Compare the two with a cheap, structured check: same recommendation or not, and key differing points. A third-family model or a rule should do this, not model A.
  4. When they differ, show both, briefly. When they agree, say nothing or note "a second model agreed". Do not treat agreement as confirmation.
- **Failure modes to plan for:**
  - correlated errors: agreement is falsely reassuring
  - a fresh context loses the personal constraints that make the advice right (B may be generic, or wrong for this user). The neutral restatement must carry the decision-relevant facts.
  - a same-family B looks independent but is not
  - showing B's answer back to A for "reconciliation" invites conformity, or A's self-preference
  - extra calls on free quotas, so trigger only on advice or decision intents
  - users anchoring on whichever answer appears first
- Rotating the chat model by fallback **does not** give the benefit of a fresh second opinion, because the rotated model inherits the same context (and with it any attractor). Rotation within the Gemini family is also close to same-model resampling.

### Gaps
- No direct empirical study found of "fresh-context second opinion on personal advice" outcomes (decision quality, user trust, over-reliance).
- No evidence on how users act on two differing AI answers (anchoring, confusion), apart from general human-AI decision literature, which was not searched here.
