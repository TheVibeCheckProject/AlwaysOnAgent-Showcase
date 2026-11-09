"""
Access control. Without HUD_AUTH_TOKEN only this machine (loopback) may use the API. With it set, any client
presenting the token may: in the Authorization: Bearer header (DeskAI), the X-HUD-Token header, or ?token=
on the URL. ?token= is deprecated (2026-10-07): a token in a URL ends up in logs, chat history and shared
links. It still works so older DeskAI builds keep loading media; the app now sends the header, and a media link
that must leave the app (share, external player) is a short-lived signed link (POST /api/media/link) instead.
"""

import hashlib
import hmac
import logging
import re
import time
from urllib.parse import quote, unquote, urlsplit

from fastapi import Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from interfaces.hud import state

LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOCAL_HOST_NAMES = {"127.0.0.1", "localhost", "::1"}       # the Host a local tool's request names
# Tailscale Serve (the HTTPS address, 2026-10-27) relays tailnet devices from 127.0.0.1 too; its own headers say so
# even if a version leaves out X-Forwarded-For, so they never get this PC's no-token trust
FORWARDING_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip", "tailscale-user-login",
                      "tailscale-headers-info", "x-forwarded-host", "x-forwarded-proto")
# No CORS (2026-10-04): DeskAI is a native app and never needs it. Wildcard preflights on chat, videos and images
# let any web page a browser on this PC opened send the trusted-loopback API commands (chat's /task among them).


# Signed media links: the media an app can't fetch with a header (a video handed to an external player, a link
# shared to another app). HMAC-SHA256 over "<path>\n<expiry>" with a key derived from HUD_AUTH_TOKEN, so there
# is no new secret to set, and changing the token voids every link. Only GET/HEAD of a single media file.
MEDIA_LINK_PATH = re.compile(r"^/(?:videos|images)/[^/]+$|^/api/videos/[^/]+/thumbnail$|^/api/tasks/[^/]+/thumb$")
MEDIA_LINK_DEFAULT_TTL = 24 * 3600
MEDIA_LINK_MAX_TTL = 7 * 24 * 3600
_LINK_KEY_LABEL = b"alwaysonagent-media-link-v1"


def _media_link_key() -> bytes:
    return hmac.new(state.HUD_AUTH_TOKEN.encode("utf-8"), _LINK_KEY_LABEL, hashlib.sha256).digest()


def _media_link_sig(path: str, expires: int) -> str:
    return hmac.new(_media_link_key(), f"{path}\n{expires}".encode(), hashlib.sha256).hexdigest()


def media_link_path(url_or_path: str) -> str | None:
    """The decoded path of a media file a signed link may point at, or None if it isn't one."""
    path = unquote(urlsplit((url_or_path or "").strip()).path or "")
    if ".." in path or "\\" in path or not MEDIA_LINK_PATH.match(path):
        return None
    return path


def sign_media_link(url_or_path: str, ttl_seconds: int = MEDIA_LINK_DEFAULT_TTL, now: float | None = None):
    """(relative signed url, expiry epoch seconds) for a media file, or None when it isn't a media path or the
    server has no HUD_AUTH_TOKEN (then only this machine can use the API and no link is needed)."""
    path = media_link_path(url_or_path)
    if path is None or not state.HUD_AUTH_TOKEN:
        return None
    ttl = max(60, min(int(ttl_seconds or MEDIA_LINK_DEFAULT_TTL), MEDIA_LINK_MAX_TTL))
    expires = int(now if now is not None else time.time()) + ttl
    return f"{quote(path)}?exp={expires}&sig={_media_link_sig(path, expires)}", expires


def media_link_valid(request: Request, now: float | None = None) -> bool:
    if not state.HUD_AUTH_TOKEN or request.method not in ("GET", "HEAD"):
        return False
    sig, exp = request.query_params.get("sig"), request.query_params.get("exp")
    path = request.url.path
    if not sig or not exp or not exp.isdigit() or not MEDIA_LINK_PATH.match(path) or ".." in path:
        return False
    expires, current = int(exp), int(now if now is not None else time.time())
    if expires < current or expires > current + MEDIA_LINK_MAX_TTL + 60:
        return False
    return hmac.compare_digest(sig, _media_link_sig(path, expires))


def install_log_redaction(*logger_names: str) -> None:
    """Hides tokens (the HUD token's value anywhere, and ?token=/&sig= in any URL) in these loggers' records.
    uvicorn's access log prints each request's full URL, query string included."""
    from core.config import LogRedactionFilter
    for name in logger_names or ("uvicorn", "uvicorn.access", "uvicorn.error", "WebHUD"):
        target = logging.getLogger(name)
        if not any(isinstance(f, LogRedactionFilter) for f in target.filters):
            target.addFilter(LogRedactionFilter())


def token_matches(candidate: str | None) -> bool:
    # Bytes, not str: compare_digest raises TypeError on a non-ASCII str, which was a 500 instead of a 401 (audit 1.7)
    if not state.HUD_AUTH_TOKEN or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), state.HUD_AUTH_TOKEN.encode("utf-8"))


def is_local_client(request: Request) -> bool:
    # A reverse proxy or tunnel on this PC connects from 127.0.0.1 but relays outside visitors
    if any(h in request.headers for h in FORWARDING_HEADERS):
        return False
    # A browser's cross-site request (it sends Origin) comes from some web page, not the owner's own tools,
    # even when the browser runs on this PC: it needs the token like anyone else
    if "origin" in request.headers:
        return False
    # The owner's tools address this PC by name (127.0.0.1, localhost). A page whose own name was switched to
    # 127.0.0.1 (DNS rebinding) sends same-origin requests with no Origin, but under its own name: no trust for it
    # (2026-10-28 audit 1.1)
    if (request.url.hostname or "").lower() not in LOCAL_HOST_NAMES:
        return False
    return bool(request.client and request.client.host in LOCAL_CLIENT_HOSTS)


def extract_auth_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth:
        return auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
    x_token = request.headers.get("x-hud-token")
    if x_token:
        return x_token.strip()
    query_token = request.query_params.get("token")
    return query_token.strip() if query_token else None


def verify_token(
    authorization: str | None = Header(None),
    x_hud_token: str | None = Header(None),
    token: str | None = Query(None)
) -> bool:
    """Dependency helper for FastAPI endpoints requiring authentication."""
    if not state.HUD_AUTH_TOKEN:
        return True
    extracted = None
    if authorization:
        extracted = authorization[7:].strip() if authorization.lower().startswith("bearer ") else authorization.strip()
    elif x_hud_token:
        extracted = x_hud_token.strip()
    elif token:
        extracted = token.strip()
    if not token_matches(extracted):
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid HUD_AUTH_TOKEN")
    return True


def auth_rejection(request: Request):
    """Returns a response that short-circuits the request, or None to let it through."""
    path = request.url.path

    # A bare liveness check. /api/status (version, uptime, model, load, tasks) needs the token like the rest: DeskAI
    # sends it, and the agent's own checks (heartbeat, deploy, apply_update) call it from this PC (audit 1.5)
    if path == "/api/health":
        return None

    # Loopback is always trusted so the workstation can never be locked out
    if is_local_client(request) or token_matches(extract_auth_token(request)):
        return None

    # A signed, unexpired link to one media file (see sign_media_link)
    if media_link_valid(request):
        return None

    host = request.client.host if request.client else "unknown"
    state.logger.warning(f"Rejected unauthenticated request from {host}: {request.method} {path}")
    return JSONResponse({"detail": "Unauthorized"}, status_code=401)


class HUDAuthMiddleware:
    """Pure ASGI middleware (not BaseHTTPMiddleware) so the SSE stream and its disconnect detection are untouched."""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            rejection = auth_rejection(Request(scope))
            if rejection is not None:
                await rejection(scope, receive, send)
                return
        await self.app(scope, receive, send)
