"""Today §6: building an edition — when one is due; gathering cards; storing it.

Moved into core/today/build.py on 2026-11-04 (was core/today_build.py, split out of
core/today.py §6 on 2026-11-03 as _BuildingMixin; Today inherits it). core/today_build.py
is now a re-export shim.
"""
import datetime
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.database import DatabaseManager

try:
    from .constants import _BUILD_LOCK, EDITION_EVERY, FEED_RECHECK, FEED_SECONDS, FIRST_LOOKBACK, MAX_STORIES
except ImportError:
    from today_constants import _BUILD_LOCK, EDITION_EVERY, FEED_RECHECK, FEED_SECONDS, FIRST_LOOKBACK, MAX_STORIES  # type: ignore[no-redef]

try:
    from .links import _parse, link_key
except ImportError:
    from today_links import _parse, link_key  # type: ignore[no-redef]

try:
    from .recipe import apply_rules, recipe
except ImportError:
    from today_recipe import apply_rules, recipe  # type: ignore[no-redef]


logger = logging.getLogger("AlwaysOnAgent.Today")


class _BuildingMixin:
    # Host state set by Today.__init__ (core/today/__init__.py).
    # Annotation-only declarations: no runtime value exists, so nothing here can
    # shadow anything at runtime. Types mirror Today.__init__.
    db: "DatabaseManager"  # quoted: imported only under TYPE_CHECKING
    images_dir: Path
    now: Callable[[], datetime.datetime]
    fetch: Callable[[str], tuple[bytes, str]]
    feed_fetch: Callable[[str], str] | None

    if TYPE_CHECKING:
        # _with_media and _prune live on _PicturesMixin, which comes AFTER this
        # mixin in Today's MRO (Today(_ReadingMixin, _BuildingMixin, _PicturesMixin)):
        # declared type-only — runtime stubs here would shadow the real methods.
        def _with_media(self, cards: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str | None]]: ...
        def _prune(self, now: datetime.datetime) -> None: ...

    def build_if_due(self, force: bool = False) -> int | None:
        asked = self.now()
        # One at a time, and "due" decided inside the lock: two requests at once (the app opening Today during a
        # pull-to-refresh) both built an edition with the same cards (2026-10-28 audit 0.4)
        with _BUILD_LOCK:
            now = self.now()
            with self.db.get_connection() as conn:
                last = conn.execute("SELECT built_at FROM today_editions ORDER BY id DESC LIMIT 1").fetchone()
            last_at = _parse(last["built_at"]) if last else None
            if last_at is not None and not force and now - last_at < EDITION_EVERY:
                return None
            if force and last_at is not None and last_at >= asked:
                return None                       # built by the request this one waited for: that's the fresh one
            self._read_channels(now, refresh=force)
            cards = apply_rules(self._gather(last_at or now - FIRST_LOOKBACK, now), recipe(self.db)["rules"])
            if not cards:
                return None                       # nothing new: the last edition stays (or none yet)
            return self._store(cards, now)

    def _read_channels(self, now: datetime.datetime, refresh: bool = False) -> None:
        """New uploads from followed channels go into the pool before an edition is put together. Until 2026-10-28 only
        the away shift read them (after the app was closed 2 h, every 6 h), so a channel just followed showed nothing
        and a pull-to-refresh never found a new video. A channel not read yet is read now; on a refresh, the rest if
        their last read is over FEED_RECHECK old; all within FEED_SECONDS."""
        try:
            from core import feeds
            from core.candidates import CandidatePool
            if not feeds.channels(self.db):
                return
            unread = feeds.unread_channels(self.db)
            if not (unread or refresh):
                return
            # Today.__init__ sets feed_fetch to feeds._get_text when none is given,
            # so this only matters for a bare mixin: same fallback here.
            feed_fetch = self.feed_fetch if self.feed_fetch is not None else feeds._get_text
            added = feeds.to_pool(self.db, CandidatePool(self.db, now=self.now), now, fetch=feed_fetch,
                                  force=unread, every=FEED_RECHECK, deadline=time.time() + FEED_SECONDS)
            if added:
                logger.info(f"Today: {added} new video(s) from followed channels")
        except Exception as e:
            logger.info(f"Today: channels not read ({type(e).__name__}: {e})")

    def build_now(self, since: datetime.datetime) -> int | None:
        """A new edition now, from everything since `since` not shown before (the 2026-10-28 backfill: the first
        edition had only a finished-tasks card, from before findings kept their links)."""
        with _BUILD_LOCK:
            now = self.now()
            cards = apply_rules(self._gather(since, now), recipe(self.db)["rules"])
            return self._store(cards, now) if cards else None

    def _gather(self, since: datetime.datetime, now: datetime.datetime) -> list[dict[str, Any]]:
        since_s = since.isoformat()
        with self.db.get_connection() as conn:
            shown_links, shown_refs, findings, settled, question, done = self._gather_queries(conn, since_s, now)
        cards: list[dict[str, Any]] = []
        cards += self._gather_finding_cards(findings, shown_links)
        cards += self._gather_settled_cards(settled, shown_refs)
        cards += self._gather_done_card(done, shown_refs)
        cards += self._gather_question_card(question, shown_refs)
        cards += self._gather_discover_cards(now, shown_refs)
        return cards                              # the recipe's limit (or MAX_CARDS) is applied after

    def _gather_queries(self, conn, since_s: str, now: datetime.datetime):
        """The four source queries plus the already-shown link/ref sets (a fresh database has none of them)."""
        def rows(sql, args=()):
            try:
                return conn.execute(sql, args).fetchall()
            except sqlite3.OperationalError:          # a source this agent hasn't made yet (a fresh database)
                return []
        shown_links = {r[0] for r in conn.execute("SELECT link_key FROM today_items WHERE link_key IS NOT NULL")}
        shown_refs = {r[0] for r in conn.execute("SELECT source_ref FROM today_items WHERE source_ref IS NOT NULL")}
        findings = rows(
            "SELECT id, text, why, topic, ref, url FROM proactive_candidates WHERE source = 'interest' "
            "AND url IS NOT NULL AND created_at > ? AND (status != 'dropped' OR reason LIKE 'repeat of%') "
            "ORDER BY COALESCE(score, 0) DESC, id DESC LIMIT 30", (since_s,))
        settled = rows(
            "SELECT id, text, created_at FROM proactive_messages WHERE topic = 'open-loops' AND created_at > ? "
            "ORDER BY id", (since_s,))
        question = rows(
            "SELECT id, text, why FROM proactive_candidates WHERE kind = 'question' AND status = 'pending' "
            "AND source IN ('curiosity', 'pattern') AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY COALESCE(score, 0) DESC, id DESC LIMIT 5", (now.isoformat(),))
        done = rows(
            "SELECT id, title FROM tasks WHERE phase = 'completed' AND completed_at > ? ORDER BY completed_at DESC "
            "LIMIT 50", (since_s,))
        return shown_links, shown_refs, findings, settled, question, done

    def _gather_finding_cards(self, findings, shown_links) -> list[dict[str, Any]]:
        """Interest findings as story/video cards, skipping ones already shown (and capping the stories)."""
        cards: list[dict[str, Any]] = []
        for f in findings:
            if len([c for c in cards if c["card"] in ("story", "video")]) >= MAX_STORIES:
                break
            key = link_key(f["url"])
            if not key or key in shown_links:
                continue
            shown_links.add(key)
            ref = f"candidate:{f['id']}"
            if (f["ref"] or "").startswith("youtube:"):
                vid = f["ref"].split(":", 1)[1]
                author, _, title = (f["topic"] or "").partition(": ")
                cards.append({"card": "video", "title": title or f["text"], "body": "", "link": f["url"], "link_key": key,
                              "site": author or "YouTube", "why": f["why"], "source_ref": ref,
                              "image_url": f"youtube-thumb:{vid}"})
            else:
                cards.append({"card": "story", "title": "", "body": f["text"], "link": f["url"], "link_key": key,
                              "site": "", "why": f["why"], "source_ref": ref, "preview": True})
        return cards

    def _gather_settled_cards(self, settled, shown_refs) -> list[dict[str, Any]]:
        """Settled open loops as cards, skipping ones already shown."""
        return [{"card": "settled", "title": "Settled", "body": s["text"],
                 "why": "What the agent found out about something you were waiting on",
                 "source_ref": f"message:{s['id']}"}
                for s in settled if f"message:{s['id']}" not in shown_refs]

    def _gather_done_card(self, done, shown_refs) -> list[dict[str, Any]]:
        """Finished tasks as one card, routine ones counted per line ("Post 1 pin ×3")."""
        if not done:
            return []
        ids = ",".join(str(d["id"]) for d in done)
        if f"tasks:{ids}"[:200] in shown_refs:
            return []
        # The routine ones repeat ("Post 1 pin to Pinterest" three times a day): one line each, counted
        counts: dict[str, int] = {}
        for d in done:
            t = d["title"] or f"Task {d['id']}"
            counts[t] = counts.get(t, 0) + 1
        lines = [f"{t} ×{n}" if n > 1 else t for t, n in counts.items()]
        return [{"card": "done", "title": f"{len(done)} finished",
                 "body": "\n".join(lines[:6] + ([f"and {len(lines) - 6} more"] if len(lines) > 6 else [])),
                 "why": "Background tasks that finished", "source_ref": f"tasks:{ids}"[:200]}]

    def _gather_question_card(self, question, shown_refs) -> list[dict[str, Any]]:
        """The first pending question not already shown, as one card."""
        q = next((q for q in question if f"candidate:{q['id']}" not in shown_refs), None)
        if q is None:
            return []
        return [{"card": "question", "title": "A question", "body": q["text"], "why": q["why"] or "",
                 "source_ref": f"candidate:{q['id']}"}]

    def _gather_discover_cards(self, now: datetime.datetime, shown_refs) -> list[dict[str, Any]]:
        """Once a day: something interesting (when the recipe asks for it) and a trivia question (core/discover.py)."""
        try:
            from core import discover
            from core.config import get_config
            r = recipe(self.db)
            return discover.daily_cards(
                self.fetch, now, shown_refs,
                interesting=discover.wants_interesting(r["text"], r["rules"]["more"]),
                want_trivia=r["rules"]["show"].get("trivia", True), nasa_key=get_config("NASA_API_KEY"))
        except Exception as e:
            logger.info(f"Today: no discover cards ({type(e).__name__}: {e})")
            return []

    def _store(self, cards: list[dict[str, Any]], now: datetime.datetime) -> int:
        header: dict[str, Any] = {"date": now.date().isoformat(), "count": len(cards)}
        self.images_dir.mkdir(parents=True, exist_ok=True)
        prepared = self._with_media(cards)
        # The edition and its cards together, after the downloads: a reader in between saw an edition with some of
        # its cards (audit 0.4)
        with self.db.get_connection() as conn:
            ed = conn.execute("INSERT INTO today_editions (built_at, header_json) VALUES (?, ?)",
                              (now.isoformat(), json.dumps(header))).lastrowid
            assert ed is not None  # INSERT always sets it
            for pos, (c, image) in enumerate(prepared):
                conn.execute(
                    "INSERT INTO today_items (edition, position, card, title, body, link, link_key, site, image, why, "
                    "source_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ed, pos, c["card"], (c.get("title") or "")[:200], (c.get("body") or "")[:1200],
                     c.get("link") or None, c.get("link_key") or None, (c.get("site") or "")[:80] or None, image,
                     (c.get("why") or "")[:300] or None, c.get("source_ref"), now.isoformat()))
        self._prune(now)
        logger.info(f"Today: edition {ed} with {len(cards)} card(s)")
        return ed
