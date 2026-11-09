"""Today §5: reading it — the edition or running list, the header, saved cards, reactions.

Moved into core/today/reading.py on 2026-11-04 (was core/today_reading.py, split out of
core/today.py §5 on 2026-11-03 as _ReadingMixin; Today inherits it). core/today_reading.py
is now a re-export shim.
"""
import datetime
import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .constants import (
        ACTIONS,
        BRIEF_SHOWN_FOR,
        DECK_MAX,
        EDITION_EVERY,
        MAX_CARDS,
        RUNNING_MAX,
        WEATHER_KEEP,
        in_deck,
    )
except ImportError:
    from today_constants import (  # type: ignore[no-redef]
        ACTIONS,
        BRIEF_SHOWN_FOR,
        DECK_MAX,
        EDITION_EVERY,
        MAX_CARDS,
        RUNNING_MAX,
        WEATHER_KEEP,
        in_deck,
    )

try:
    from .links import _parse
except ImportError:
    from today_links import _parse  # type: ignore[no-redef]

try:
    from .recipe import apply_rules, recipe
except ImportError:
    from today_recipe import apply_rules, recipe  # type: ignore[no-redef]


logger = logging.getLogger("AlwaysOnAgent.Today")


class _ReadingMixin:
    # Host state set by Today.__init__ (core/today/__init__.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime. Types mirror Today.__init__.
    db: "DatabaseManager"  # quoted: imported only under TYPE_CHECKING
    images_dir: Path
    now: Callable[[], datetime.datetime]
    weather: Callable[[str], dict[str, Any] | None]
    location: Callable[[], str]
    _weather_cache: tuple[str, datetime.datetime, Any] | None

    if TYPE_CHECKING:
        # build_if_due lives on _BuildingMixin, which comes AFTER this mixin in
        # Today's MRO (Today(_ReadingMixin, _BuildingMixin, _PicturesMixin)):
        # declared type-only — a runtime stub here would shadow the real method.
        def build_if_due(self, force: bool = False) -> int | None: ...

    def edition(self, edition_id: int | None = None, refresh: bool = False) -> dict[str, Any]:
        """The edition to show: a given one, or the latest (a new one is built first when it's due). The header
        (date, weather, the day's brief) and the recipe are read now, not when the edition was built: a town or
        recipe set later shows at once (2026-10-28: the town was set and the edition kept saying nothing)."""
        if edition_id is None:
            self.build_if_due(force=refresh)
        rules = recipe(self.db)["rules"]
        out: dict[str, Any] = {"header": self.header(), "edition": None, "items": [], "older": [],
                               "recipe": recipe(self.db)}
        with self.db.get_connection() as conn:
            row, items, older = self._edition_load(conn, edition_id)
        if row is None:
            return out
        built = _parse(row["built_at"])
        shown, per_deck = self._edition_apply(items, rules, edition_id)
        out.update(edition={"id": row["id"], "built_at": row["built_at"], **json.loads(row["header_json"] or "{}"),
                            "count": len(shown) - sum(per_deck.values()),
                            "next_after": (built + EDITION_EVERY).isoformat() if built else None},
                   items=shown, older=[{"id": o["id"], "built_at": o["built_at"]} for o in older])
        return out

    def _edition_load(self, conn, edition_id: int | None):
        """The edition row, its items, and the older editions. A given edition shows its own items; the latest
        shows the running list: the newest RUNNING_MAX cards from every edition, newest first, until dismissed."""
        row = (conn.execute("SELECT * FROM today_editions WHERE id = ?", (edition_id,)).fetchone()
               if edition_id is not None else
               conn.execute("SELECT * FROM today_editions ORDER BY id DESC LIMIT 1").fetchone())
        if row is None:
            return None, [], []
        if edition_id is not None:
            items = conn.execute("SELECT * FROM today_items WHERE edition = ? AND state != 'dismissed' "
                                 "ORDER BY position", (row["id"],)).fetchall()
        else:
            # Showing only the latest edition hid things not yet seen: on Oct 29 one 5:48 am story became a new
            # edition and pushed the night's two videos off Today (Alex: "I never got rid of the two that were
            # there"). Only the newest "finished tasks" card is kept.
            items, done = [], False
            for i in conn.execute("SELECT * FROM today_items WHERE state != 'dismissed' "
                                  "ORDER BY edition DESC, position LIMIT 200").fetchall():
                if i["card"] == "done":
                    if done:
                        continue
                    done = True
                items.append(i)
        older = conn.execute("SELECT id, built_at FROM today_editions WHERE id < ? ORDER BY id DESC LIMIT 7",
                             (row["id"],)).fetchall()
        return row, items, older

    def _edition_apply(self, items, rules, edition_id: int | None):
        """Cap the items, then append the deck extras (trivia and "on this day") after the cap."""
        cap = rules.get("max_cards") or (RUNNING_MAX if edition_id is None else MAX_CARDS)
        # Trivia and "on this day" live in the app's two decks (2026-11-03: 14 of the 20 cards were these daily
        # extras, a new one each day). They no longer count against the running list's cap: the newest DECK_MAX of
        # each come after it, and DeskAI 109+ draws them as decks (an older build shows them as cards, as before)
        decked = [i for i in items if in_deck(dict(i))] if edition_id is None else []
        items = [i for i in items if not (edition_id is None and in_deck(dict(i)))]
        shown = apply_rules([self._public(dict(i)) for i in items], {**rules, "max_cards": cap}, reorder=False)
        per_deck: dict[str, int] = {}
        for d in apply_rules([self._public(dict(i)) for i in decked], {**rules, "max_cards": 2 * DECK_MAX},
                             reorder=False):
            kind = "trivia" if d.get("card") == "trivia" else "onthisday"
            if per_deck.get(kind, 0) < DECK_MAX:
                per_deck[kind] = per_deck.get(kind, 0) + 1
                shown.append(d)
        return shown, per_deck

    def header(self) -> dict[str, Any]:
        """Today's date, the weather now at the owner's town (kept WEATHER_KEEP), and the day's brief, once written."""
        now = self.now()
        h: dict[str, Any] = {"date": now.date().isoformat(), "weather": None, "brief": None, "location": ""}
        try:
            loc = (self.location() or "").strip()
        except Exception as e:
            logger.info(f"Today: no location ({type(e).__name__}: {e})")
            loc = ""
        h["location"] = loc
        if loc:
            cached = self._weather_cache
            if cached and cached[0] == loc and now - cached[1] < WEATHER_KEEP:
                h["weather"] = cached[2]
            else:
                try:
                    h["weather"] = self.weather(loc)
                    self._weather_cache = (loc, now, h["weather"])
                except Exception as e:
                    logger.info(f"Today: no weather ({type(e).__name__}: {e})")
        try:
            with self.db.get_connection() as conn:
                b = conn.execute("SELECT * FROM proactive_messages WHERE kind = 'brief' "
                                 "ORDER BY id DESC LIMIT 1").fetchone()
            at = _parse(b["created_at"]) if b else None
            if at is not None and now - at <= BRIEF_SHOWN_FOR:
                # The facts it was written from, for the tiles: what's coming up (still ahead now) and the mail
                try:
                    # .keys(): `in` on a sqlite3.Row looks at its values, not its column names
                    data = json.loads(b["data_json"] or "{}") if "data_json" in b.keys() else {}  # noqa: SIM118
                except ValueError:
                    data = {}
                # Times are the brief's day's: past ones (or all, once it's another day) aren't "up next" anymore
                up_next = [u for u in data.get("up_next") or []
                           if isinstance(u, dict) and at.date() == now.date() and str(u.get("time", "")) >= f"{now:%H:%M}"]
                h["brief"] = {"text": b["text"], "at": b["created_at"], "up_next": up_next,
                              "mail": [m for m in data.get("mail") or [] if isinstance(m, dict)]}
        except sqlite3.OperationalError:
            pass
        # This month's trivia: answered right / answered (2026-10-29)
        try:
            month = now.strftime("%Y-%m")
            with self.db.get_connection() as conn:
                r = conn.execute("SELECT SUM(state = 'right'), SUM(state IN ('right', 'wrong')) FROM today_items "
                                 "WHERE card = 'trivia' AND substr(created_at, 1, 7) = ?", (month,)).fetchone()
            h["trivia"] = {"right": int(r[0] or 0), "answered": int(r[1] or 0)}
        except sqlite3.Error:
            h["trivia"] = {"right": 0, "answered": 0}
        return h

    def saved(self) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM today_items WHERE state = 'saved' ORDER BY id DESC LIMIT 100").fetchall()
        return [self._public(dict(r)) for r in rows]

    @staticmethod
    def _public(i: dict[str, Any]) -> dict[str, Any]:
        return {"id": i["id"], "card": i["card"], "title": i["title"] or "", "body": i["body"] or "",
                "link": i["link"] or "", "site": i["site"] or "", "why": i["why"] or "", "state": i["state"],
                "image_url": f"/api/today/items/{i['id']}/image" if i["image"] else None,
                "created_at": i["created_at"], "source_ref": i["source_ref"] or ""}

    def image_path(self, item_id: int) -> Path | None:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT image FROM today_items WHERE id = ?", (item_id,)).fetchone()
        if row is None or not row["image"]:
            return None
        p = self.images_dir / row["image"]
        return p if p.is_file() else None

    def react(self, item_id: int, action: str) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r} (one of {', '.join(ACTIONS)})")
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM today_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise KeyError(item_id)
            state = ACTIONS[action]
            # Seeing a card never undoes what Alex did with it
            if action == "seen" and row["state"] != "new":
                state = row["state"]
            conn.execute("UPDATE today_items SET state = ? WHERE id = ?", (state, item_id))
            if action == "answered" and (row["source_ref"] or "").startswith("candidate:"):
                conn.execute("UPDATE proactive_candidates SET status = 'sent', outcome = 'answered', sent_at = ? "
                             "WHERE id = ?", (self.now().isoformat(), int(row["source_ref"].split(":", 1)[1])))
            row = conn.execute("SELECT * FROM today_items WHERE id = ?", (item_id,)).fetchone()
        return self._public(dict(row))
