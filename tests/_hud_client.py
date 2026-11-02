"""
Test helper: a FastAPI TestClient whose requests appear to come from a chosen IP.
Starlette's TestClient always reports the client host as 'testclient', which the
Web HUD (correctly) does not treat as loopback, so tests set the address explicitly.
"""

from fastapi.testclient import TestClient

LOCAL_ADDR = ("127.0.0.1", 50000)
LAN_ADDR = ("192.168.1.50", 50000)


def client_from(app, addr=LOCAL_ADDR, **kwargs) -> TestClient:
    async def asgi(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, client=addr)
        await app(scope, receive, send)
    # A local tool names this PC in its Host (http://127.0.0.1:8080); TestClient's default "testserver" would be a
    # rebinding page's own name, which gets no loopback trust (auth.is_local_client, audit 1.1)
    kwargs.setdefault("base_url", "http://127.0.0.1")
    return TestClient(asgi, **kwargs)
