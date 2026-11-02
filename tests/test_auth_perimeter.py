"""The API's perimeter (interfaces/hud/auth.py), 2026-10-28 audit 1.1, 1.5, 1.7."""
import pytest

from core.database import DatabaseManager
from interfaces.hud import app as hud_app
from interfaces.hud import state as hud_state
from tests._hud_client import LAN_ADDR, LOCAL_ADDR, client_from


@pytest.fixture
def token(tmp_path):
    original_db, original_token = hud_state.db, hud_state.HUD_AUTH_TOKEN
    hud_state.db = DatabaseManager(tmp_path / "hud.db")
    hud_state.HUD_AUTH_TOKEN = "perimeter-token-123"
    yield hud_state.HUD_AUTH_TOKEN
    hud_state.db, hud_state.HUD_AUTH_TOKEN = original_db, original_token


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "127.0.0.1:8080"])
def test_the_owners_own_tools_on_this_pc_need_no_token(token, host):
    client = client_from(hud_app, addr=LOCAL_ADDR, base_url=f"http://{host}")
    assert client.get("/api/tasks").status_code == 200
    assert client.get("/api/status").status_code == 200        # heartbeat, deploy and apply_update call it so


def test_a_rebinding_page_on_this_pc_gets_no_loopback_trust(token):
    """1.1: a page at evil.example:8080 that switched its DNS to 127.0.0.1 sends same-origin requests with no Origin,
    from 127.0.0.1, but with its own name in Host. It could read memory, logs and private repos."""
    page = client_from(hud_app, addr=LOCAL_ADDR, base_url="http://evil.example:8080")
    for path in ("/api/memory", "/api/logs", "/api/tasks", "/api/status"):
        assert page.get(path).status_code == 401, path
    assert page.get("/api/tasks", headers={"X-HUD-Token": token}).status_code == 200


def test_status_needs_the_token_from_elsewhere_and_health_doesnt(token):
    """1.5: /api/status told anyone on the LAN or tailnet the version, uptime, model, load and task count."""
    phone = client_from(hud_app, addr=LAN_ADDR, base_url="http://192.168.1.20:8080")
    assert phone.get("/api/status").status_code == 401
    assert phone.get("/api/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert phone.get("/api/health").status_code == 200


def test_a_non_ascii_token_is_refused_not_a_server_error(token):
    """1.7: compare_digest on a non-ASCII str raised TypeError: a 500 instead of a 401."""
    phone = client_from(hud_app, addr=LAN_ADDR, base_url="http://192.168.1.20:8080")
    # As bytes: httpx won't encode a non-ASCII str header itself; the server reads it back as "pässwörd"
    assert phone.get("/api/tasks", headers={"X-HUD-Token": "pässwörd".encode("latin-1")}).status_code == 401
