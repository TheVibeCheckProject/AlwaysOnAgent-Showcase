"""
Choosing this message's tools: the router, the keyword/semantic picker,
and more_tools.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tools_routing.py).

Split out of core/assistant_tools.py on 2026-11-03; AssistantTools inherits
these methods via _RoutingMixin, so they are still bound methods on the tools object.
"""

import json
import logging
import os
import re
import sqlite3
from typing import TYPE_CHECKING, Any

try:
    from core import watches

    from .tool_constants import SELF_NUMBERS
    from .tool_groups import GROUP_ABOUT, GROUP_GUIDES, GROUP_WORDS, TOOL_GROUPS
    from .tool_routing import AFFIRMATION, ROUTER_MODELS, ROUTER_SYSTEM_PROMPT, semantic_groups
except ImportError:
    import watches  # type: ignore[no-redef]

    from core.assistant_tools.tool_constants import SELF_NUMBERS
    from core.assistant_tools.tool_groups import GROUP_ABOUT, GROUP_GUIDES, GROUP_WORDS, TOOL_GROUPS
    from core.assistant_tools.tool_routing import AFFIRMATION, ROUTER_MODELS, ROUTER_SYSTEM_PROMPT, semantic_groups

logger = logging.getLogger(__name__)


class _RoutingMixin:
    # Interface this mixin requires from the host class (AssistantTools).
    # The plain annotations create no runtime value, so nothing here can
    # shadow a real definition.
    # (a bare annotation is not enough: mypy infers this attribute's type
    # from the assignment in agentwork_projects below, so give it a value
    # here so the declared Optional type is the one mypy uses. The class
    # attribute is harmless at runtime: AssistantTools.__init__ always sets
    # the instance attribute.)
    _projects: list[dict[str, Any]] | None = None
    agentwork: Any | None
    user_text: str
    db: Any
    # _specs is assigned a plain list in tool_specs below, which would make
    # mypy infer a non-Optional type and clash with AssistantTools.__init__'s
    # Optional declaration; declare it Optional here (None = "not picked yet",
    # which is what more_tools already checks for).
    _specs: list[dict[str, Any]] | None = None

    # specs lives on AssistantTools itself, which comes before this mixin in
    # the MRO, so this runtime stub never shadows it.
    @staticmethod
    def specs(projects: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    # _memory_review lives on _CoreToolsMixin, which comes AFTER this mixin
    # in AssistantTools' MRO, so a runtime stub here would shadow it; declare
    # it only for the type checker.
    if TYPE_CHECKING:
        def _memory_review(self) -> Any:
            ...

    def agentwork_projects(self) -> list[dict[str, Any]]:
        """AgentWork's projects (read once per reply); [] without AgentWork."""
        if self._projects is None:
            self._projects = self.agentwork.list_projects() if self.agentwork else []
        return self._projects

    def pick_groups_router(self, llm: Any | None = None) -> list[str] | None:
        """Asks a small, fast model which EXTRA tool groups this message needs (on top of core, which tool_specs
        always sends). Returns a list (maybe []), or None if the router is off or failed (then tool_specs uses the
        keyword + semantic picker)."""
        if not self.user_text or not self.user_text.strip():
            return None
        if os.getenv("CHAT_TOOL_ROUTER", "1") == "0":
            return None
        if llm is None or not hasattr(llm, "complete"):
            return None
        try:
            query_text = self.user_text
            # The previous reply goes along every time, not only after a "yes": a follow-up ("I tapped it but it
            # failed") means nothing without the message it answers (2026-10-14).
            try:
                row = self.db.get_connection().execute(
                    "SELECT content FROM mem_messages WHERE role = 'assistant' ORDER BY id DESC LIMIT 1").fetchone()
                if row and row[0]:
                    query_text = f"Assistant's previous reply: {row[0][:300]}\nUser's message: {self.user_text}"
            except sqlite3.Error:
                pass                                      # no memory table (tests, a fresh database)

            from core.llm_usage import usage_purpose
            with usage_purpose("route_tools"):
                res, _route = llm.complete(
                    [
                        {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                        {"role": "user", "content": query_text}
                    ],
                    max_tokens=60,
                    temperature=0.0,
                    json_mode=True,
                    timeout=5,
                    models=list(ROUTER_MODELS)
                )
            if res:
                parsed = json.loads(res)
                groups = parsed.get("groups")
                if isinstance(groups, list):
                    # A group that doesn't exist ("email" for mail) means the router misread the list: dropping
                    # it would leave the reply with core only, so the keyword picker decides instead
                    if any(not isinstance(g, str) or g not in TOOL_GROUPS for g in groups):
                        return None
                    # core is always sent and memory_check only while the weekly check waits: neither is a pick
                    resolved = [g for g in groups if g not in ("core", "memory_check")]
                    return list(dict.fromkeys(resolved))
        except Exception as e:
            logger.warning(f"Tool router failed, falling back to keyword picker: {e}")
        return None

    def tool_specs(self, llm: Any | None = None, use_router: bool = False) -> list[dict[str, Any]]:
        """The tools for this reply: "core" always, plus the groups the message is about, plus more_tools. The
        groups come from the router model when use_router=True and it answers, else from the keyword + semantic
        picker. Never empty: without core the chat can't check anything and invents answers (2026-10-14). The
        list is kept: more_tools extends it in place, and the tool loop sends the same list on its next round."""
        every = self.specs(self.agentwork_projects())
        if os.getenv("CHAT_TOOL_ROUTING", "1") == "0":
            return every
        self._every_spec = {s["function"]["name"]: s for s in every}
        router_groups = self.pick_groups_router(llm) if use_router else None
        self.groups = router_groups if router_groups is not None else self.pick_groups()
        self.picked_by = "router" if router_groups is not None else "keywords"
        # Asked about its own numbers, the chat must have my_numbers whatever the router says (2026-11-03: the router
        # gave nothing for "can you see the numbers yourself now?", and it answered with the PC's heartbeat count)
        if SELF_NUMBERS.search(self.user_text or "") and "stats" not in self.groups:
            self.groups.append("stats")
        # Each tool once: a tool in two groups (contacts_search; connect_google since 2026-10-27) was sent twice when
        # both were loaded, and Gemini rejects the whole request ("Duplicate function declaration": Oct 11, Oct 27)
        names = list(dict.fromkeys(n for g in ["core", *self.groups] for n in TOOL_GROUPS[g]))
        review = self._memory_review()
        if review is not None and review.pending():
            names.append("memory_check")
        self._specs = [self._every_spec[n] for n in names if n in self._every_spec] + [self._more_tools_spec()]
        return self._specs

    def pick_groups(self) -> list[str]:
        """The groups this message is about. Only a short yes-type reply ('yes, do it') also reads the
        assistant's last message, which it answers. No match means core tools only; more_tools loads a group.

        Measured on 51 real messages (2026-10-04, IDEAS #16): reading the last three messages too, and adding
        the web group when nothing matched, sent 114 groups nobody needed ("Thank you" got the project tools
        because the previous reply mentioned code): ~1,610 tool tokens a call. Message-only finds all 21 needed
        groups with 12 extra: ~444 tokens."""
        text = self.user_text.lower()
        if AFFIRMATION.match(self.user_text.strip()) and len(self.user_text.split()) <= 6:
            try:
                row = self.db.get_connection().execute(
                    "SELECT content FROM mem_messages WHERE role = 'assistant' ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    text += " " + row[0].lower()
            except sqlite3.Error:
                pass                                      # no memory table (tests, a fresh database)
        regex_groups = [g for g, words in GROUP_WORDS.items() if re.search(words, text)]
        if "watches" not in regex_groups and self._mentions_a_watch(text):
            regex_groups.append("watches")     # 2026-10-10: "only show the pinterest email..." got no watch tools
        sem_groups = semantic_groups(self.user_text, getattr(self, "embedder", None))
        return list(dict.fromkeys(regex_groups + sem_groups))

    def _mentions_a_watch(self, text: str) -> bool:
        """The message names what an active watch matches (its sender, subject or words): it's about that watch."""
        try:
            active = watches.Watches(self.db).list()
        except sqlite3.Error:
            return False
        for w in active:
            for field in ("sender", "subject", "words", "gone", "target"):
                for token in re.findall(r"[a-z0-9]{4,}", (w.get(field) or "").lower()):
                    if token not in ("http", "https", "mail", "email", "account", "noreply", "www") and token in text:
                        return True
        return False

    def guides(self) -> str:
        """How to use the groups sent this reply (GROUP_GUIDES), for the context note; '' when only core went."""
        lines = [f"- {g}: {GROUP_GUIDES[g]}" for g in getattr(self, "groups", []) if g in GROUP_GUIDES]
        return ("### Using the tools loaded for this message\n" + "\n".join(lines)) if lines else ""

    def _more_tools_spec(self) -> dict[str, Any]:
        others = [g for g in GROUP_ABOUT if g not in getattr(self, "groups", [])]
        return {"type": "function", "function": {
            "name": "more_tools",
            "description": "Load a tool group this request needs: " + "; ".join(f"{g} ({GROUP_ABOUT[g]})" for g in others),
            "parameters": {"type": "object", "properties": {
                "group": {"type": "string", "enum": others or list(GROUP_ABOUT)}}, "required": ["group"]}}}

    def more_tools(self, group: str) -> str:
        if group not in GROUP_ABOUT:
            return f"Error: no tool group '{group}'. Groups: {', '.join(GROUP_ABOUT)}."
        specs = getattr(self, "_specs", None)
        if specs is None:
            return "Every tool is already available."
        have = {s["function"]["name"] for s in specs}
        added = [n for n in TOOL_GROUPS[group] if n not in have and n in self._every_spec]
        specs[-1:-1] = [self._every_spec[n] for n in added]     # before more_tools, which stays last
        self.groups = [*getattr(self, "groups", []), group]
        guide = f" How to use them: {GROUP_GUIDES[group]}" if group in GROUP_GUIDES else ""
        return (f"Loaded: {', '.join(added)}. Call them now.{guide}" if added
                else f"The {group} tools were already loaded.")

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['_RoutingMixin']
