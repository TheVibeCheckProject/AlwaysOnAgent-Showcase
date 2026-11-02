# Chat UX for a single, never-ending personal-assistant conversation (mobile, 2024-2026)

Researched 2026-10-29 for DeskAI (Jetpack Compose). Two primary pages (help.openai.com release notes and memory FAQ) returned HTTP 403, so some ChatGPT memory claims below come from search-result summaries of press and help pages and are flagged as such. Vendor claims are marked [vendor].

## Generative UI: how assistants render rich cards (media, task progress, approvals), mobile specifics

### Takeaway
The field has settled on one pattern: the agent sends structured data, or picks from a catalog of components the client owns, and the client draws them with its own native widgets. Arbitrary generated code is the exception. OpenAI's Apps SDK guidelines are the most concrete published rules for cards inside a chat on mobile:
- at most 2 actions per card
- no nested scrolling
- a card is never taller than the phone screen
- carousels hold 3-8 items
- anything bigger goes fullscreen, and an ongoing activity goes picture-in-picture

Google's A2UI applies the same "declarative components from a client catalog" idea across platforms.

### Cited Findings
**OpenAI Apps SDK (ChatGPT apps): official UI guidelines**
- There are three display modes. Inline is the default: every app starts inline, and inline surfaces render above the model's reply. Fullscreen is for "interactive maps, editing canvases, or detailed browsing". Picture-in-picture (PiP) is for an ongoing activity that stays visible while the chat continues, such as a live session, a game or a video. — [OpenAI Apps SDK UI guidelines](https://developers.openai.com/apps-sdk/concepts/ui-guidelines)
- Inline cards:
  - Up to two actions: "one primary CTA and one optional secondary CTA".
  - "No nested scrolling. Cards should auto fit their content and prevent internal scrolling."
  - A card can grow "up to the height of the mobile display area"; beyond that, use fullscreen.
  - No multiple views, tabs or drill-ins inside a card.
  - Edits made in a card are persisted.

  — [OpenAI Apps SDK UI guidelines](https://developers.openai.com/apps-sdk/concepts/ui-guidelines)
- Inline carousels: 3-8 items. Each item needs an image, should have a title, and gets one optional CTA. The page contradicts itself on metadata length: one section says no more than 2 lines, another says 3 lines max. — [OpenAI Apps SDK UI guidelines](https://developers.openai.com/apps-sdk/concepts/ui-guidelines)
- PiP behaviour:
  - It stays pinned to the top of the display while the user scrolls, until dismissed or the session ends.
  - When the session ends it returns to an inline position and scrolls away.
  - Its state should update from what the user types in the composer.
  - Its controls should stay minimal.

  — [OpenAI Apps SDK UI guidelines](https://developers.openai.com/apps-sdk/concepts/ui-guidelines)
- Composer behaviour:
  - The composer stays overlaid in fullscreen.
  - While a response streams, the input "shimmers".
  - When the response finishes, a short truncated snippet appears above the composer; tapping it opens the chat sheet.

  — [OpenAI Apps SDK UI guidelines](https://developers.openai.com/apps-sdk/concepts/ui-guidelines)
- Accessibility rules for cards:
  - WCAG AA contrast.
  - Alt text on all images.
  - Layouts must survive text resizing.
  - System font stack.
  - Enforced aspect ratios for images.
  - No logo inside the card.

  — [OpenAI Apps SDK UI guidelines](https://developers.openai.com/apps-sdk/concepts/ui-guidelines)
- OpenAI's guidance is to use cards, carousels and fullscreen "only when visual interaction improves the workflow". — [OpenAI plugin UI guidelines](https://developers.openai.com/plugins/concepts/ui-guidelines) (from a search summary)
- Community guidance, not official: start with an inline summary and offer fullscreen when the UI needs tabs or several controls; size inline content to fit and avoid nested scrolling. — [Sunpeak display-mode reference](https://sunpeak.ai/blogs/chatgpt-app-display-mode-reference)

**Google A2UI (Agent-to-User Interface)**
- What it is: an open-source (Apache 2.0) protocol from Google, with contributions from CopilotKit. Agents send "declarative component descriptions" instead of text or executable code. — [a2ui.org](https://a2ui.org/)
- Safety model: agents may use only pre-approved components from a catalog the client provides. Custom components such as charts and maps are allowed. Because the format is data, there is no code to execute, which the site says prevents UI injection [vendor]. — [a2ui.org](https://a2ui.org/)
- Versions: v0.9.1 is the current production release, v1.0 is a release candidate, and v0.8 is legacy. v1.0 adds client-to-server RPC (`actionResponse`) and action IDs, which is how user actions on a card go back to the agent. — [a2ui.org](https://a2ui.org/). Press coverage from Dec 2025 cited v0.8, so the version has moved on since launch — [The Decoder](https://the-decoder.com/googles-open-standard-lets-ai-agents-build-user-interfaces-on-the-fly/)
- Streaming: the JSON is flat and streamed, so the UI builds up while it is generated. — [a2ui.org](https://a2ui.org/)
- Renderers: the site names Angular, Flutter and Lit renderers plus a Markdown renderer. There is no Jetpack Compose renderer on the main page. — [a2ui.org](https://a2ui.org/)
- Adoption: Flutter's GenUI SDK uses A2UI; secondary sources also name Opal and Gemini Enterprise. — [Analytics Vidhya](https://www.analyticsvidhya.com/blog/2025/12/google-a2ui-explained/); [The Decoder](https://the-decoder.com/googles-open-standard-lets-ai-agents-build-user-interfaces-on-the-fly/)
- How it compares: Google contrasts A2UI with MCP's approach, where UI is a resource, usually prefab HTML in a sandbox. — [The Decoder](https://the-decoder.com/googles-open-standard-lets-ai-agents-build-user-interfaces-on-the-fly/)

**Google Research "Generative UI" (Nov 2025)**
- The paper is "Generative UI: LLMs are Effective UI Generators" ([PDF](https://generativeui.github.io/static/pdfs/paper.pdf)). It ships in the Gemini app as the "dynamic view" and "visual layout" experiments, and in Search AI Mode for US Pro and Ultra users. — [Google Research blog](https://research.google/blog/generative-ui-a-rich-custom-visual-interactive-user-experience-for-any-prompt/)
- How it is built: Gemini 3 Pro writes a full custom interface for each prompt, using tools (image generation, web search), long system instructions and post-processors that fix common errors. — [Google Research blog](https://research.google/blog/generative-ui-a-rich-custom-visual-interactive-user-experience-for-any-prompt/)
- Human preference ranking (speed not counted): human-expert websites came first, generative UI "closely behind", and markdown, plain text and the top search result trailed "by a substantial gap" [vendor: the blog gives no numbers]. — [Google Research blog](https://research.google/blog/generative-ui-a-rich-custom-visual-interactive-user-experience-for-any-prompt/)
- Limitations the authors state: generation "can sometimes take a minute or more", there are "occasional inaccuracies", and quality depends heavily on the model. — [Google Research blog](https://research.google/blog/generative-ui-a-rich-custom-visual-interactive-user-experience-for-any-prompt/)

**Approval cards (human-in-the-loop): Vercel AI SDK and its ecosystem**
- Recent AI SDK docs describe tool approval: selected tool calls are reviewed, approved or denied before they run. This applies only to tools the SDK executes, not tools the model provider runs. — [AI SDK docs, tool approvals (ai@7.0.x)](https://app.unpkg.com/ai@7.0.40/files/docs/03-agents/06-tool-approvals.mdx)
- The message flow, per an integration page:
  - The backend emits `tool-approval-request` chunks.
  - The client answers with `addToolApprovalResponse` from `useChat`.
  - Rejected calls produce `tool-output-denied`.
  - AI Elements ships a prebuilt "Confirmation" component.

  — [Pydantic AI, Vercel AI integration](https://pydantic.dev/docs/ai/integrations/ui/vercel-ai/) (third-party)
- The usual approval card has three states: request (approve and reject buttons), accepted, and rejected. — [shadcn.io Confirmation](https://www.shadcn.io/ai/confirmation) (not affiliated with official shadcn/ui)
- An older pattern defines a tool without an `execute` function, so the client intercepts the call and asks the user. — [Ably HITL guide](https://ably.com/docs/ai-transport/guides/vercel-ai-sdk/vercel-human-in-the-loop)

### Inferences
- For DeskAI, the A2UI/OpenAI model maps directly onto Compose: the agent sends a typed card payload (video, image, doc, task, approval) and the app owns a fixed catalog of composables. No WebView-generated UI. This matches the existing "server only matches the API contract" split.
- Video cards: play inline at their own aspect ratio, with no nested scroll. If the owner keeps a video playing while scrolling, that is the PiP pattern, so pin the player to the top until it is dismissed.
- Task-progress card: the OpenAI PiP rule (pinned while live, returns to inline when finished) fits the planned "live running-task card in the top bar". While the task runs, the top-bar card is the PiP. When it finishes, it should become an ordinary inline card in the thread, so the record stays in history.
- Approval card: 1 primary and 1 secondary action, with the three states above. The resolved state ("Approved 14:02") should stay in the thread as a permanent record, never disappear.
- Generative UI that writes a whole interface per answer (Google's dynamic view) takes up to a minute or more per answer. That is too slow and too costly for the free-model-only budget, so a fixed component catalog is the right fit.

### Gaps
- Could not get primary-source detail on how Claude (artifacts/inline visuals), Perplexity (answer cards) or ChatGPT's own built-in media and task cards render on mobile. The search budget went to the documented frameworks instead.
- No confirmed A2UI Compose/Android renderer was found.
- The exact current names in the AI SDK (`needsApproval` vs `toolApproval`, the UI part states) were not confirmed against ai-sdk.dev.

## Single continuous conversation products: history, search, day markers, jumping to topics, long-thread performance

### Takeaway
Very little is published on how one-thread AI assistants handle navigation. Pi is the clearest example of a deliberate single running conversation with no reset. The established patterns for one endless thread come from messaging apps (date jump, search, day separators), not from AI products.

### Cited Findings
- **Pi (Inflection), single thread:**
  - Users "can't technically 'restart' their conversations with Pi" without a different login, per an Inflection spokesperson. — [Business Insider via search summary; Yahoo/Insider coverage](https://ca.news.yahoo.com/ai-chatbot-called-pi-designed-165950735.html)
  - Pi remembered at least 100 turns across logouts. — [Fortune, May 2023](https://fortune.com/2023/05/03/inflection-ai-deepmind-cofounder-mustafa-suleyman-pi-chatbot/amp)
  - The product still runs, but investment is uncertain after the 2024 Microsoft pivot (third-party listing, Mar 2026). — [Coda One](https://www.codaone.ai/tools/pi)
- **Messaging date-jump patterns:**
  - WhatsApp lets users pick a date and jump to messages sent on or after that day. — [Mimikama](https://www.mimikama.org/en/whatsapp-chat-search-date-function/)
  - Qwil opens the first message of the chosen day. — [Qwil support](https://support.qwil.io/hc/en-us/articles/4409136809617-Jump-to-date)
  - JANDI jumps to the next date that has messages when the chosen day is empty. — [JANDI](https://support.jandi.com/en/articles/date-search-8968b656)
  - Telegram has a calendar date search inside message search. Its bug tracker shows an iOS bug that jumped to Feb 1970. — [Telegram bug tracker](https://bugs.telegram.org/c/63155)
- **ChatGPT's "sources" as history navigation:** ChatGPT can link straight to past conversations, reportedly reaching back about a year, and the TechRadar headline reads "link you directly to them". This turns memory retrieval into a way to navigate history. Only the headline was retrievable. — [TechRadar](https://www.techradar.com/ai-platforms-assistants/chatgpt/after-todays-big-memory-upgrade-chatgpt-can-now-remember-conversations-from-a-year-ago-and-link-you-directly-to-them)

### Inferences
- DeskAI's single thread can borrow proven messaging patterns:
  - Sticky day separators ("Today", "Yesterday", "Mon 12 Oct").
  - A date-jump calendar that goes to the first message of the chosen day, or the next day with messages.
  - In-thread search whose results scroll to and briefly highlight the matched message.
  - A "back to latest" floating button after a jump.
- The provenance links in the next section double as the "jump to topic" feature. Each memory chip is a deep link (message id) into the same thread, so no session list is needed.
- Long-thread performance in Compose (general knowledge, not sourced here):
  - Page history from the server by message id or time cursor.
  - Use `LazyColumn` with stable `key`s and `contentType` per card type.
  - Load older pages as the user scrolls up, and jump to a date by loading the page around a target id rather than scrolling through everything.

### Gaps
- No sources were found on how Meta AI, Replika or Character.AI handle history, search or performance in their effectively single-thread chats.
- No NN/g or CHI work was found on navigating a single endless AI thread. This looks like an open area, not a missed search.

## Memory provenance and transparency UI ("remembered from Oct 12")

### Takeaway
By 2026 the leading pattern is per-answer "sources" that cover memory, not only web results:
- **ChatGPT** shows which past chats and saved memories shaped an answer. They open the original context and explain why each was used.
- **Claude** makes memory lookups visible as tool calls.
- **Gemini** personalizes from past chats and connected apps, but no per-answer provenance display was found.

User research consistently finds people want to see, edit and delete what the assistant remembers.

### Cited Findings
- **ChatGPT, past chats as sources (Jan 2026):** OpenAI's January 2026 release notes reportedly say a past chat used to answer appears as a source "so you can open and review the original context". (Search summary; the primary page returned 403.) — [Tom's Guide](https://tomsguide.com/ai/chatgpts-improved-memory-means-it-has-an-easier-time-remembering-your-previous-chats); [TechRadar](https://www.techradar.com/ai-platforms-assistants/chatgpt/after-todays-big-memory-upgrade-chatgpt-can-now-remember-conversations-from-a-year-ago-and-link-you-directly-to-them)
- **ChatGPT, memory sources (May 2026):** a single third-party write-up says "Memory Sources" reached all consumer plans on May 5, 2026, listing saved memories, past chats, custom instructions, library files and Gmail messages. Users can correct, delete or mark an item "not relevant". (Unverified single source; the page body could not be fetched.) — [ud.hk](https://www.ud.hk/en-US/resources/ai/2026-05-28-chatgpt-memory-sources)
- **ChatGPT help text** (via search summary):
  - Tapping a memory in sources "will open an explanation on why that memory was used".
  - Sources "may not show every factor".
  - Memory sources are left out of shared chats.

  — [OpenAI Help: What is memory](https://help.openai.com/fr-fr/articles/8983136-what-is-memory)
- **ChatGPT history:** referencing all past chats started in April 2025. — [TechCrunch](https://techcrunch.com/2025/04/10/openai-updates-chatgpt-to-reference-your-other-chats/)
- **Claude:**
  - Past-chat search is RAG and "will appear as tool calls" in the current chat, so the user sees when Claude looked something up. It is available on paid plans on web, desktop and mobile, and is scoped per project. — [Claude Help Center](https://support.claude.com/en/articles/11817273-how-does-claude-s-memory-work)
  - It is implemented as two tools, `conversation_search` and `recent_chats`. Willison argues the visible calls are a transparency advantage. — [Simon Willison, 12 Sep 2025](https://feeds.simonwillison.net/2025/Sep/12/claude-memory/)
  - The help pages disagree on where the toggle lives (Settings > Memory vs Settings > Capabilities). — [Claude Help Center](https://support.claude.com/en/articles/11817273)
- **Gemini:**
  - "Personal context" (Aug 13, 2025) personalizes from past chats by default, with a "Your past chats with Gemini" toggle. "Temporary Chats" are not used for personalization and are kept up to 72 h. — [9to5Google](https://9to5google.com/2025/08/13/gemini-personal-context/)
  - "Personal Intelligence" (beta, Jan 14, 2026, US Pro/Ultra) reasons across Gmail, Photos, YouTube and Search. — [Dataconomy](https://dataconomy.com/2026/01/15/gemini-gains-personal-intelligence-to-synthesize-data-from-gmail-and-photos/)
  - No source found describes a per-answer provenance UI in Gemini.
- **User research:**
  - Interviews (n=18) found users want granular control over how memories are created, managed, used and updated, with review, edit and delete. — [Aalto: Users' privacy perceptions of RAG-based memory (HAIPS 2025)](https://research.aalto.fi/en/publications/understanding-users-privacy-perceptions-towards-llms-rag-based-me/)
  - A study of ChatGPT memory found most participants had negative expectancy violations on learning what was remembered, and they asked for more visibility. — [UGA Grady: "AI Never Forgets"](https://grady.uga.edu/research/ai-never-forgets-exploring-users-perceptions-of-ai-memory-and-chatgpts-memory-feature/)
  - In a PACIS 2025 quasi-experiment (N=84), memorization lowered willingness to disclose private data; the authors propose memory visualization as a fix. — [PACIS 2025](https://aisel.aisnet.org/pacis2025/security/security/4)
  - MemoAnalyzer, a memory-visualization and editing tool, raised privacy awareness in a 5-day study (N=36). It is a CHI'23-era paper. — [arXiv 2410.14931](https://arxiv.org/html/2410.14931v1)
  - A 2026 arXiv analysis deconstructs what ChatGPT memory stores about users. — [The Algorithmic Self-Portrait](https://arxiv.org/pdf/2602.01450)

### Inferences
- DeskAI already records where each memory came from, so it can go one step further than ChatGPT. Because it is a single thread, a chip like "Remembered from 12 Oct" can deep-link to the exact message and highlight it, instead of opening a separate chat.
- Following ChatGPT's tap behaviour and the user research:
  - Show a small, quiet chip under the reply, never inline in the prose.
  - Tapping it opens a sheet with the remembered fact, why it was used, a "Go to moment" link, and Edit / Forget / "Not relevant".
- Present provenance as "may not show everything", as OpenAI does, rather than implying a complete explanation.
- This fits the owner's rule that inferences are a labelled tier of memory: label inferred items differently ("I guessed this from 3 chats") from facts the owner stated.

### Gaps
- Could not read OpenAI's primary release notes or memory FAQ (HTTP 403). The exact Jan 2026 and May 2026 wording and plan availability need checking.
- No source describes whether Gemini shows per-answer memory provenance.
- No CHI 2024-2026 paper on provenance UI specifically was found. The research above is from adjacent venues.

## Mobile chat UX: readability, spacing, grouping, input bar, accessibility (Material 3 / Android)

### Takeaway
The firm, citable Android rules are:
- touch targets of at least 48dp, separated by at least 8dp
- layouts that survive text scaling
- WCAG AA contrast
- alt text on images

OpenAI's card rules add: no nested scroll, a card no taller than the screen, at most 2 actions. No Material 3 chat-specific spec was found.

### Cited Findings
- Touch targets: at least 48dp, as in the Material accessibility guidelines. `IconButton` and Material components enforce this, and Compose extends small clickables' touch area beyond their visible bounds. Use `sizeIn` for dynamically sized controls. — [Android: Compose accessibility API defaults](https://developer.android.com/develop/ui/compose/accessibility/api-defaults)
- Separate 48x48dp touch targets by at least 8dp. — [Google Accessibility: Touch target size](https://support.google.com/accessibility/android/answer/7101858?hl=en-GB)
- Compose accessibility key steps (targets, content descriptions, semantics). — [Android: Key steps to improve Compose accessibility](https://developer.android.com/develop/ui/compose/accessibility/key-steps)
- Cards must handle text resizing, meet WCAG AA contrast, carry alt text and use system fonts. — [OpenAI Apps SDK UI guidelines](https://developers.openai.com/apps-sdk/concepts/ui-guidelines)
- While streaming: shimmer the composer, then show a truncated reply snippet above it. — [OpenAI Apps SDK UI guidelines](https://developers.openai.com/apps-sdk/concepts/ui-guidelines)

### Inferences
- The round glass mic, attach and hub buttons must each be at least 48dp with at least 8dp between them. A glass/blur style needs an opaque enough tint behind the icons to keep AA contrast (3:1 for icons, 4.5:1 for text) over a busy thread.
- Name and avatar only on the first message is standard message grouping. Keep the agent's identity reachable for TalkBack by giving every agent message a semantics label such as "Agent, 14:02", even when the avatar is hidden.
- Larger text and more spacing: use `sp` everywhere, and test cards at the largest font scale so they wrap and grow, never clip or scroll internally.

### Gaps
- No Material 3 chat-specific component guidance was found (bubbles, grouping, input bar).
- No NN/g 2025-2026 primary article on AI chat readability or history was found; search returned only secondary summaries.
- No sourced figures for chat line length, bubble spacing or Android 200% font-scale testing were found in this pass.
