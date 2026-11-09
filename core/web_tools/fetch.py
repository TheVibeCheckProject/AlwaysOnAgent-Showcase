"""Safe fetching (web_tools §2). Split out of core/web_tools.py 2026-11-03.

Every request goes through _get(): a page can't make the agent fetch an address on your home
network or the Mini itself, not by its name, a redirect, or the address it really connects to.
"""
import http.client
import ipaddress
import json
import socket
import urllib.parse
import urllib.request
from typing import Any


def _wt():
    """core.web_tools, looked up at call time. Tests patch names on that module (w._get, w._geocode,
    w._assert_public_host, ...), so every call to a former web_tools function goes through it here;
    a direct import-time binding would silently ignore the patch."""
    try:
        from core import web_tools
        return web_tools
    except ImportError:  # running from core/ directly
        import web_tools  # type: ignore[no-redef]
        return web_tools


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AlwaysOnAgent/1.0 (personal assistant)"
TIMEOUT = 15
MAX_PAGE_BYTES = 2_000_000


def _assert_public_host(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("only public http(s) addresses can be fetched")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror:
        raise ValueError(f"can't resolve {parts.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if not ip.is_global:
            raise ValueError("that address is on this PC or the local network, which is off limits")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _wt()._assert_public_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _assert_peer_public(sock: socket.socket) -> None:
    """
    Checks the address actually connected to. The name is looked up again when connecting, so a
    host that answers with a public address for the check and this PC for the connection (DNS
    rebinding) is caught here, before any request is sent.
    """
    ip = ipaddress.ip_address(str(sock.getpeername()[0]).split("%")[0])
    if not ip.is_global:
        sock.close()
        raise ValueError("that address is on this PC or the local network, which is off limits")


class _PeerCheckedHTTP(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        _assert_peer_public(self.sock)


class _PeerCheckedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        _assert_peer_public(self.sock)


class _CheckedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PeerCheckedHTTP, req)


class _CheckedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        ctx = getattr(self, "_context", None)
        return self.do_open(_PeerCheckedHTTPS, req, context=ctx)


_opener = urllib.request.build_opener(_SafeRedirects)
# For addresses the model chose (web pages): every connection, redirects included, must be public
_checked_opener = urllib.request.build_opener(_SafeRedirects, _CheckedHTTPHandler, _CheckedHTTPSHandler)


def _get(url: str, data: bytes | None = None, check_host: bool = False, headers: dict[str, str] | None = None) -> tuple[bytes, str]:
    if check_host:
        _wt()._assert_public_host(url)
    req_headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers)
    with (_checked_opener if check_host else _opener).open(req, timeout=TIMEOUT) as resp:
        return resp.read(MAX_PAGE_BYTES), resp.headers.get_content_type() or ""


def _get_json(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
    if headers:
        try:
            body, _ = _wt()._get(url, data=data, headers=headers)
        except TypeError:
            body, _ = _wt()._get(url, data=data)
    else:
        body, _ = _wt()._get(url, data=data)
    return json.loads(body.decode("utf-8", "replace"))
