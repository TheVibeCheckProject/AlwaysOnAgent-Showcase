"""Looking at the Mini's own files from chat: find, list, read, and show (images, video, audio, PDFs) in DeskAI.

2026-10-27, Alex: "I want the assistant to be able to display images or mp4, or wave files etc in chat if i ask for
them." Before this, the chat's file tools saw only the agent's own code (read_project_file), so asked for a computer
run's screenshots it guessed folders. This is step 1 of fuller computer access, and it only LOOKS: nothing here
changes, moves or deletes a file. Changes will need Alex's tap and the app open (the 2026-10-27 background audit,
"full computer access").

Where it may look: ROOTS (the user's folder and C:\\Projects; MINI_FILE_ROOTS in .env, ";"-separated, overrides).
Never: secrets (.env, keys, certificates, databases, anything named secret/credential/token/password), hidden (dot)
folders and files (tools keep sign-ins there: .kaggle, .config, .claude), browser profiles and app data (logins,
cookies), SSH/GPG keys, the data/ and secrets/ folders of any copy of the agent. A path that resolves outside the
roots (.., links) is refused.

A file to show is served by GET /api/files/m/{ref}/{name} (interfaces/hud/files_api.py): ref is the path, URL-safe
base64 encoded, checked again there. DeskAI loads it with the token in a header like any image.
"""
import base64
import datetime
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

try:
    from core.config import get_config
except ImportError:
    from config import get_config  # type: ignore[no-redef]

DENY_NAMES = re.compile(r"(^\.env)|(\.(db|db-wal|db-shm|sqlite|pem|key|pfx|p12|crt|keystore|jks|kdbx|ppk)$)|"
                        r"(secret|credential|token|password|cookie|login data|web data)|(^id_(rsa|ed25519|ecdsa|dsa))",
                        re.IGNORECASE)
DENY_DIRS = {"appdata", ".ssh", ".gnupg", ".git", "secrets", ".aws", ".azure", ".kube", ".docker", "$recycle.bin",
             "node_modules", "__pycache__", ".venv", "venv", ".cache"}
# Other agents' data folders that hold what the owner typed (its own data/ and secrets/: see _denied_parts)
AGENT_DIRS = ("agentcomputeruse\\data\\requests", "agentcomputeruse\\data\\answers")
MEDIA = {".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image", ".webp": "image", ".bmp": "image",
         ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video", ".avi": "video", ".m4v": "video",
         ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".ogg": "audio", ".flac": "audio", ".aac": "audio",
         ".pdf": "document"}
TEXT = {".txt", ".md", ".py", ".json", ".log", ".csv", ".yaml", ".yml", ".ini", ".cfg", ".toml", ".html", ".css",
        ".js", ".ts", ".kt", ".kts", ".xml", ".ps1", ".bat", ".sh", ".srt", ".vtt", ".jsonl"}
KIND_WORDS = {"image": "image", "images": "image", "picture": "image", "pictures": "image", "photo": "image",
              "photos": "image", "screenshot": "image", "screenshots": "image", "video": "video", "videos": "video",
              "audio": "audio", "sound": "audio", "sounds": "audio", "song": "audio", "songs": "audio",
              "document": "document", "documents": "document", "pdf": "document"}
MAX_SHOW_BYTES = 500 * 1024 * 1024
WHOLE_READ_MAX_BYTES = 2 * 1024 * 1024   # text files larger than this are read by window (read_text)
FIND_SECONDS, FIND_MAX_FILES = 6.0, 200_000
# Searched first: where a person's own files are. 2026-10-27, the first live search walked the folders in order, ran
# out of time and returned test leftovers (aoa-aside\pt_deploy) instead of real videos.
PREFERRED = ("Desktop", "Downloads", "Pictures", "Videos", "Music", "Documents", "OneDrive",
             "C:/Projects/TiktokVideos", "C:/Projects/AgentGallery", "C:/Projects/AgentComputerUse/data/runs")
# Never worth walking: tests' scratch folders, caches, build output, installed packages, AI models
SKIP_DIRS = re.compile(r"^(pytest-.*|pt_.*|aoa-aside|\.?te?mp|\.pytest_cache|site-packages|dist-packages|\.gradle|"
                       r"\.android|\.m2|\.nuget|\.npm|\.cargo|\.rustup|\.vscode|\.idea|build|intermediates|"
                       r"\.ollama|ollama|models|anaconda3|miniconda3|scoop|\.tools|go)$", re.IGNORECASE)


class FileAccessError(Exception):
    pass


def roots() -> list[Path]:
    raw = (get_config("MINI_FILE_ROOTS") or "").strip()
    paths = [Path(p.strip()) for p in raw.split(";") if p.strip()] if raw else [Path.home(), Path("C:/Projects")]
    return [p.resolve() for p in paths if p.exists()]


def _denied_parts(p: Path) -> str | None:
    low = str(p).lower()
    if any(a in low for a in AGENT_DIRS):
        return "the agent's own data"
    # The agent's data and secrets, in any copy of it (AlwaysOnAgent-Lite, a clone in Downloads): memory, backups, keys.
    # Its code stays readable through read_project_file.
    names = [part.lower() for part in p.parts]
    for i, name in enumerate(names[:-1]):
        if name.startswith("alwaysonagent") and names[i + 1] in ("data", "secrets"):
            return "the agent's own data"
    # Only the parts below the root that holds it: a root may itself sit under a folder that's off limits elsewhere
    parts = p.parts
    for r in roots():
        if p == r or r in p.parents:
            parts = p.relative_to(r).parts
            break
    for part in parts:
        if part.startswith("."):
            # Dot-folders and dot-files hold sign-ins and API keys the name rules below don't catch
            # (~\.kaggle\kaggle.json, ~\.config\gh\hosts.yml, ~\.claude.json; 2026-10-28 audit 0.1)
            return "hidden folders and files are off limits"
        if part.lower() in DENY_DIRS:
            return f"'{part}' folders are off limits"
        if DENY_NAMES.search(part):
            return "secrets are off limits"
    return None


def check(path: str) -> Path:
    """The real, allowed path, or FileAccessError saying why not."""
    raw = (path or "").strip().strip('"')
    if not raw:
        raise FileAccessError("no path given")
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute():
        p = roots()[0] / p if roots() else p
    try:
        p = p.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise FileAccessError(f"nothing at {raw}")
    if not any(p == r or r in p.parents for r in roots()):
        raise FileAccessError(f"{p} is outside the folders I can look in ({'; '.join(str(r) for r in roots())})")
    why = _denied_parts(p)
    if why:
        raise FileAccessError(why)
    return p


def kind_of(p: Path) -> str:
    return MEDIA.get(p.suffix.lower(), "text" if p.suffix.lower() in TEXT else "other")


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _when(t: float) -> str:
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def _query_terms(query: str, kind: str) -> tuple[list[str], set, Optional["re.Pattern[str]"]]:
    """The query split into name words, kinds (from kind words in the query or the kind argument), and an
    optional * pattern. "screen shots" said as two words is the screenshots kind (2026-10-27: it was searched
    as two name words)."""
    q = re.sub(r"\bscreen\s+shots?\b", "screenshots", (query or "").lower())
    words = [w for w in re.findall(r"[\w.*-]+", q)]
    kinds = {KIND_WORDS[w] for w in words if w in KIND_WORDS} | ({kind.lower()} if kind else set())
    words = [w for w in words if w not in KIND_WORDS]
    pattern = re.compile("^" + ".*".join(re.escape(w).replace(r"\*", ".*") for w in words) + "$") \
        if any("*" in w for w in words) else None
    return words, kinds, pattern


def _search_starts(folder: str) -> list[Path]:
    """Where to walk: the given folder, or the preferred folders first, then every root."""
    if folder:
        return [check(folder)]
    home = Path.home()
    preferred = [(Path(p) if ":" in p else home / p) for p in PREFERRED]
    return [p.resolve() for p in preferred if p.is_dir() and any(p.resolve() == r or r in p.resolve().parents
                                                                   for r in roots())] + roots()


def _walk_one(start: Path, words: list[str], kinds: set, pattern: Optional["re.Pattern[str]"],
              deadline: float, walked: set[str], scanned: int) -> tuple[list[tuple[float, Path]], int, bool]:
    """Walks one start folder, returning (matches, files scanned, stopped early on time or count)."""
    found: list[tuple[float, Path]] = []
    for dirpath, dirnames, filenames in os.walk(start):
        walked.add(dirpath.lower())
        dirnames[:] = [d for d in dirnames if d.lower() not in DENY_DIRS and not d.startswith(".")
                       and not SKIP_DIRS.match(d) and os.path.join(dirpath, d).lower() not in walked
                       and not _denied_parts(Path(dirpath) / d)]
        for name in filenames:
            scanned += 1
            low = name.lower()
            if DENY_NAMES.search(low) or name.startswith("."):
                continue
            if pattern is not None:
                if not pattern.match(low):
                    continue
            elif words and not all(w in low for w in words):
                continue
            p = Path(dirpath) / name
            if kinds and kind_of(p) not in kinds:
                continue
            try:
                found.append((p.stat().st_mtime, p))
            except OSError:
                pass
        if time.time() > deadline or scanned > FIND_MAX_FILES:
            return found, scanned, True
    return found, scanned, False


def find(query: str, folder: str = "", kind: str = "", limit: int = 20) -> list[dict[str, str]]:
    """Files whose names hold every word of the query (or match a * pattern), newest first. kind: image, video,
    audio, document, text, or "" for any; kind words in the query ("videos", "screenshots") count as a kind."""
    words, kinds, pattern = _query_terms(query, kind)
    starts = _search_starts(folder)
    found: list[tuple[float, Path]] = []
    walked: set[str] = set()
    deadline, scanned = time.time() + FIND_SECONDS, 0
    for start in starts:
        if str(start).lower() in walked:
            continue
        batch, scanned, stopped = _walk_one(start, words, kinds, pattern, deadline, walked, scanned)
        found.extend(batch)
        if stopped:
            break
    found.sort(reverse=True)
    return [{"path": str(p), "kind": kind_of(p), "size": _size(p.stat().st_size), "modified": _when(t)}
            for t, p in found[:limit]]


def listing(folder: str, limit: int = 60) -> dict[str, list[str]]:
    p = check(folder)
    if not p.is_dir():
        raise FileAccessError(f"{p} is a file, not a folder")
    dirs, files = [], []
    for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
        if c.name.startswith(".") or _denied_parts(c):
            continue
        try:
            if c.is_dir():
                dirs.append(c.name + "\\")
            else:
                st = c.stat()
                files.append(f"{c.name} ({_size(st.st_size)}, {_when(st.st_mtime)})")
        except OSError:
            pass
    return {"folder": [str(p)], "folders": dirs[:limit], "files": files[:limit]}


def read_text(path: str, start: int = 0, chars: int = 4000) -> str:
    p = check(path)
    if p.is_dir():
        raise FileAccessError(f"{p} is a folder")
    if kind_of(p) != "text":
        raise FileAccessError(f"{p.name} isn't a text file: show it instead")
    size = p.stat().st_size
    if size > WHOLE_READ_MAX_BYTES:
        # A big log: only the window asked for is read, by byte offset (2026-10-28 audit 0.3: a multi-GB log was read
        # whole into the agent's memory to return 4,000 characters)
        offset = max(0, min(int(start or 0), size))
        with p.open("rb") as f:
            f.seek(offset)
            raw = f.read(chars * 4)
        part = raw.decode("utf-8", errors="replace")[:chars]
        end = offset + len(part.encode("utf-8", errors="replace"))
        return part + (f"\n[...about {size - end:,} more bytes; read again from {end}]" if end < size else "")
    text = p.read_text(encoding="utf-8", errors="replace")
    part = text[max(0, start):max(0, start) + chars]
    more = f"\n[...{len(text) - start - len(part)} more characters; read again from {start + len(part)}]" \
        if start + len(part) < len(text) else ""
    return part + more


def ref_for(p: Path) -> str:
    return base64.urlsafe_b64encode(str(p).encode("utf-8")).decode("ascii").rstrip("=")


def path_from_ref(ref: str) -> Path:
    try:
        raw = base64.urlsafe_b64decode(ref + "=" * (-len(ref) % 4)).decode("utf-8")
    except Exception:
        raise FileAccessError("not a file link")
    return check(raw)


def show_line(path: str) -> str:
    """A markdown line DeskAI shows: an image inline, or a card that plays video/audio or opens a PDF."""
    p = check(path)
    k = kind_of(p)
    if k not in ("image", "video", "audio", "document"):
        raise FileAccessError(f"{p.name} isn't an image, video, audio file or PDF: read it instead")
    size = p.stat().st_size
    if size > MAX_SHOW_BYTES:
        raise FileAccessError(f"{p.name} is {_size(size)}: too big to send to the phone (limit {_size(MAX_SHOW_BYTES)})")
    url = f"/api/files/m/{ref_for(p)}/{quote(p.name)}"
    if k == "image":
        return f"![{p.name}]({url})"
    icon = {"video": "▶", "audio": "🔊", "document": "📄"}[k]
    return f"[{icon} {p.name} ({_size(size)})]({url})"
