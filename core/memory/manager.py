"""
Long-term memory for the AlwaysOnAgent assistant
================================================
Layered, like a person's memory:

1. Recent conversation   - the latest messages, verbatim, within a token budget.
2. Compressed history    - when the recent window grows past COMPRESS_TRIGGER tokens, its oldest part
                           is summarized (level 1). When a level has more than ROLLUP_KEEP summaries,
                           the oldest ROLLUP_MERGE are merged one level up, so history of any length
                           stays a bounded, ever-coarser digest.
3. Core profile          - a short always-present brief about the user, rebuilt from the facts.
4. Long-term facts       - durable facts extracted after every exchange; new information updates or
                           replaces old facts instead of piling up duplicates.
5. Archive               - every message is kept forever and searchable.

Search is hybrid: SQLite FTS5 keyword ranking fused (reciprocal rank fusion) with semantic
similarity from a local embedding model (core/embedder.py). Without the model it degrades to
keyword search. Everything lives in the agent's SQLite database; only the slice of memory
relevant to a request is sent to the LLM. Secrets are redacted before anything is stored.

One conversation ("main") is shared by every interface (DeskAI, the HUD, background tasks), so
the assistant is the same person everywhere.

MAP OF THIS PACKAGE
-------------------
The sections live in core/memory/: manager.py holds §1-§4 (the
MemoryManager shell), one file per section for the rest.
Each section starts with a banner like "# §7  MESSAGES". Search for the § number to jump straight to it.

  §1   Setup and small helpers     imports, the kinds of facts, token counting, little converters
  §3   Reply guards                plain-code checks on every reply before it's saved: a claim nothing backs, a
                                   promise nothing keeps, a broken media link, a second closing question in a row
  §4   MemoryManager: setup        the one class everything below lives in; __init__ stays here, the rest
                                   moved to core/memory/memory_setup.py (_SetupMixin)






  ( §2, the persona, moved to core/memory/persona.py; its names are re-exported by the package.)
( §5, §6, §7, §10, §15 and §17 moved to core/memory/memory_schema.py, core/memory/memory_embeddings.py,
  core/memory/memory_messages.py, core/memory/memory_screen.py, core/memory/memory_compression.py and
  core/memory/memory_stats.py; §8, §9, §11, §12, §13, §14 and §16 moved to core/memory/memory_facts.py,
  core/memory/memory_second_opinion.py, core/memory/memory_search.py, core/memory/memory_prompt.py,
  core/memory/memory_chat.py, core/memory/memory_learning.py and core/memory/memory_upkeep.py; §4 (minus
  __init__) moved to core/memory/memory_setup.py. MemoryManager inherits them all as mixins.
  §1 setup constants and small helpers moved to core/memory/memory_text.py and are re-exported by the package.)

Related things sit together: a helper is in the section of the code that uses it (the self-check line for the
prompt is in §12, next to build_context). When adding a method, put it in its section and keep this map in step
with the banners; a new kind of work gets a new numbered section.

How one message flows: §13 respond() -> §12 build_context() (persona (core/memory/persona.py), profile, memories from §11 search,
recent history §7, live status, closing reminder) -> the model and its tools -> the §3 guards -> saved (§7) ->
in the background, §14 learns from it and §15 compresses when the window is full.
"""

# §1  SETUP AND SMALL HELPERS
#     Imports, the kinds of facts memory keeps (CATEGORIES), how much of it background tasks see, and small helpers:
#     counting tokens, storing vectors in the database, building a keyword search, reading a model's JSON answer.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════

import concurrent.futures
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

try:
    from core.config import redact_secrets
    from core.database import DatabaseManager, get_iso_now
    from core.llm_usage import UsageLog, usage_purpose
    from core.procedures import Procedures
except ImportError:
    from database import DatabaseManager  # type: ignore[no-redef]
    from llm_usage import UsageLog  # type: ignore[no-redef]
    from procedures import Procedures  # type: ignore[no-redef]

logger = logging.getLogger("Memory")
NOTIFY: Any | None = None      # set by the live agent (core.config.notify_owner): tells the owner when a delete is held

# §1 setup constants and small helpers moved to core/memory/memory_text.py;
# re-exported by the package so `from core.memory import CATEGORIES` (and count_tokens,
# _fts_query, _parse_json_object, _is_date_statement) still works.
try:
    from .memory_text import (
        _DATE_STATEMENT,
        _STOPWORDS,
        CATEGORIES,
        FOLLOW_UP_PRONOUNS,
        MAIN_CONV,
        SPEAKER,
        TASK_CATEGORIES,
        TASK_FACTS,
        _blob_to_vec,
        _evidence,
        _fts_query,
        _is_date_statement,
        _parse_json_object,
        _short_date,
        _vec_to_blob,
        count_tokens,
    )
except ImportError:
    from .memory_text import (
        MAIN_CONV,
        _blob_to_vec,
        _short_date,
        _vec_to_blob,
        count_tokens,
    )


# §2  THE PERSONA
#     ASSISTANT_PERSONA is the assistant's picture of itself, sent first with every message: honesty rules, how to
#     talk (no praise, few questions), how memory works, what it can do. Then the notes for when you dispute
#     something it said, FINAL_REMINDER (the last thing it reads before every reply, so it carries the rules that
#     matter most), and the small fixed texts the prompt is built from.
#     Moved to core/memory/persona.py and re-exported by the package for backwards compatibility.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
try:
    from .persona import (
        ASSISTANT_PERSONA,
        CAPABILITIES_SUMMARY,
        CONTEXT_HEADER,
        DISPUTE,
        DISPUTE_NOTE,
        FINAL_REMINDER,
        GUARD_PROFILE_NOTE,
        GUARD_REMINDER,
        HONESTY_GUARD,
        NO_TOOLS_NOTE,
        dispute_on,
        guard_on,
    )
except ImportError:
    pass

# §3  REPLY GUARDS
#     Plain code (no AI) that checks every reply after the model writes it and before it's saved or shown.
#     Moved to core/memory/reply_guards.py and re-exported by the package for backwards compatibility.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
try:
    from .reply_guards import (
        BACKGROUND_CLAIM,
        BACKGROUND_NOTE,
        CLAIMS,
        LIVE_CLAIM,
        LIVE_NOTE,
        MEDIA_AS_IMAGE,
        MEDIA_ICON,
        MEDIA_LINE,
        MEDIA_LOOKUPS,
        MEDIA_MADE_UP,
        PROMISE,
        PROMISE_BACKED_BY,
        PROMISE_FAILED_NOTE,
        PROMISE_STARTED_NOTE,
        PROMISE_TAIL,
        SHOW_COMMAND,
        TAIL_KEEP_MIN,
        TAIL_QUESTION,
        TASK_ID,
        ensure_media_shown,
        keep_promise,
        tools_ran_note,
        trim_tail_question,
        unbacked_claim_note,
    )
except ImportError:
    pass


try:
    from .memory_compression import _CompressionMixin
    from .memory_embeddings import _EmbeddingsMixin
    from .memory_messages import _MessagesMixin
    from .memory_schema import _SchemaMixin
    from .memory_screen import _MemoryScreenMixin
    from .memory_stats import _StatsMixin
    from .memory_text import _blob_to_vec, _short_date, _vec_to_blob, count_tokens
except ImportError:
    from .memory_compression import _CompressionMixin
    from .memory_embeddings import _EmbeddingsMixin
    from .memory_messages import _MessagesMixin
    from .memory_schema import _SchemaMixin
    from .memory_screen import _MemoryScreenMixin
    from .memory_stats import _StatsMixin
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
try:
    from .memory_chat import _ChatTurnMixin
    from .memory_compression import _CompressionMixin
    from .memory_embeddings import _EmbeddingsMixin
    from .memory_facts import _FactsMixin
    from .memory_learning import _LearningMixin
    from .memory_messages import _MessagesMixin
    from .memory_prompt import _PromptMixin
    from .memory_schema import _SchemaMixin
    from .memory_screen import _MemoryScreenMixin
    from .memory_search import _SearchMixin
    from .memory_second_opinion import _SecondOpinionMixin
    from .memory_setup import _SetupMixin
    from .memory_stats import _StatsMixin
    from .memory_upkeep import _UpkeepMixin
except ImportError:
    from .memory_chat import _ChatTurnMixin
    from .memory_compression import _CompressionMixin
    from .memory_embeddings import _EmbeddingsMixin
    from .memory_facts import _FactsMixin
    from .memory_learning import _LearningMixin
    from .memory_messages import _MessagesMixin
    from .memory_prompt import _PromptMixin
    from .memory_schema import _SchemaMixin
    from .memory_screen import _MemoryScreenMixin
    from .memory_search import _SearchMixin
    from .memory_second_opinion import _SecondOpinionMixin
    from .memory_setup import _SetupMixin
    from .memory_stats import _StatsMixin
    from .memory_upkeep import _UpkeepMixin


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
# §4  MEMORYMANAGER: SETUP
#     The one class that holds the whole memory. __init__ stays here; the prompt budget, thresholds,
#     and which AI models do the background chores moved to core/memory/memory_setup.py (_SetupMixin).
#     Sections §5 to §17 are all methods of this class, inherited as mixins.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
class MemoryManager(_SetupMixin, _FactsMixin, _SecondOpinionMixin, _SearchMixin, _PromptMixin, _ChatTurnMixin, _LearningMixin, _UpkeepMixin, _SchemaMixin, _EmbeddingsMixin, _MessagesMixin, _MemoryScreenMixin, _CompressionMixin, _StatsMixin):
    def __init__(self, db: DatabaseManager, llm: Any = None, embedder: Any = None,
                 conv: str = MAIN_CONV, log: logging.Logger | None = None):
        self.db = db
        self.llm = llm
        self.embedder = embedder
        self.conv = conv
        self.log = log or logger
        self._bg = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="MemoryWorker")
        self._msg_cache: dict[int, np.ndarray] = {}
        self._msg_cache_mark = 0.0
        self._fact_cache: tuple[Any, list[int], np.ndarray | None] = (None, [], None)
        self._cache_lock = threading.Lock()
        # Similarity cut-offs are on a model's own scale: a model that brings its own replaces the defaults below
        for name, value in (getattr(embedder, "THRESHOLDS", None) or {}).items():
            setattr(self, name, value)
        self._init_schema()
        self.procedures = Procedures(self)        # how the agent's own work got done (core/procedures.py)
        self._check_embedding_model()
        if self.LOCAL_MODE != "shadow":
            # The shadow trial's log (conversation text, up to 20 MB) outlived the trial (off since 2026-10-10), and a
            # forget never reached it (2026-10-28 audit 4.1)
            try:
                (Path(getattr(db, "db_path", "data/state.db")).parent / "shadow" / "memory.jsonl").unlink(missing_ok=True)
            except OSError:
                pass
        self.owner_name()                 # tells the search model whose questions it reads
        # Record the tokens of every call the LLM client makes (chat, memory upkeep, tasks) in this database
        if getattr(llm, "usage_log", False) is None:
            llm.usage_log = UsageLog(db)

    # §5  MOVED to core/memory/memory_schema.py (_SchemaMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §6  MOVED to core/memory/memory_embeddings.py (_EmbeddingsMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §7  MOVED to core/memory/memory_messages.py (_MessagesMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §8  MOVED to core/memory/memory_facts.py (_FactsMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §9  MOVED to core/memory/memory_second_opinion.py (_SecondOpinionMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §10  MOVED to core/memory/memory_screen.py (_MemoryScreenMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §11  MOVED to core/memory/memory_search.py (_SearchMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §12  MOVED to core/memory/memory_prompt.py (_PromptMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §13  MOVED to core/memory/memory_chat.py (_ChatTurnMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §14  MOVED to core/memory/memory_learning.py (_LearningMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §15  MOVED to core/memory/memory_compression.py (_CompressionMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §16  MOVED to core/memory/memory_upkeep.py (_UpkeepMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    # §17  MOVED to core/memory/memory_stats.py (_StatsMixin)
    #     (methods/constants from this section now live there; MemoryManager inherits them)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
