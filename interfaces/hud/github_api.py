"""DeskAI's repo browser (2026-10-06): the owner's GitHub repos like a file explorer, and "Work on this".

Browsing is read-only and goes through Connectors (`github.py`), which uses the `gh` sign-in already on this
computer: no token is kept by the agent. "Work on this" starts an AgentWork job on the repo; a repo that isn't an
AgentWork project yet is registered first, on the spot (the owner no longer adds projects by hand). The job works
on its own branch, as every AgentWork job does, and goes live only on the owner's tap.

Endpoints (docs/API.md "Repos"):
  GET  /api/github/repos                     {"repos": [{repo, description, private, updated, default_branch, url, project}]}
  GET  /api/github/tree?repo=o/r&path=dir    {"repo", "path", "entries": [{name, path, type: dir|file, size}]}
  GET  /api/github/file?repo=o/r&path=f      {"repo", "path", "name", "size", "url", "text" | null, "note" | null}
  POST /api/github/work {repo, instruction, path?}   {"status": "success", "task_id", "project", "registered"}
"""
import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from core import connectors
from interfaces.hud import common, state

router = APIRouter()

REPOS_CACHE_SECONDS = 120          # the list barely changes, and listing takes a few seconds (two gh calls)
_repos_cache: dict[str, Any] = {"at": 0.0, "repos": None}


class GitHubWorkRequest(BaseModel):
    repo: str = Field(..., max_length=200)                  # owner/name
    instruction: str = Field(..., max_length=4000)
    path: str | None = Field(None, max_length=500)       # the folder or file the owner was looking at


def _norm(url: str) -> str:
    """A repo address in one form, to match GitHub repos with AgentWork projects ('owner/name', lowercase)."""
    u = (url or "").strip().lower().removesuffix("/").removesuffix(".git")
    return u.split("github.com/", 1)[1] if "github.com/" in u else u


def _projects_by_repo() -> dict[str, str]:
    return {_norm(p.get("repo") or ""): p["name"] for p in state.get_agentwork().list_projects()}


def _call(fn, *args):
    try:
        return fn(*args)
    except connectors.ConnectorError as e:
        raise HTTPException(status_code=502, detail=f"GitHub: {e}")


@router.get("/api/github/repos")
def github_repos(refresh: bool = False):
    if refresh or _repos_cache["repos"] is None or time.time() - _repos_cache["at"] > REPOS_CACHE_SECONDS:
        _repos_cache.update(at=time.time(), repos=_call(connectors.github_repos))
    projects = _projects_by_repo()
    return {"repos": [{**r, "project": projects.get(_norm(r["repo"]))} for r in _repos_cache["repos"]]}


@router.get("/api/github/tree")
def github_tree(repo: str, path: str = ""):
    return _call(connectors.github_tree, repo, path)


@router.get("/api/github/file")
def github_file(repo: str, path: str):
    return _call(connectors.github_file, repo, path)


SHARE_TYPES = {"zip": "application/zip", "text": "text/plain; charset=utf-8"}


@router.get("/api/github/download")
def github_download(repo: str, path: str = "", format: str = "zip"):
    """A file or folder to share from the phone (2026-10-10, the owner: "share a file or a folder with another person
    or AI without copying a whole code and pasting it"): a file as itself, a folder as a zip or as one text file
    (each file under its path, for AI chats that can't open a zip). Left-out files: the X-Left-Out header."""
    if format not in ("zip", "text", "raw"):
        raise HTTPException(status_code=400, detail="format is zip, text or raw")
    folder = Path(state.db.db_path).parent / "tmp" / "share"     # beside the database: data/ (tests: their own)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / uuid.uuid4().hex
    got = _call(connectors.github_download, repo, path, format, str(out))
    name = got.get("name") or out.name
    media = SHARE_TYPES.get(format) if name.endswith((".zip", ".txt")) else \
        (mimetypes.guess_type(name)[0] or "application/octet-stream")
    if media and media.startswith("text/") and "charset" not in media:
        media += "; charset=utf-8"
    return FileResponse(out, media_type=media, filename=name, background=BackgroundTask(out.unlink, missing_ok=True),
                        headers={"X-Left-Out": str(len(got.get("skipped") or [])),
                                 "X-Files": str(got.get("files") or 0)})


def _new_project_name(repo: str, taken: list[str]) -> str:
    owner, _, name = repo.partition("/")
    base = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")[:41] or "repo"
    if base not in taken:
        return base
    return re.sub(r"[^a-z0-9_-]+", "-", f"{base}-{owner.lower()}")[:41]


@router.post("/api/github/work")
def github_work(req: GitHubWorkRequest, idempotency_key: str | None = common.IdempotencyKey,
                task_source: str | None = common.TaskSourceHeader):
    existing = common.task_for_key(idempotency_key)
    if existing:
        return {"status": "success", "task_id": existing, "duplicate": True}
    instruction = req.instruction.strip()
    if len(instruction) < 8:
        raise HTTPException(status_code=400, detail="Describe the change in a sentence (at least 8 characters).")
    repo = req.repo.strip().strip("/")
    found = next((r for r in (_repos_cache["repos"] or _call(connectors.github_repos)) if _norm(r["repo"]) == _norm(repo)),
                 None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{repo} isn't one of your GitHub repos.")
    projects = _projects_by_repo()
    project, registered = projects.get(_norm(repo)), False
    if project is None:
        desc = found.get("description") or f"{repo} on GitHub"
        try:
            made = state.get_agentwork().add_project(_new_project_name(repo, list(projects.values())),
                                                     f"https://github.com/{found['repo']}.git", desc,
                                                     base=found.get("default_branch") or "main")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Couldn't set up {repo} for work: {e}")
        project, registered = made["name"], True
        state.db.append_log("INFO", f"[WebHUD] Registered {repo} as AgentWork project '{project}' (repo browser)")
    if req.path and req.path.strip("/"):
        instruction += f"\n\n(The owner was looking at `{req.path.strip('/')}` in the repo when asking.)"
    task_id = common.queue_instruction(instruction, project, how=f"on {repo} from DeskAI's repo browser",
                                       source=common.clean_source(task_source, default="DeskAI Repos"))
    common.remember_key(idempotency_key, task_id)
    return {"status": "success", "task_id": task_id, "project": project, "registered": registered}
