"""docs/API.md is the contract DeskAI is built against (IDEAS #21): every API route the app serves is documented
there, and every documented one exists. Path parameters are compared without their names ({id} == {fact_id})."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.rstrip("/"))


def _served():
    # From the app's own OpenAPI schema: the same on every FastAPI version (0.141 on the Mini keeps included
    # routers as wrapper objects in app.routes, so walking app.routes found almost nothing there)
    from interfaces.hud.server import app
    return {(m.upper(), _norm(path)) for path, ops in app.openapi()["paths"].items() if path.startswith("/api")
            for m in ops if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE")}


def _documented(text: str):
    return {(m, _norm(p)) for m, p in re.findall(r"`(GET|POST|PUT|PATCH|DELETE) (/api/[^`?\s]*)", text)}


def test_every_route_is_documented_and_every_documented_route_exists():
    served, documented = _served(), _documented((ROOT / "docs" / "API.md").read_text(encoding="utf-8"))
    assert not served - documented, f"served but not in docs/API.md: {sorted(served - documented)}"
    assert not documented - served, f"in docs/API.md but not served: {sorted(documented - served)}"


def test_deskais_copy_of_the_contract_matches_when_it_is_next_to_this_repo():
    deskai = ROOT.parent / "DeskAI" / "docs" / "API.md"
    if not deskai.is_file():                  # the Mini has no DeskAI checkout
        return
    ours = _documented((ROOT / "docs" / "API.md").read_text(encoding="utf-8"))
    theirs = _documented(deskai.read_text(encoding="utf-8"))
    assert ours == theirs, f"DeskAI's docs/API.md differs: only ours {sorted(ours - theirs)}, only theirs {sorted(theirs - ours)}"
