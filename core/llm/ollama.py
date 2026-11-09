"""The local Ollama client
=======================
A small client for a local Ollama server: is it up, which model, plain text out.

Split out of core/llm.py (now the core/llm/ package) §8 on 2026-11-03; core/llm.py re-exports OllamaClient
for backwards compatibility.
"""
import json
import logging
import urllib.request

try:
    from core.config import get_config
except ImportError:
    from config import get_config  # type: ignore[no-redef]


class OllamaClient:
    """The local Ollama server: the offline fallback for coding tasks and affirmations."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        default_model: str = "qwen2.5-coder:3b",
        logger: logging.Logger | None = None
    ):
        self.host = host.rstrip("/")
        self.default_model = default_model
        self.logger = logger or logging.getLogger("OllamaClient")

    @classmethod
    def from_config(cls, logger: logging.Logger | None = None) -> "OllamaClient":
        """The server and model set in .env (OLLAMA_HOST, OLLAMA_MODEL)."""
        return cls(host=get_config("OLLAMA_HOST", "http://localhost:11434"),
                   default_model=get_config("OLLAMA_MODEL", "qwen2.5-coder:3b"), logger=logger)

    def is_alive(self) -> tuple[bool, list[str]]:
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", headers={"User-Agent": "AlwaysOnAgent"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                return True, models
        except (OSError, ValueError, AttributeError, TypeError):
            return False, []

    def select_best_model(self, requested_model: str | None = None) -> str:
        if requested_model:
            return requested_model
        alive, models = self.is_alive()
        if not alive:
            return self.default_model
        priority = ["qwen2.5-coder:3b", "qwen3:8b", "hermes3:8b", "hermes3"]
        for p in priority:
            for m in models:
                if p in m:
                    return m
        return models[0] if models else self.default_model

    def generate(self, prompt: str, timeout: int = 90, model: str | None = None, temperature: float = 0.8,
                 max_tokens: int = 200) -> str | None:
        """Plain text from the local model, or None if Ollama is unreachable or fails."""
        payload = {"model": model or self.select_best_model(), "prompt": prompt, "stream": False,
                   "options": {"temperature": temperature, "num_predict": max_tokens}}
        try:
            req = urllib.request.Request(f"{self.host}/api/generate", data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json", "User-Agent": "AlwaysOnAgent"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return (json.loads(resp.read().decode("utf-8")).get("response") or "").strip() or None
        except (OSError, ValueError, AttributeError, TypeError) as e:
            self.logger.warning(f"[ollama] Text generation failed: {e}")
            return None
