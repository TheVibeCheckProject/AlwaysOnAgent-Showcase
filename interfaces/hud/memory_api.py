"""The assistant's memory (sync handlers: embedding / LLM calls run in FastAPI's threadpool)."""

import sqlite3

from fastapi import APIRouter, HTTPException, Query

from core.llm import CloudLLM
from core.llm_usage import UsageLog
from core.memory import CATEGORIES
from core.memory_review import MemoryReview
from core.open_loops import OpenLoops
from interfaces.hud import common, state
from interfaces.hud.models import (
    FactCreateRequest,
    FactPatchRequest,
    MemoryReviewAnswer,
    ProcedureCreateRequest,
    ProcedurePatchRequest,
)

router = APIRouter()


def _open_loops(mem) -> list:
    """Memories waiting on something (core/open_loops.py), or [] if they can't be read: the Memory screen must
    still open."""
    try:
        return [{k: l.get(k) for k in ("fact_id", "content", "waiting_on", "check_after", "checked_at", "asked_at")}
                for l in OpenLoops(mem.db, mem, connectors_mod=False).open_loops()]
    except (OSError, sqlite3.Error, RuntimeError):
        return []


@router.get("/api/memory")
def memory_overview(
    query: str | None = Query(None),
    q: str | None = Query(None)
):
    """Knowledge & context retrieval endpoint. Returns search results if query is set, or full overview."""
    search_term = (query or q or "").strip()
    mem = state.get_memory()
    if search_term:
        return {
            "query": search_term,
            "facts": [common.public_fact(f) for f in mem.search_facts(search_term, k=25)],
            "messages": mem.search_archive(search_term, k=10, compressed_only=False),
            "stats": mem.stats(),
        }
    # "facts" is what the agent believes now. Until 2026-10-13 it also held every replaced and faded version, and
    # DeskAI (which has no status field) showed five "waiting on the permit decision" facts as current after the decision
    # was saved. Old versions are in "past", newest first.
    every = [common.public_fact(f) for f in mem.list_facts(limit=2000, include_faded=True)]
    return {
        "stats": mem.stats(),
        "profile": mem.get_profile(),
        "facts": [f for f in every if f.get("status") == "active" and not f.get("invalid_from")],
        "past": sorted((f for f in every if f.get("status") != "active" or f.get("invalid_from")),
                       key=lambda f: f.get("updated_at") or "", reverse=True),
        # Memories waiting on something, checked daily (core/open_loops.py): {fact_id, waiting_on, check_after, ...}
        "open_loops": _open_loops(mem),
        "summaries": mem.list_summaries(),
        "categories": list(CATEGORIES),
    }


@router.get("/api/memory/search")
def memory_search(q: str = ""):
    q = q.strip()
    if not q:
        return {"facts": [], "messages": []}
    mem = state.get_memory()
    return {
        "facts": [common.public_fact(f) for f in mem.search_facts(q, k=25)],
        "messages": mem.search_archive(q, k=10, compressed_only=False),
    }


@router.get("/api/memory/conversation")
def memory_conversation(before_id: int | None = None, limit: int = 50):
    """The shared conversation, a page at a time (oldest first); pass before_id for older pages."""
    mem = state.get_memory()
    msgs = mem.list_messages(before_id=before_id, limit=max(1, min(limit, 200)))
    has_more = bool(msgs) and bool(mem.list_messages(before_id=msgs[0]["id"], limit=1))
    return {"messages": msgs, "has_more": has_more}


@router.post("/api/memory/facts")
def memory_add_fact(req: FactCreateRequest):
    mem = state.get_memory()
    fid, created = mem.add_fact(req.content, category=req.category, importance=req.importance,
                                pinned=req.pinned, source="hud")
    fact = mem.get_fact(fid)
    assert fact is not None  # just created above
    return {"status": "success", "id": fid, "created": created, "fact": common.public_fact(fact)}


@router.patch("/api/memory/facts/{fact_id}")
def memory_edit_fact(fact_id: int, req: FactPatchRequest):
    mem = state.get_memory()
    if not mem.get_fact(fact_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    if req.content is not None:
        mem.update_fact(fact_id, req.content, supersede=False)
    if req.pinned is not None:
        mem.set_pinned(fact_id, req.pinned)
    mem.set_fact_fields(fact_id, importance=req.importance, category=req.category, status=req.status)
    fact = mem.get_fact(fact_id)
    assert fact is not None  # existence checked at the top of this handler
    return {"status": "success", "fact": common.public_fact(fact)}


@router.delete("/api/memory/facts/{fact_id}")
def memory_forget_fact(fact_id: int, erase: bool = True):
    """erase=true also removes the information from chat history and summaries (in the background)."""
    if not state.get_memory().forget_fact(fact_id, scrub=erase):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "success", "id": fact_id, "erased_everywhere": erase}


# ------------------------------------------------------------------ learned recipes (core/procedures.py)
def _public_procedure(p: dict) -> dict:
    return {"id": p["id"], "area": p["area"], "when": p["situation"], "do": p["steps"], "source": p["source"],
            "source_ref": p["source_ref"] or None, "evidence": p["evidence"] or None, "status": p["status"],
            "pinned": bool(p["pinned"]), "uses": p["uses"], "helped": p["helped"], "failed": p["failed"],
            "created_at": p["created_at"], "updated_at": p["updated_at"], "last_used_at": p["last_used_at"]}


@router.get("/api/memory/procedures")
def memory_procedures(status: str | None = Query(None, pattern="^(active|retired)$")):
    """Learned recipes ("when X, do Y"), with where each came from and its track record."""
    return {"procedures": [_public_procedure(p) for p in state.get_memory().procedures.list(status)]}


@router.post("/api/memory/procedures")
def memory_add_procedure(req: ProcedureCreateRequest):
    procs = state.get_memory().procedures
    pid, created = procs.add(req.area, req.when, req.do, "owner", evidence="Added in DeskAI", pinned=req.pinned)
    if pid is None:
        raise HTTPException(status_code=400, detail="Say when it applies and what to do.")
    proc = procs.get(pid)
    assert proc is not None  # just created above
    return {"status": "success", "created": created, "procedure": _public_procedure(proc)}


@router.patch("/api/memory/procedures/{procedure_id}")
def memory_edit_procedure(procedure_id: int, req: ProcedurePatchRequest):
    p = state.get_memory().procedures.update(procedure_id, area=req.area, situation=req.when, steps=req.do,
                                             pinned=req.pinned, status=req.status)
    if p is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return {"status": "success", "procedure": _public_procedure(p)}


@router.delete("/api/memory/procedures/{procedure_id}")
def memory_delete_procedure(procedure_id: int):
    if not state.get_memory().procedures.delete(procedure_id):
        raise HTTPException(status_code=404, detail="Recipe not found")
    return {"status": "success", "id": procedure_id}


@router.post("/api/memory/profile/rebuild")
def memory_rebuild_profile():
    mem = state.get_memory()
    if not mem.rebuild_profile():
        raise HTTPException(status_code=503, detail="The AI service is unavailable; try again shortly.")
    return {"status": "success", "profile": mem.get_profile()}


def _review() -> MemoryReview:
    mem = state.get_memory()
    return MemoryReview(mem.db, mem, llm=None)          # answering needs no AI


@router.get("/api/memory/review")
def memory_review_pending():
    """The weekly memory check waiting for the owner (core/memory_review.py), or {"pending": null}.
    Items: {"n", "op": "add"|"retire", "content", "category"?, "quote"?, "fact_id"?, "reason"?}."""
    return {"pending": _review().pending()}


@router.post("/api/memory/review")
def memory_review_answer(req: MemoryReviewAnswer):
    """Keeps the numbered items (saves the new facts, retires the out-of-date ones); the rest are dropped."""
    try:
        return {"status": "success", **_review().answer(req.keep)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/memory/usage")
def memory_usage(hours: int = 24):
    """LLM token usage: prompt vs cached tokens per purpose, and the latest chat prompt's breakdown."""
    return UsageLog(state.get_memory().db).summary(hours=max(1, min(hours, 24 * 30)))


@router.get("/api/memory/budget")
def memory_budget():
    """Daily free AI budget breakdown by model and provider reset countdowns, plus how chat fared over the last
    24 hours (share answered by the best model, failures and why) and each Gemini model's learned daily limit."""
    log = UsageLog(state.get_memory().db)
    out = log.daily_budget_summary()
    out["chat_health"] = log.model_health(24, best=CloudLLM.CHAT_MODELS[0])
    for m in out["by_model"]:
        if m["provider"] == "gemini":
            m["since_reset"] = log.calls_since_reset("gemini", m["model"])
            m["learned_daily_limit"] = log.daily_limit("gemini", m["model"]) or None
    return out
