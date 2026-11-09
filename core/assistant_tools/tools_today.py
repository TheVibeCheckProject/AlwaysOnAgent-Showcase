"""
Today tools: the Today screen's recipe and weather town; Google sign-in.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tools_today.py).

Split out of core/assistant_tools.py on 2026-11-03; AssistantTools inherits
these methods via _TodayToolsMixin, so they are still bound methods on the tools object.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from core import connectors, proactive
except ImportError:
    import connectors  # type: ignore[no-redef]
    import proactive  # type: ignore[no-redef]


class _TodayToolsMixin:
    if TYPE_CHECKING:
        # Host-class attributes, set by AssistantTools._init_state, and _mail_error,
        # which lives on _MailToolsMixin: that mixin comes AFTER this one in
        # AssistantTools' MRO, so a runtime stub here would shadow the real method.
        # These declarations exist for mypy only and create no runtime values.
        db: DatabaseManager
        memory: Any

        @staticmethod
        def _mail_error(e: Exception) -> str: ...

    def today_settings(self, add: str | None = None, replace: str | None = None,
                       location: str | None = None) -> str:
        """The Today screen's recipe and weather town (core/today.py)."""
        try:
            from core import today as today_mod
        except ImportError:
            import today as today_mod  # type: ignore[no-redef]
        p = proactive.Proactive(self.db)
        try:
            if location:
                p.change(location=location)
            current = today_mod.recipe(self.db)
            if replace is not None or add:
                text = replace if replace is not None else (current["text"] + ("\n" if current["text"] else "") + add)
                if len(text.strip()) > today_mod.RECIPE_MAX_CHARS:
                    # save_recipe keeps the first RECIPE_MAX_CHARS, so the newest words were dropped silently while the
                    # reply said they were saved (2026-10-28 audit 0.8)
                    return (f"Error: the Today recipe would be {len(text.strip())} characters, over its "
                            f"{today_mod.RECIPE_MAX_CHARS}. Nothing was changed. Offer to rewrite it shorter (replace) "
                            "with what still matters.")
                # save_recipe's set_location takes Callable[[str], None]; p.change returns a dict, so a
                # lambda passing it through would not match. A named function discards the result.
                def _set_town(town: str) -> None:
                    p.change(location=town)
                current = today_mod.save_recipe(self.db, text, today_mod.llm_asker(getattr(self.memory, "llm", None)),
                                                _set_town)
        except ValueError as e:
            return f"Error: {e}."
        town = p.settings().get("location") or "not set"
        recipe_text = current["text"].replace("\n", " / ") or "(empty)"
        return (f"Today recipe: \"{recipe_text}\". Rules: {today_mod.recipe_summary(current)}. Weather town: {town}. "
                "The user sees the rules as chips at the top of Today and can edit the recipe there too.")

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
    def _google_error(self, e: Exception) -> str:
        text = str(e)
        if "not allowed yet" in text or "not connected" in text or "connect again" in text:
            return (f"Not connected for this yet ({text}). Offer to connect: call connect_google, which gives the "
                    "user a link to approve on their phone.")
        return self._mail_error(e)

    def connect_google(self) -> str:
        try:
            url = connectors.google_auth_url()
        except Exception as e:
            return self._mail_error(e)
        return ("Give the user this link (it's tappable in their chat) and the steps, in plain words:\n"
                f"{url}\n"
                "1. Open it, choose their Google account, and allow what's listed (read Gmail, draft and send email, "
                "calendar, contacts, Drive, their lists: nothing is sent or added to the calendar without their tap on a card). Google may say the "
                "app isn't verified: it's their own; Advanced, then continue.\n"
                "2. They land on a page that doesn't load (its address starts with http://localhost). Copy that whole "
                "address and paste it into this chat. It finishes the connection; it never reaches you or memory.")

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['_TodayToolsMixin']
