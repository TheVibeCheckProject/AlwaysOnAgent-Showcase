"""
Procedural memory: how things got done (docs/IDEAS.md #25)
==========================================================
Facts are about the owner; recipes are about the agent's own work: "when X, do Y", kept by area (a tool, a
project, an engine, or a kind of job) and handed to the next chat turn or task in that area.

Where recipes come from:
- owner:  the owner teaches a how-to in chat ("when you make a video, keep the caption short"). Picked up by
          the fact extraction call (no extra call), grounded in the owner's own words. Trusted from the start.
- tool:   a chat tool call that returned an error, then worked. One background call distils a lesson.
- task:   a background task that failed, was retried and then succeeded.
- seed:   added by hand (the owner in DeskAI, or a script).

Fail-safes, because a wrong recipe is repeated in every later job of its area:
- A learned recipe (tool, task) is saved only if a second AI agrees the evidence shows it
  (MemoryManager.second_opinion, the check deletes use, IDEAS #11). Disagreement or no answer: not saved.
- Every use is scored: when a recipe went into a turn or task, a clean outcome counts as helped and an error
  in its area as failed. A learned recipe that fails RETIRE_AFTER_FAILS times and more often than it helped is
  retired (kept, no longer used). The owner's own and pinned recipes are never retired automatically.
- Only the best few go into a prompt (MAX_PER_PROMPT, under BUDGET_TOKENS): ranked by closeness to the
  request plus track record.
- A near-duplicate updates the recipe it matches (with the same second opinion) instead of piling up.
- A learned recipe is know-how, never a copy of the job it came from (the owner, 2026-10-11: "a task isn't
  copied step by step... I don't want a repeat Notepad call wanting to read a grocery list"). The distil prompt
  and the second opinion say so, and copy_problem() refuses one that repeats the job's own details (its file
  names, quoted text, long numbers) or is a script of steps.
"""

import builtins
import json
import logging
import os
import re
from collections.abc import Sequence
from typing import Any

import numpy as np

try:
    from core.config import redact_secrets
    from core.database import get_iso_now
except ImportError:
    from config import redact_secrets  # type: ignore[no-redef]
    from database import get_iso_now  # type: ignore[no-redef]

logger = logging.getLogger("Procedures")

SOURCES = ("owner", "seed", "tool", "task")
LEARNED = ("tool", "task")             # what a second opinion must confirm, and what can be retired
# What goes into the CLOUD model's prompt per turn (finding them is local and free; the store has no limit).
# Each line rides on every matching turn against the chat model's small daily quota. Starting values, to be
# set by the eval (scripts/eval_memory.py --only procedures); overridable in .env.
MAX_PER_PROMPT = int(os.environ.get("PROCEDURES_PER_PROMPT", "4"))
BUDGET_TOKENS = int(os.environ.get("PROCEDURES_BUDGET_TOKENS", "400"))
RETIRE_AFTER_FAILS = 3
MAX_TEXT = 400

DISTIL_PROMPT = (
    "You help a personal assistant learn from its own work. Below is something that went wrong and was then "
    "done right. Write ONE short reusable lesson so it goes right the first time next time, or answer that "
    "there is none: a one-off (a typo, a temporary outage, a missing file that's now there) teaches nothing.\n"
    "The lesson must follow from what is shown; never invent steps, commands or names that aren't in it.\n"
    "It must be general know-how for the next job of this KIND, never a copy of this job: no file names, quoted "
    "text or values from it, and not its steps in order. Good: \"to read a text file, open it by its path; no "
    "need to start Notepad first\". Bad: \"open Notepad, open Groceries.txt, read the items\".\n"
    'Respond with JSON only: {"lesson": null} or {"lesson": {"when": "the situation, in a few words", '
    '"do": "what to do, one or two sentences"}}'
)


def _clip(text: Any, n: int = MAX_TEXT) -> str:
    return " ".join(redact_secrets(str(text or "")).split())[:n]


def _area(text: Any) -> str:
    """A short lowercase label: 'tool:web_research', 'project:website', 'tiktok videos'."""
    t = re.sub(r"[^a-z0-9:_\- ]+", "", str(text or "").lower()).strip()
    return re.sub(r"\s+", " ", t)[:40] or "general"


class Procedures:
    """The recipe store, on the memory's database, embedder and second opinion."""

    def __init__(self, memory: Any):
        self.mem = memory
        self.db = memory.db
        self.log = getattr(memory, "log", logger)
        conn = self.db.get_connection()
        with conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS mem_procedures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                area TEXT NOT NULL,                 -- 'tool:<name>', 'project:<name>', 'engine:<name>' or a job
                situation TEXT NOT NULL,            -- when it applies
                steps TEXT NOT NULL,                -- what to do
                source TEXT NOT NULL,               -- owner | seed | tool | task
                source_ref TEXT,                    -- the message id or task id it came from
                evidence TEXT,                      -- the owner's words, or what went wrong and how it was fixed
                status TEXT NOT NULL DEFAULT 'active',      -- active | retired
                pinned INTEGER NOT NULL DEFAULT 0,
                uses INTEGER NOT NULL DEFAULT 0,
                helped INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                embedding BLOB,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mem_procedures_area ON mem_procedures (status, area);
            """)

    # ------------------------------------------------------------------ storage
    @staticmethod
    def text_of(row: dict[str, Any]) -> str:
        return f"{row['situation']}: {row['steps']}"

    def get(self, pid: int) -> dict[str, Any] | None:
        row = self.db.get_connection().execute("SELECT * FROM mem_procedures WHERE id = ?", (int(pid),)).fetchone()
        return dict(row) if row else None

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mem_procedures" + (" WHERE status = ?" if status else "") + " ORDER BY id"
        return [dict(r) for r in self.db.get_connection().execute(sql, (status,) if status else ())]

    def _vectors(self, rows: builtins.list[dict[str, Any]]) -> np.ndarray | None:
        missing = [r for r in rows if r.get("embedding") is None]
        if missing:
            vecs = self.mem._embed([self.text_of(r) for r in missing], wait=True)
            if vecs is not None:
                conn = self.db.get_connection()
                with conn:
                    conn.executemany("UPDATE mem_procedures SET embedding = ? WHERE id = ?",
                                     [(v.astype(np.float32).tobytes(), r["id"]) for v, r in zip(vecs, missing)])
                for v, r in zip(vecs, missing):
                    r["embedding"] = v.astype(np.float32).tobytes()
        if not rows or any(r.get("embedding") is None for r in rows):
            return None
        return np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])

    def add(self, area: str, situation: str, steps: str, source: str, source_ref: Any = None,
            evidence: str = "", pinned: bool = False) -> tuple[int | None, bool]:
        """Saves a recipe, or updates the one it duplicates. Returns (id, created); (None, False) if empty."""
        area, situation, steps = _area(area), _clip(situation, 160), _clip(steps)
        if len(situation) < 3 or len(steps) < 8:
            return None, False
        source = source if source in SOURCES else "seed"
        now = get_iso_now()
        same = self._same_as(area, situation, steps)
        conn = self.db.get_connection()
        if same is not None:
            # The owner's newer word replaces the old steps; a learned duplicate only confirms the recipe
            with conn:
                if source in ("owner", "seed"):
                    conn.execute("UPDATE mem_procedures SET situation = ?, steps = ?, source = ?, source_ref = ?, "
                                 "evidence = ?, status = 'active', embedding = NULL, updated_at = ? WHERE id = ?",
                                 (situation, steps, source, str(source_ref or ""), _clip(evidence, 600), now, same))
                else:
                    conn.execute("UPDATE mem_procedures SET status = 'active', updated_at = ? WHERE id = ?", (now, same))
            return same, False
        with conn:
            cur = conn.execute(
                "INSERT INTO mem_procedures (area, situation, steps, source, source_ref, evidence, pinned, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (area, situation, steps, source, str(source_ref or ""), _clip(evidence, 600), int(pinned), now, now))
        self.log.info(f"Procedure #{cur.lastrowid} saved ({source}, {area}): {situation} -> {steps}")
        return cur.lastrowid, True

    def _same_as(self, area: str, situation: str, steps: str) -> int | None:
        """The active recipe in this area that says the same thing (a second opinion decides, as for facts)."""
        rows = [r for r in self.list("active") if r["area"] == area]
        if not rows:
            return None
        text = f"{situation}: {steps}"
        for r in rows:
            if re.sub(r"\W+", " ", self.text_of(r).lower()).strip() == re.sub(r"\W+", " ", text.lower()).strip():
                return r["id"]
        mat, q = self._vectors(rows), self.mem._embed([text], wait=True)
        if mat is None or q is None:
            return None
        sims = mat @ q[0]
        best = int(np.argmax(sims))
        if sims[best] < self.mem.DUPLICATE_SIM - 0.05:
            return None
        agree, _ = self.mem.second_opinion(
            f"Saved recipe A ({area}): \"{self.text_of(rows[best])}\"\nNew recipe B: \"{text}\"\n\nProposed: treat B as "
            "the same recipe as A (an update of it), not a separate one. Are they about the same situation?")
        return rows[best]["id"] if agree else None

    def update(self, pid: int, **fields: Any) -> dict[str, Any] | None:
        """Owner edits: situation, steps, area, pinned, status ('active' | 'retired')."""
        sets: list[str] = []
        params: list[Any] = []
        for key in ("situation", "steps"):
            if fields.get(key):
                sets.append(f"{key} = ?")
                params.append(_clip(fields[key], 160 if key == "situation" else MAX_TEXT))
        if fields.get("area"):
            sets.append("area = ?")
            params.append(_area(fields["area"]))
        if fields.get("pinned") is not None:
            sets.append("pinned = ?")
            params.append(int(bool(fields["pinned"])))
        if fields.get("status") in ("active", "retired"):
            sets.append("status = ?")
            params.append(fields["status"])
        if not sets:
            return self.get(pid)
        if any(s.startswith(("situation", "steps")) for s in sets):
            sets.append("embedding = NULL")
        conn = self.db.get_connection()
        with conn:
            cur = conn.execute(f"UPDATE mem_procedures SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
                               params + [get_iso_now(), int(pid)])
        return self.get(pid) if cur.rowcount else None

    def delete(self, pid: int) -> bool:
        conn = self.db.get_connection()
        with conn:
            return bool(conn.execute("DELETE FROM mem_procedures WHERE id = ?", (int(pid),)).rowcount)

    # ------------------------------------------------------------------ using them
    def relevant(self, query: str, areas: Sequence[str] = (), k: int = MAX_PER_PROMPT,
                 budget: int = BUDGET_TOKENS, qvec: np.ndarray | None = None) -> builtins.list[dict[str, Any]]:
        """The few recipes worth sending with this request: those of the given areas, and any close in meaning to
        it, best first (closeness plus track record), within the token budget. qvec: the query's vector, if the
        caller has it already."""
        rows = self.list("active")
        if not rows:
            return []
        area_set = {_area(a) for a in areas if a}
        mat = self._vectors(rows)
        if qvec is None and query:
            q = self.mem._embed([query], query=True, wait=True)
            qvec = q[0] if q is not None else None
        floor = getattr(self.mem, "VEC_MIN_PROCEDURE", 0.5)
        scored = []
        for i, r in enumerate(rows):
            sim = float(mat[i] @ qvec) if mat is not None and qvec is not None else 0.0
            in_area = r["area"] in area_set
            if not in_area and sim < floor:
                continue
            trust = (0.3 if r["pinned"] else 0) + (0.15 if r["source"] == "owner" else 0) \
                + 0.05 * min(r["helped"], 6) - 0.15 * r["failed"]
            scored.append((sim + (0.5 if in_area else 0) + trust, r))
        scored.sort(key=lambda x: -x[0])
        try:
            from core.memory import count_tokens
        except ImportError:
            from memory import count_tokens  # type: ignore[no-redef]
        out: list[dict[str, Any]] = []
        used = 0
        for _, r in scored[:k * 2]:
            t = count_tokens(self.text_of(r))
            if len(out) >= k or used + t > budget:
                break
            out.append(r)
            used += t
        return out

    @staticmethod
    def block(rows: builtins.list[dict[str, Any]], heading: str = "### How you've done this before") -> str:
        if not rows:
            return ""
        return heading + " (learned recipes: follow them unless the request says otherwise)\n" + "\n".join(
            f"- When {r['situation'].rstrip('.')}: {r['steps']}" for r in rows)

    def record_outcome(self, rows: builtins.list[dict[str, Any]], outcomes: dict[int, bool | None]) -> None:
        """outcomes: {recipe id: True (helped), False (failed), None (used, no signal)}. Retires a learned recipe that
        keeps failing."""
        if not rows:
            return
        conn = self.db.get_connection()
        now = get_iso_now()
        with conn:
            for r in rows:
                ok = outcomes.get(r["id"])
                conn.execute("UPDATE mem_procedures SET uses = uses + 1, helped = helped + ?, failed = failed + ?, "
                             "last_used_at = ? WHERE id = ?", (int(ok is True), int(ok is False), now, r["id"]))
        for r in rows:
            fresh = self.get(r["id"])
            if fresh and fresh["source"] in LEARNED and not fresh["pinned"] and fresh["status"] == "active" \
                    and fresh["failed"] >= RETIRE_AFTER_FAILS and fresh["failed"] > fresh["helped"]:
                self.update(fresh["id"], status="retired")
                self.log.info(f"Procedure #{fresh['id']} retired: failed {fresh['failed']}x, helped {fresh['helped']}x")

    def chat_outcomes(self, rows: builtins.list[dict[str, Any]], trace: builtins.list[dict[str, Any]]) -> dict[int, bool | None]:
        """A tool recipe helped if its tool ran without an error this turn, failed if it errored; others: no signal."""
        out: dict[int, bool | None] = {}
        for r in rows:
            if not r["area"].startswith("tool:"):
                out[r["id"]] = None
                continue
            tool = r["area"][5:]
            results = [str(t.get("result") or "") for t in trace or [] if t.get("name") == tool]
            out[r["id"]] = None if not results else not any(x.startswith("Error") for x in results)
        return out

    # ------------------------------------------------------------------ learning
    def _distil_and_check(self, area: str, what_happened: str, purpose: str) -> dict[str, str] | None:
        out = self.mem._bg_chat([{"role": "system", "content": DISTIL_PROMPT},
                                 {"role": "user", "content": what_happened}],
                                purpose, max_tokens=300, temperature=0.1, json_mode=True, timeout=40)
        try:
            from core.memory import _parse_json_object
        except ImportError:
            from memory import _parse_json_object  # type: ignore[no-redef]
        lesson = (_parse_json_object(out) or {}).get("lesson")
        if not isinstance(lesson, dict) or not lesson.get("when") or not lesson.get("do"):
            return None
        copied = copy_problem(lesson, what_happened)
        if copied:
            self.log.info(f"Procedure not saved ({area}): {copied}: {lesson}")
            return None
        agree, reason = self.mem.second_opinion(
            f"{what_happened}\n\nProposed recipe for the area '{area}', to be reused in later work: when "
            f"{lesson['when']}, {lesson['do']}\n\nDoes what happened above clearly support this recipe, and is it "
            "reusable rather than a one-off? It must be general know-how, not a copy of this job's steps or of its "
            "particular files, names, values or text: answer no if it is.")
        if not agree:
            self.log.info(f"Procedure not saved ({area}): the second opinion "
                          + (f"disagreed: {reason}" if agree is False else "couldn't be had") + f": {lesson}")
            return None
        return {"when": str(lesson["when"]), "do": str(lesson["do"])}

    @staticmethod
    def recoveries(trace: builtins.list[dict[str, Any]]) -> builtins.list[tuple[str, builtins.list[dict[str, Any]]]]:
        """(tool, the calls from its first error to its first success after it) for each tool that recovered."""
        out: list[tuple[str, list[dict[str, Any]]]] = []
        for name in dict.fromkeys(t.get("name") for t in trace or []):
            if name is None:
                continue
            calls = [t for t in trace if t.get("name") == name]
            first_err = next((i for i, t in enumerate(calls) if str(t.get("result") or "").startswith("Error")), None)
            if first_err is None:
                continue
            fixed = next((i for i in range(first_err + 1, len(calls))
                          if not str(calls[i].get("result") or "").startswith("Error")), None)
            # The same call simply worked again (a timeout, a locked database): nothing was done differently, so
            # there's nothing to learn. The eval's "retry when the weather tool times out" came from that.
            if fixed is not None and _same_args(calls[first_err].get("arguments"), calls[fixed].get("arguments")):
                continue
            if fixed is not None:
                out.append((name, calls[first_err:fixed + 1]))
        return out

    def learn_from_trace(self, user_text: str, trace: builtins.list[dict[str, Any]], msg_id: int | None = None) -> int:
        """Recipes from tool calls that failed and then worked in one chat turn. Returns how many were saved."""
        saved = 0
        for tool, calls in self.recoveries(trace):
            shown = "\n".join(f"{i + 1}. {tool}({_clip(c.get('arguments'), 300)}) -> {_clip(c.get('result'), 300)}"
                              for i, c in enumerate(calls))
            happened = (f"The user asked: \"{_clip(user_text, 300)}\"\nThe assistant's calls to the tool {tool}, "
                        f"in order (the first failed, the last worked):\n{shown}")
            lesson = self._distil_and_check(f"tool:{tool}", happened, "procedure")
            if lesson:
                saved += self.add(f"tool:{tool}", lesson["when"], lesson["do"], "tool", source_ref=msg_id,
                                  evidence=happened)[1]
        return saved

    def learn_from_task(self, task: dict[str, Any], earlier_failure: str, final_output: str) -> bool:
        """A recipe from a task that failed, was retried and then succeeded."""
        area = task_area(task)
        happened = (f"A background task ({area}): \"{_clip(task.get('title'), 120)}\"\nInstruction: "
                    f"{_clip(task.get('prompt'), 500)}\nThe earlier attempt failed: {_clip(earlier_failure, 700)}\n"
                    f"The retry succeeded: {_clip(final_output, 500)}")
        lesson = self._distil_and_check(area, happened, "procedure")
        if not lesson:
            return False
        return self.add(area, lesson["when"], lesson["do"], "task", source_ref=task.get("id"),
                        evidence=happened)[1]


# A file's name (Groceries.txt, report_2026.pdf); quoted text; a number of 3+ digits
_FILE_NAME = re.compile(r"\b[\w\-]+\.(?:txt|md|docx?|pdf|csv|xlsx?|json|png|jpe?g|gif|html?|py|log|zip|mp4)\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“']([^\"”']{6,})[\"”']")
_LONG_NUMBER = re.compile(r"\b\d{3,}\b")
# Separators between steps: "then", "; ", "1." ... A lesson with more than MAX_STEPS steps is a script.
_STEP_BREAK = re.compile(r"\bthen\b|;|(?:^|\s)\d+[.)]\s", re.IGNORECASE)
MAX_STEPS = 3


def copy_problem(lesson: dict[str, Any], what_happened: str) -> str | None:
    """Why a learned lesson is a copy of the job it came from, or None. A copy repeats the job's own details (a
    file name, quoted text or a long number that's in what happened) or lists its steps (more than MAX_STEPS).
    Deterministic, before the second opinion: the owner's rule (2026-10-11), "a task isn't copied step by step"."""
    text = f"{lesson.get('when', '')} {lesson.get('do', '')}"
    source = what_happened or ""
    low_source = source.lower()
    for name in _FILE_NAME.findall(text):
        if name.lower() in low_source:
            return f"it names the job's own file ({name})"
    for quoted in _QUOTED.findall(text):
        if quoted.strip().lower() in low_source:
            return f"it repeats the job's own text ({quoted.strip()[:40]!r})"
    for number in _LONG_NUMBER.findall(text):
        if re.search(rf"\b{number}\b", source):
            return f"it repeats the job's own value ({number})"
    steps = len(_STEP_BREAK.findall(str(lesson.get("do", "")))) + 1
    if steps > MAX_STEPS:
        return f"it's a script of {steps} steps, not know-how"
    return None


def _same_args(a: Any, b: Any) -> bool:
    def parsed(x: Any) -> Any:
        try:
            return json.loads(x) if isinstance(x, str) else x
        except ValueError:
            return " ".join(str(x).split())
    return parsed(a) == parsed(b)


def task_area(task: dict[str, Any]) -> str:
    meta = task.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    if meta.get("project"):
        return f"project:{meta['project']}"
    return f"engine:{(task.get('engine') or 'auto').lower()}"
