"""The guards in conftest.py hold: no test can reach a paid service with the real keys, whatever HTTP library it
uses. 2026-10-28 audit 5.1: two chat tests called real Gemini through the google-genai SDK (httpx) at every deploy on
the Mini, spending the chat's free daily requests; the guard only covered urllib."""
import os

import httpx
import pytest

from core import config


@pytest.mark.parametrize("key", ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "CLOUDFLARE_API_TOKEN",
                                 "MISTRAL_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "HEALTHCHECK_URL"])
def test_no_real_service_key_is_visible_to_tests(key):
    assert not os.environ.get(key) and not config.get_config(key)


def test_httpx_cannot_reach_a_model_host():
    with pytest.raises(RuntimeError, match="real AI model"):
        httpx.post("https://generativelanguage.googleapis.com/v1beta/models/x:generateContent", json={})
