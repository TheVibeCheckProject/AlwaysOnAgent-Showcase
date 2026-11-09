"""Long-term memory for the AlwaysOnAgent assistant, as a package.

Everything that used to live in the flat ``core/memory.py`` (plus the §1-§17
mixins, the persona and the reply guards, split out on 2026-11-03) now lives
here, one file per section:

  manager.py            MemoryManager itself (§1 setup, §4 __init__)
  memory_text.py        §1 setup constants and small helpers
  persona.py            §2 the persona
  reply_guards.py       §3 reply guards
  memory_setup.py       §4 setup mixin (minus __init__)
  memory_schema.py      §5 schema mixin
  memory_embeddings.py  §6 embeddings mixin
  memory_messages.py    §7 messages mixin
  memory_facts.py       §8 facts mixin
  memory_second_opinion.py  §9 second-opinion mixin
  memory_screen.py      §10 screen mixin
  memory_search.py      §11 search mixin
  memory_prompt.py      §12 prompt mixin
  memory_chat.py        §13 chat-turn mixin
  memory_learning.py    §14 learning mixin
  memory_compression.py §15 compression mixin
  memory_upkeep.py      §16 upkeep mixin
  memory_stats.py       §17 stats mixin

This module re-exports everything the old flat ``core/memory.py`` exposed,
so ``from core.memory import X`` and ``import core.memory`` keep working
exactly as before — including the names tests patch on the module
(``guard_on``, ``dispute_on``) and the one the live agent sets (``NOTIFY``).

NOTE: the old ``core/memory.py`` was a thin shim that was never imported
while the package existed (CPython resolves ``import core.memory`` to this
package); it was removed 2026-11-04, so this file is the operative re-export
layer.
"""

from .manager import (
    NOTIFY,
    DatabaseManager,
    MemoryManager,
    Procedures,
    UsageLog,
    _ChatTurnMixin,
    _CompressionMixin,
    _EmbeddingsMixin,
    _FactsMixin,
    _LearningMixin,
    _MemoryScreenMixin,
    _MessagesMixin,
    _PromptMixin,
    _SchemaMixin,
    _SearchMixin,
    _SecondOpinionMixin,
    _SetupMixin,
    _StatsMixin,
    _UpkeepMixin,
    get_iso_now,
    logger,
    redact_secrets,
    usage_purpose,
)
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
