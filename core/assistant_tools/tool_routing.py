"""
Picking tool groups by meaning, and the router that picks them.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tool_routing.py).

Split out of core/assistant_tools.py §3 and §5 on 2026-11-03: semantic_groups()
matches a message to groups by meaning, and the router (a small, fast AI) names
the extra groups a message needs. Imported by core/assistant_tools.py and by
the routing mixin; assistant_tools.py re-exports them all.
"""

import logging
import re
from typing import Any

try:
    from .tool_groups import GROUP_EXAMPLES
except ImportError:
    from core.assistant_tools.tool_groups import GROUP_EXAMPLES

logger = logging.getLogger(__name__)

_GROUP_EMBEDDINGS: dict[str, Any] | None = None
_CACHED_EMBEDDER_ID: int | None = None
# bge-small's scale. 0.70 found every needed group on 51 real messages with the fewest extras (0.66: 4 more
# extras); Qwen3-Embedding and EmbeddingGemma did no better at routing (2026-10-04)
SIM_THRESHOLD = 0.70
# A short reply that answers the assistant's offer, so the offer's tools come along ("yes, post it")
AFFIRMATION = re.compile(r"^(yes|yeah|yep|yup|sure|ok(ay)?|do it|go ahead|please do|sounds good|let'?s do it|"
                         r"go for it|absolutely|definitely)\b", re.IGNORECASE)

def _get_group_embeddings(embedder: Any) -> dict[str, Any] | None:
    global _GROUP_EMBEDDINGS, _CACHED_EMBEDDER_ID
    if embedder is None:
        return None
    if _GROUP_EMBEDDINGS is not None and _CACHED_EMBEDDER_ID == id(embedder):
        return _GROUP_EMBEDDINGS
    try:
        if hasattr(embedder, "ensure_loaded") and not getattr(embedder, "ready", False) and not embedder.ensure_loaded():
            return None
        cache = {}
        for g, examples in GROUP_EXAMPLES.items():
            vecs = embedder.embed(examples, query=False, wait=False)
            if vecs is None or len(vecs) == 0:
                return None
            cache[g] = vecs
        _GROUP_EMBEDDINGS = cache
        _CACHED_EMBEDDER_ID = id(embedder)
        return _GROUP_EMBEDDINGS
    except Exception as e:
        logger.warning(f"Group embeddings failed, routing without them: {e}")
        return None

def semantic_groups(text: str, embedder: Any, threshold: float = SIM_THRESHOLD) -> list[str]:
    """Matches text against example requests for each tool group using cosine similarity."""
    if not text or not text.strip() or embedder is None:
        return []
    try:
        import numpy as np
        group_vecs = _get_group_embeddings(embedder)
        if not group_vecs:
            return []
        q_vec = embedder.embed([text], query=True, wait=False)
        if q_vec is None or len(q_vec) == 0:
            return []
        qv = q_vec[0]
        matches = []
        for g, vecs in group_vecs.items():
            sims = vecs @ qv
            max_sim = float(np.max(sims))
            if max_sim >= threshold:
                matches.append((g, max_sim))
        matches.sort(key=lambda x: x[1], reverse=True)
        return [g for g, _ in matches[:2]]
    except Exception as e:
        logger.warning(f"Semantic group matching failed, using keyword groups only: {e}")
        return []

MAX_STARTS = 2            # tasks one reply may start (a runaway tool loop can't flood the queue)
MAX_NEW_SCHEDULES = 2     # schedules one reply may create


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════


# The router picks EXTRA groups only: the core tools (memory search, tasks and their logs, background status, asking
# the user) always go with a reply. 2026-10-14: when the router could answer "no tools at all", follow-ups like "I
# tapped it but it failed" got none, and the chat invented a log check ("task-196 failed because of uncommitted
# changes") and then claimed a merge it never did. Don't let a router verdict remove the core tools again.
ROUTER_SYSTEM_PROMPT = """You are a tool router for a personal assistant.
The assistant always has its core tools (memory search, background tasks and their status and logs, asking the user).
Given the user's message (and the assistant's previous reply, for context), decide which EXTRA tool groups are needed.
Return ONLY a JSON object: {"groups": ["group1"]} or {"groups": []} when the core tools are enough (casual chat,
greetings, reflections, questions about tasks or memory).
If uncertain whether a group is needed, include the most relevant group.

Available tool groups:
- web: live weather, web search, news, reading a web page URL
- mail: Gmail or Zoho Mail (search, read, unread counts, subscriptions, drafting email)
- calendar: Google Calendar (reading schedule, adding events)
- schedules: creating, listing or changing recurring schedules or reminders
- today: the app's Today screen: what it shows (its recipe: more or less of a topic, hide a kind of card, card limit,
  follow a YouTube channel there) and the town for its weather
- lists: Google Tasks (reading, adding to, or checking off to-do or shopping lists)
- phone: texting or calling someone from the user's phone
- drive: searching or reading Google Drive files
- videos: rendering a affirmation video, posting to TikTok
- stats: Pinterest pins (make, approve, post), weekly stats report
- projects: AgentWork coding on user repos, project files, diffs, go live
- canvas: writing or editing drafts (guides, scripts, plans, website pages)
- watches: mail watches for specific senders or subjects
- files: files on the home PC (find, list, read, show images/videos/audio/PDFs in chat), chat attachments, /image prompts
- computer: doing something by clicking through a website or app (a site's own search, forms, sign-ups)
"""

# The router's models: not 3.5 Flash-Lite, the chat's own first model (every router call spent one of its requests
# and per-minute slots right before the reply needed it). 2026-10-16, the router prompt on 20 of the owner's kinds
# of messages: Llama 3.3 70B on Cloudflare 20/20, median 0.67 s, ~12 neurons a call (~1% of the free 10,000 a day);
# gpt-oss-120b on Groq 19/20, 0.50 s; 3.5 Flash-Lite 15/20 (four 503s and a timeout), median 3.9 s. Llama 3.3 is
# only for this: as a chat model it ignored tool results and refused a joke ("outside the scope of the functions").
ROUTER_MODELS = ("cloudflare:@cf/meta/llama-3.3-70b-instruct-fp8-fast", "groq:openai/gpt-oss-120b")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['AFFIRMATION', 'MAX_NEW_SCHEDULES', 'MAX_STARTS', 'ROUTER_MODELS', 'ROUTER_SYSTEM_PROMPT', 'SIM_THRESHOLD', '_CACHED_EMBEDDER_ID', '_GROUP_EMBEDDINGS', '_get_group_embeddings', 'semantic_groups']
