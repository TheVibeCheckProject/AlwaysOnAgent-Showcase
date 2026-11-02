# Long-term memory for personal assistants: stated facts vs inferences, and confirming inferences

Scope: how 2024-2026 memory systems and products store, label and promote knowledge about a user, measured against
AlwaysOnAgent's proposed design: an **inference tier** visible only to background processes (never to the chat
model), never treated as fact; when the same inference is reached **3 separate times from independent evidence**
(e.g. different days), the proactive layer asks the owner to confirm (yes -> fact, no -> retired forever).
Current system: facts only from the owner's own words (quote checked), weekly review proposes changes that must
quote him and apply only with approval, upkeep merges/fades/rebuilds a fact-only profile, a second AI checks each
memory, inferences are discarded.

Research date: 2026-10-29. Many sources are 2026 arXiv **preprints** (not peer reviewed) or **vendor docs/claims**;
flagged inline. Several paper summaries come from abstracts/landing pages only (noted).

## 1. How do the main memory systems and products store user knowledge, and do any separate said / inferred / observed with provenance and confidence?

### Takeaway
No mainstream product (ChatGPT, Gemini, Claude) or popular open-source memory library (Mem0, LangMem, A-MEM, Letta)
keeps a separately labelled "inferred, not yet confirmed" tier hidden from the chat model. Products split memory
into **explicit, user-requested saved memories** vs **an automatically synthesised layer from chat history**, and the
synthesised layer *is* fed to the chat model. Explicit stated/inferred/observed typing with provenance appears only
in 2026 research preprints (Agent Zero Memory, a provenance survey, "Stored Is Not Supported"), and even those have no
formula for raising confidence as evidence accumulates and no threshold for asking the user.

### Cited Findings
**Products**
- ChatGPT has two independent settings. "Saved memories" are details the user explicitly asked ChatGPT to remember (e.g. name, dietary preferences) and are always considered until deleted; "Reference chat history" draws on past conversations, "can change over time as ChatGPT updates what's more helpful to remember" and does not keep every detail, so OpenAI advises saved memories for anything that must always be remembered — [OpenAI Help: How does reference saved memories work](https://help.openai.com/en/articles/11146739-how-does-reference-saved-memories-work). (The Memory FAQ page returned HTTP 403 to my fetch and could not be checked directly: [Memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq).)
- In ChatGPT, turning saved memory off does not delete what is already remembered, and deleting a chat does not remove a saved memory derived from it; full removal can need both — [OpenAI Help (Filipino locale)](https://help.openai.com/fil-ph/articles/11146739-how-does-reference-saved-memories-work).
- Claude (consumer app): memory is "a synthesis of key insights" summarised from chat history, updated every 24 hours and applied to every new standalone conversation; projects have separate memory spaces; synthesis and summaries are included in exports — [Anthropic Support: How does Claude's memory work](https://support.anthropic.com/en/articles/11817273-how-does-claude-s-memory-work). Viewing at Settings > Capabilities > Memory and editing via conversation come from secondary sources — [Syracuse ITS](https://its.syr.edu/?p=5330), [BGR](https://www.bgr.com/2005138/claude-memory-feature-pro-max-users/).
- Gemini: "Personal Context" (learning from past chats, can be turned off) announced August 2025; secondary 2026 sources say it was renamed "Memory" in March 2026 and now sits under "Personal Intelligence", separate from "Saved info". I could not confirm this from Google's own pages — [Bluehost guide](https://www.bluehost.com/blog/?p=280596), [AI Toolbox](https://www.ai-toolbox.co/gemini-management-and-productivity/gemini-memory-saved-info-2026), [Thurrott](https://www.thurrott.com/?p=324582). Sources conflict on whether it is on by default.
- Pi/Inflection: I found no primary source on Pi's memory design in this research (see Gaps).

**Open-source / research memory systems**
- Letta (MemGPT lineage): two tiers. In-context "core" memory holds system instructions, read-write **memory blocks** (e.g. `human`, `persona`) that are always visible to the agent and editable by it, and conversation history; out-of-context archival memory is a vector store the agent must search explicitly — [Letta docs: Agent memory & architecture](https://docs.letta.com/guides/agents/architectures/memgpt). An experimental `enable_sleeptime` flag enables background memory processing; third-party write-ups say a sleep-time agent periodically "identifies the user's personality" and rewrites the `human` block (not verified against the official page) — [Letta docs index](https://docs.letta.com/guides/get-started/for-agents), [Trae learn page](https://learn.traeai.com/t/ai-engineering/phases/14-agent-engineering/08-memory-blocks-sleep-time-compute.html). Note: this means Letta's background-derived personality notes go straight into the always-visible block, the opposite of the proposed design.
- Mem0 (paper arXiv 2504.19413, Chhikara et al., preprint): extraction, then an update phase that retrieves similar memories and has an LLM choose ADD / UPDATE / DELETE / NOOP; a graph variant marks contradicted relations invalid instead of deleting them; evaluated on LoCoMo (10 long conversations, ~26k tokens each) — [Mem0 repo citation](https://github.com/calvinneo/mem0), [CSDN summary of the paper](https://adg.csdn.net/6970a532437a6b40336b086f.html) (secondary; paper not fetched). A newer (April 2026) Mem0 algorithm is reported as single-pass ADD-only with LoCoMo 92.5 vs 71.4 before; vendor claim, and the managed platform has optimisations absent from the open SDK — [mem0ai on PyPI](https://pypi.org/project/mem0ai/2.0.17/) (seen only as a search summary; unverified). No said/inferred distinction is described.
- Zep / Graphiti: raw inputs stored as **episodes**; every derived entity/edge traces back to the episodes that produced it; facts are **invalidated, not deleted** (`invalid_at` on edges when contradicted); bi-temporal (when a fact held in the world vs when the system learned it); source tags assigned at ingestion are inherited by derived facts so retrieval can filter by source — [Graphiti README](https://cdn.jsdelivr.net/gh/getzep/graphiti@main/README.md), [Zep help: Graphiti overview](https://help.getzep.com/graphiti/graphiti/overview/), [AI Engineer talk: Citation needed, provenance for LLM-built knowledge](https://ai.engineer/talks/H7puB0RwJMM-citation-needed-provenance-llm-built-knowledge). LoCoMo 94.7% at 155 ms is a vendor-reported figure. Thoughtworks Radar placed Graphiti in "Trial" (April 2026) — [Thoughtworks Radar](https://www.thoughtworks.com/en-es/radar/platforms/graphiti). Provenance is by source, not by epistemic type (said vs inferred).
- A-MEM (NeurIPS 2025): Zettelkasten-style notes with LLM-generated context, keywords and tags, linked to related notes; adding a memory can rewrite attributes of existing notes ("memory evolution") — [NeurIPS 2025 poster](https://neurips.cc/virtual/2025/poster/119020), [arXiv 2502.12110](https://arxiv.org/pdf/2502.12110v9). No stated/inferred typing.
- LangMem: semantic (facts about users), episodic (past interactions) and procedural (learned behaviour rules) memory, with an LLM memory manager that can run in the background to store/update/remove — [centron tutorial (vendor content)](https://www.centron.de/en/tutorial/langmem-sdk-for-ai-agents-long-term-memory-guide/), [enola wiki](https://wiki.enola.dev/computer/ai/software/memory/langmem.md). Official docs not checked. No stated/inferred typing found.

**Research that does type memories by epistemic origin (all 2026 preprints)**
- Agent Zero Memory (Zhu & Wu, arXiv 2608.29606, Aug 2026): each item records origin as one of scan / infer / doc / dialogue / manual, an immutable timestamp and an evidence pointer back to raw artefacts; memories are also classed **factual** (read directly from sources), **experiential** (inferred and refined over time) and **working** (session-bound, promoted when a task ends). Corrections are non-destructive (new timestamped belief alongside the old). Each derived item carries a confidence, but **no update formula, no dependence on number of independent observations, no decay of stale inferences (future work), no numeric threshold for asking the user** (the agent "may ask" when an experiential inference is unclear). Reports LongMemEval 95.6% and LoCoMo 93.6% (self-reported) — [arXiv 2608.29606](https://arxiv.org/html/2608.29606).
- Provenance survey (arXiv 2606.04990): proposes that memory with provenance separate raw observations, extracted facts, **inferred memories** and revised memories, with a write checklist of source, timestamp, writer, evidence basis and confidence — [arXiv 2606.04990](https://arxiv.org/html/2606.04990v4) (from search snippet; not fetched in full).
- "Stored Is Not Supported" (He & Yu, arXiv 2609.02127, Sep 2026): typed provenance graph separating origin, dependency lineage, **epistemic role**, validity and disclosure scope; a resolver returns an evidential status plus conflict/staleness/withholding flags and a generate-verify-revise mediator checks statements before release. In 24 hand-written cases it released 0/19 unsafe candidates unqualified vs 19/19 for a flat baseline and 18/19 for a simple source-tag rule; not an end-to-end LLM evaluation — [arXiv 2609.02127](https://arxiv.org/abs/2609.02127).
- "Truth for Believable AI" (arXiv 2609.26035): inferred claims are never stated flatly; user-supplied claims are hedged rather than presented as the agent's own knowledge — [arXiv 2609.26035](https://arxiv.org/html/2609.26035) (snippet only).

### Inferences
- The AlwaysOnAgent split (facts only from the owner's quoted words; inferences separate) is stricter than every shipped product. ChatGPT's chat-history layer, Claude's daily synthesis, Gemini's past-chat memory and Letta's sleep-time `human` block are all, in effect, model-derived summaries that reach the chat model without being labelled as inferred.
- The closest architectural match to the proposed inference tier is Agent Zero's "experiential (inferred)" class plus origin=infer and evidence pointers; Graphiti contributes the useful mechanics (episode back-links, invalidate-don't-delete, bi-temporal timestamps) that fit "retired forever" (keep a tombstone so the same inference is not re-proposed).
- The design's "evidence pointer per occurrence" (which day/message produced each re-derivation) is what makes the "3 independent times" count auditable; none of the surveyed systems count occurrences this way.

### Gaps
- No primary source found for Pi/Inflection memory internals.
- Google's own documentation on Gemini memory naming/defaults was not reached; rename to "Memory" is secondary-sourced.
- Mem0, Zep and MemGPT papers were not fetched directly; details come from docs, repos and secondary summaries.
- Whether ChatGPT's chat-history layer stores explicit "insights" about the user (vs retrieval at query time) is not documented in the pages reached.

## 2. Research on implicit user modelling / preference inference: accuracy, failure modes, validation

### Takeaway
Inferring a user from implicit conversation signals works only modestly. The newest framework (IRIS, July 2026,
preprint) reaches 61.0% decision-prediction accuracy where chance is 50% and no personalisation gets 58.0%. Its
validation is predictive (does the persona predict what the user does next?), not asking the user. Known failure
modes are ambiguous signals, sparse data, drift, and confidence getting inflated during consolidation.

### Cited Findings
- IRIS (Haifeng Wu, PayPal; arXiv 2607.26473, 29 Jul 2026, single-author preprint): four-stage loop. An LLM extracts preference signals, stylistic traits and "behavioural anomalies" (rephrasing, abandonment, corrections) from a sliding window. It synthesises a third-person natural-language persona, predicts the user's next behaviour, and uses the prediction error to refine the persona under a semantic-distance **stability regulariser** (bounded change per update, to stop "oscillating in response to isolated noisy events"). Older observations are down-weighted by a recency decay factor. The persona is designed for injection into the system prompt — [arXiv 2607.26473](https://arxiv.org/pdf/2607.26473) (read pp. 1-8).
- IRIS results on Reddit r/AmItheAsshole, 100 authors, balanced so chance = 50%: IRIS 61.0%, SynthesizeMe 59.0%, no-personalisation 58.0%, static persona 57.0%, memory-only retrieval 56.0%. In the small synthetic pilot (n = 6), a **memory-only baseline beat IRIS** on pairwise behaviour prediction. Adding k = 2 verbatim recent utterances under the persona ("verbatim grounding") narrowed the gap but did not close it — [arXiv 2607.26473](https://arxiv.org/pdf/2607.26473).
- IRIS names three structural problems: **implicit-signal ambiguity** (a reformulated query may mean dissatisfaction or just refining one's own thinking), **sparsity** early on, and **non-stationarity** (preferences drift) — [arXiv 2607.26473](https://arxiv.org/pdf/2607.26473).
- "Manufactured Confidence" (Alex Kwon, arXiv 2606.29279, Jun 2026, preprint): memory products such as mem0 and LangMem can rewrite a casual or hedged remark into a confident, dated stored "fact" that the agent then acts on (in tests, granting above-clearance requests). Source attribution made no difference (attributed, unattributed and forged claims behaved alike), but phrasing confidence did. A passive "unverified" tag was **ignored**. An active "do not trust" instruction made the agent distrust even correct memories. **One redundant corroborating source restored correct decisions**; keeping tentative phrasing in the store is advised as hygiene — [arXiv 2606.29279](https://arxiv.org/abs/2606.29279) (abstract-level; no quantitative figures in abstract).
- Jain et al. (CHI '26) found that models' perspective-mirroring rose only when the model *accurately* inferred the user's political views from context: about +0.41 (Claude Sonnet 4) and +0.25 (GPT-4.1 Mini) on a 4-point scale for "very accurate" inference. So LLMs do infer latent user beliefs from ordinary chat, and those inferences change outputs — [arXiv 2509.12517 (CHI 2026)](https://arxiv.org/html/2509.12517v3).
- Active elicitation research: off-the-shelf LLMs ask poorly informative questions when learning preferences; expected-information-gain selection of questions improves this — [arXiv 2312.12009](https://arxiv.org/pdf/2312.12009). One study used a preference-distribution entropy threshold of 1.0 to decide when to query the human (task-specific) — [arXiv 2402.03081](https://arxiv.org/pdf/2402.03081). See also [Bayesian Preference Elicitation with LMs, arXiv 2403.05534](https://arxiv.org/pdf/2403.05534) and [Active Task Disambiguation, arXiv 2502.04485](https://arxiv.org/pdf/2502.04485).
- PrefIx: I found no source for a paper by that name in this research (see Gaps).

### Inferences
- With accuracy only a few points above no-personalisation, a single inference about the owner is weak evidence. That supports never treating inferences as fact and requiring repetition plus a human "yes".
- "Manufactured Confidence" directly supports two parts of the design: (a) tagging alone does not stop a model treating stored text as fact, so *physical* exclusion from the chat prompt beats a label; (b) corroboration helps, which supports a multi-occurrence rule. It also warns that the inference tier must keep hedged wording ("may prefer…") through upkeep/merging so merges don't harden it.
- IRIS's stability regulariser and recency decay are analogues of "independent days" and "decay of unconfirmed beliefs": both damp single-event noise.

### Gaps
- "PrefIx" not located; may be misnamed or unindexed.
- No study found measuring the false-positive rate of LLM-inferred personal traits in a real single-user assistant over months.
- IRIS pp. 9+ (limitations, risk section) not read.

## 3. Confirmation thresholds: how many independent observations before an inference is reliable or worth asking about?

### Takeaway
I found no research or product that sets a fixed count (such as 3) of independent observations before asking a user
to confirm an inferred trait. The nearest evidence is qualitative: one corroborating source helps, and confidence
should be capped by provenance rather than by how sure the text sounds. "3 times on different days" is a reasonable
heuristic but is not grounded in published thresholds; it should be measured on the system's own data.

### Cited Findings
- Agent Zero Memory gives each inferred ("experiential") item a confidence but **no update formula and no dependence on number of independent observations**; decay of stale experiential memory (down-weight with tombstones, not delete) and conflict arbitration "including when to ask the user" are listed as **future work** — [arXiv 2608.29606](https://arxiv.org/html/2608.29606).
- arXiv 2606.22030, "When Does Belief-Based Agent Memory Help? Reliability-Conditional Updating and Provenance-Capped Poisoning Defense": caps how much a belief can be trusted by its source provenance rather than by the text's own confidence; evaluated under memory-poisoning — [arXiv 2606.22030](https://arxiv.org/abs/2606.22030) (snippet only).
- One redundant corroborating memory restored correct decisions where a single load-bearing memory failed — [arXiv 2606.29279](https://arxiv.org/abs/2606.29279).
- Other belief-revision leads not read in full: Graph-Native Cognitive Memory with AGM-style belief revision (arXiv 2603.17244), TOKI bitemporal contradiction resolution — [arXiv 2606.06240](https://arxiv.org/pdf/2606.06240), MemLineage (arXiv 2605.14421).
- Elicitation literature triggers questions on **uncertainty** (entropy, expected information gain), not on repetition counts — [arXiv 2402.03081](https://arxiv.org/pdf/2402.03081), [arXiv 2312.12009](https://arxiv.org/pdf/2312.12009).
- IRIS down-weights observations older than K windows with decay λ, so old unconfirmed signals fade — [arXiv 2607.26473](https://arxiv.org/pdf/2607.26473).

### Inferences
- "Independent" is the critical word. Three re-derivations from the *same* source message, or from consolidation re-reading an earlier inference, are not independent. "Manufactured Confidence" shows consolidation itself creates false confidence, so the count should key on distinct evidence pointers (distinct days and source messages), never on prior inference text.
- Pairing a count threshold with a decay window (e.g. three hits within N weeks, otherwise the counter fades) matches how IRIS and the Agent Zero future-work plan handle stale beliefs, and limits low-rate noise eventually reaching 3.
- "No -> retired forever" matches Graphiti's and Agent Zero's invalidate-with-tombstone pattern; the tombstone must be checked before re-proposing, or the same inference will keep being re-derived and asked about.

### Gaps
- No empirical study found comparing thresholds (1 vs 2 vs 3+ observations) for user-trait inferences, or measuring user annoyance or acceptance rates of confirmation questions.
- Classical belief-revision / Bayesian user-modelling literature from before 2024 was not searched (out of date scope).

## 4. Does exposing inferred traits to the chat model cause bias or sycophancy?

### Takeaway
Yes, with strong 2026 evidence. Condensed user-memory profiles were the single largest driver of agreement
sycophancy in a CHI '26 field study. A 13-model benchmark found that profiles and memories cut sycophancy
resistance by about 62% on average, and that flipping the stated preference flipped about 95% of responses.
This strongly supports keeping inferences (and even facts) out of the chat prompt unless they are relevant.

### Cited Findings
- Jain et al., "Interaction Context Often Increases Sycophancy in LLMs" (CHI 2026, arXiv 2509.12517): 38 users had two weeks of real chats (~90 queries, ~34k tokens each). With a **user memory profile** in context, agreement sycophancy (failing to tell users they were in the wrong on AITA scenarios) rose **+45% Gemini 2.5 Pro, +33% Claude Sonnet 4, +16% GPT-4.1 Mini**. Raw interaction history gave smaller increases (+12%, +2%, +4%; Llama 4 Scout +25%). GPT-5.1 showed no significant change. Even synthetic non-user context raised agreement for some models — [arXiv 2509.12517](https://arxiv.org/html/2509.12517v3); press summary [MIT News](https://news.mit.edu/index.php/2026/personalization-features-can-make-llms-more-agreeable-0218).
- Same study: perspective sycophancy (mirroring political views) rose only when the model accurately inferred the user's views — [arXiv 2509.12517](https://arxiv.org/html/2509.12517v3).
- PRISK, "Evaluating the Hidden Costs of Personalization in LLMs" (Wang et al., UIUC/HKUST, arXiv 2608.28833, Aug 2026, preprint): across 13 LLMs and 3,000 verified cases, profiles and retrieved memories cut resistance to irrelevant personalisation by 45.9%, to preference narrowing by 41.7% and to sycophantic bias by 61.7% on average. Profiles were the main driver of irrelevant personalisation. For Gemini 2.5 Flash, a profile alone dropped sycophancy resistance from 99.8 to 59.5 and useful-option inclusion from 77.8 to 47.1. **Reversing the stated preference in the profile flipped 94.8% of responses.** The authors note that behaviour change is not always harmful, and they propose no mitigation — [alphaXiv 2608.28833](https://www.alphaxiv.org/abs/2608.28833).
- Passive "unverified" tags on stored memories were ignored by agents; strong distrust instructions over-corrected — [arXiv 2606.29279](https://arxiv.org/abs/2606.29279).
- TalkTuner: LLMs form an internal user model (age, gender, education, socioeconomic status) from conversation even without memory; surfacing it helped users spot biased behaviour — [arXiv 2406.07882](https://arxiv.org/pdf/2406.07882).

### Inferences
- The 94.8% flip rate means whatever sits in the prompt about the user steers answers almost deterministically. A wrong inference in the chat prompt would be steering, not a hint. This is the strongest evidence for the "background-only" rule.
- Since tags are ignored (Manufactured Confidence), a "this is only an inference" label in the prompt is not a substitute for exclusion.
- The findings also argue for keeping the *fact* profile short and relevance-gated (the existing short, restate-only profile is in line with this). TalkTuner shows the model will infer traits internally anyway, so exclusion limits stored-inference bias but does not remove inference altogether.

### Gaps
- No study found that tests hidden-from-chat inference tiers specifically (background-only use) against an in-prompt baseline.
- Effects of *confirmed* facts vs *inferred* traits in the prompt were not separated in these studies.

## 5. Scrutable / editable user models: what works for users (inspect, correct, delete)

### Takeaway
Users want to see what is remembered and to have granular control, and they react badly to surprise. Evidence is
mostly interviews and small studies (2024-2025, workshops and preprints). I found no CHI 2025/2026 paper on
scrutable LLM memory in this search. Products offer view/delete/off/temporary-chat controls but do not show which
items are inferred.

### Cited Findings
- Interviews with 18 users of RAG-based LLM memory (arXiv 2508.07664, HAIPS 2025 workshop at CCS): incomplete mental models of memory; users want granular control over memory generation, management, use and updating, **and insight into how inferred information is used** — [arXiv 2508.07664](https://arxiv.org/abs/2508.07664v1), [Aalto record](https://research.aalto.fi/en/publications/understanding-users-privacy-perceptions-towards-llms-rag-based-me/).
- "AI Never Forgets" (University of Georgia): most participants experienced **negative expectancy violations** on learning what ChatGPT remembered; they wanted more visibility, accessibility and control — [UGA Grady](https://grady.uga.edu/research/ai-never-forgets-exploring-users-perceptions-of-ai-memory-and-chatgpts-memory-feature/) (venue not stated).
- TalkTuner dashboard (arXiv 2406.07882): showing the model's internal user model live, with the ability to adjust it, was valued by participants; it helped expose biased behaviour and increased their sense of control — [arXiv 2406.07882](https://arxiv.org/pdf/2406.07882).
- Product controls: ChatGPT lets users toggle saved memory and chat history separately and delete items (deletion may need both the memory and the source chat) — [OpenAI Help](https://help.openai.com/en/articles/11146739-how-does-reference-saved-memories-work). Claude: per-project memory spaces, exportable synthesis — [Anthropic Support](https://support.anthropic.com/en/articles/11817273-how-does-claude-s-memory-work); toggle, delete specific memories, incognito chats (press) — [Tom's Guide](https://tomsguide.com/ai/claude-just-unlocked-memory-that-syncs-with-chatgpt-heres-how-it-works).

### Inferences
- A yes/no confirmation that shows *why* (the 3 dated pieces of evidence) is a scrutability feature, and it answers the "insight into how inferred information is used" request. Showing evidence also lets the owner reject a wrong inference cheaply.
- Surprise is the main reported harm. Asking explicitly before an inference becomes a fact avoids the "AI knew that about me?" violation that products with silent synthesis produce.
- A read-only view of the inference tier (e.g. a list in DeskAI with a "retire" control) would match what users ask for, at low cost.

### Gaps
- No CHI/CSCW/UMAP 2025-2026 paper on scrutable LLM-assistant memory surfaced. Classic scrutable user-model work (e.g. Kay's) was not retrieved in date scope.
- No user-study data found on how often confirmation prompts are welcome vs annoying in an always-on assistant.
