"""
Local text embeddings for semantic memory search
================================================
Runs BGE-small-en-v1.5 (MIT licence, int8-quantized ONNX, ~34 MB) on the CPU with onnxruntime.
The model is downloaded once from Hugging Face into data/models/ and never leaves the machine.

If the model can't be loaded (offline on first run, missing package), embed() returns None and
callers fall back to keyword search. Loading is retried at most every 10 minutes.
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, List, Optional

import numpy as np

logger = logging.getLogger("LocalEmbedder")
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models"


class LocalEmbedder:
    MODEL_ID = "bge-small-en-v1.5-int8"     # stored vectors are tagged with this (memory re-embeds on a change)
    REPO = "Xenova/bge-small-en-v1.5"
    MODEL_FILE = "onnx/model_quantized.onnx"
    TOKENIZER_FILE = "tokenizer.json"
    # BGE retrieval: queries get this instruction prefix, stored passages don't
    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
    DIM = 384
    MAX_TOKENS = 512
    BATCH = 32
    RETRY_SECONDS = 600

    def __init__(self, cache_dir: Path = MODELS_DIR):
        self.cache_dir = Path(cache_dir)
        self._session = None
        self._tokenizer = None
        self._input_names = set()
        self._lock = threading.Lock()
        self._failed_at = 0.0

    @property
    def ready(self) -> bool:
        return self._session is not None

    def ensure_loaded(self) -> bool:
        """Download (first time only) and load the model. Thread-safe; cheap once loaded."""
        if self._session is not None:
            return True
        with self._lock:
            if self._session is not None:
                return True
            if self._failed_at and time.time() - self._failed_at < self.RETRY_SECONDS:
                return False
            try:
                import warnings
                warnings.filterwarnings("ignore", module="huggingface_hub")
                warnings.filterwarnings("ignore", message=".*unauthenticated requests to the HF Hub.*")
                for noisy_lib in ("httpx", "httpcore", "huggingface_hub", "urllib3"):
                    logging.getLogger(noisy_lib).setLevel(logging.WARNING)

                import onnxruntime as ort
                from huggingface_hub import hf_hub_download
                from tokenizers import Tokenizer

                self.cache_dir.mkdir(parents=True, exist_ok=True)
                try:
                    # Attempt local cached files first (instant, 0 network, no warnings)
                    model_path = hf_hub_download(self.REPO, self.MODEL_FILE, cache_dir=str(self.cache_dir), local_files_only=True)
                    tok_path = hf_hub_download(self.REPO, self.TOKENIZER_FILE, cache_dir=str(self.cache_dir), local_files_only=True)
                except Exception:
                    model_path = hf_hub_download(self.REPO, self.MODEL_FILE, cache_dir=str(self.cache_dir))
                    tok_path = hf_hub_download(self.REPO, self.TOKENIZER_FILE, cache_dir=str(self.cache_dir))

                tokenizer = Tokenizer.from_file(tok_path)
                tokenizer.enable_truncation(max_length=self.MAX_TOKENS)
                tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2          # leave the CPU to the rest of the agent
                session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
                self._input_names = {i.name for i in session.get_inputs()}
                self._tokenizer, self._session = tokenizer, session
                if os.environ.get("SUPERVISOR_ACTIVE") != "1":
                    logger.info(f"Local semantic memory model ready ({self.REPO}).")
                return True
            except Exception as e:
                self._failed_at = time.time()
                logger.warning(f"Local embedding model unavailable, using keyword search only: {e}")
                return False

    def start_background_load(self) -> None:
        threading.Thread(target=self.ensure_loaded, name="EmbedderLoad", daemon=True).start()

    def embed(self, texts: List[str], query: bool = False, wait: bool = True) -> Optional[np.ndarray]:
        """
        Unit-length float32 vectors, shape (len(texts), 384), so a dot product is cosine similarity.
        wait=False returns None instead of blocking on a first-time download.
        """
        if not texts:
            return np.zeros((0, self.DIM), dtype=np.float32)
        if self._session is None and (not wait or not self.ensure_loaded()):
            return None
        prefix = self.QUERY_PREFIX if query else ""
        out = []
        for i in range(0, len(texts), self.BATCH):
            encs = self._tokenizer.encode_batch([prefix + (t or "") for t in texts[i:i + self.BATCH]])
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, feeds)[0]
            cls = hidden[:, 0, :]                       # BGE uses the [CLS] vector
            cls = cls / np.maximum(np.linalg.norm(cls, axis=1, keepdims=True), 1e-12)
            out.append(cls.astype(np.float32))
        return np.vstack(out)


class OllamaEmbedder:
    """
    Memory search on Qwen3-Embedding 0.6B through the Mini's Ollama (docs/IDEAS.md #15). Benchmarked on 21 real-style
    questions over the owner's facts (2026-10-04): right fact first 18/21 and in the top 3 21/21, against 14 and
    19 for bge-small; bigger models (bge-base/large, Qwen3-Embedding 4B) did no better. ~76 ms a query on the CPU.
    Same interface as LocalEmbedder. If Ollama isn't answering it tries to start it; meanwhile embed() returns None
    and memory search falls back to keywords (logged).
    """
    MODEL_ID_PREFIX = "ollama:"
    QUERY_PREFIX = ("Instruct: Given a question from a user to their personal assistant, retrieve the remembered "
                    "fact about the user that answers it\nQuery: ")
    # Once memory knows the owner's name, facts are written with it ("Alex lives in…"), and the model has to be told
    # that "I" in a question is that person: without it, first-person search fell from 21/21 to 15/21 (2026-10-04)
    QUERY_PREFIX_NAMED = ("Instruct: Given a question from {name} to their personal assistant, retrieve the "
                          "remembered fact about {name} that answers it. In the question, I, me and my mean "
                          "{name}.\nQuery: ")
    BATCH = 32
    TIMEOUT = 60
    RETRY_SECONDS = 60
    # Used on every message: keep it loaded. 639 MB is the file; loaded it takes ~2.4 GB of RAM (measured on the
    # Mini 2026-10-07: Ollama /api/ps size 2.37 GB, llama-server ~2.9 GB private, context 4096, CPU only).
    KEEP_ALIVE = "24h"
    NUM_THREAD = 4                # of 12: leave the CPU to renders and the API
    # Memory's similarity cut-offs on this model's scale (core/memory.py has the bge-small ones). Measured
    # 2026-10-04 on the owner's facts and messages: wrong facts score ~0.30 (bge 0.40), right ones ~0.51 (bge 0.52);
    # duplicates >= 0.92, real updates >= 0.80, different subjects <= 0.50.
    THRESHOLDS = {"VEC_MIN_FACT": 0.37, "VEC_MIN_FACT_KEYWORD": 0.30, "VEC_MIN_ARCHIVE": 0.40,
                  "VEC_MIN_ARCHIVE_KEYWORD": 0.25, "VEC_MIN_SUMMARY": 0.32, "VEC_MIN_SCRUB": 0.22,
                  "DUPLICATE_SIM": 0.915, "UPDATE_MIN_SIM": 0.65,
                  # Recipes (core/procedures.py), measured 2026-10-04 on the eval: right ones 0.47-0.63, wrong <= 0.41
                  "VEC_MIN_PROCEDURE": 0.44}

    def __init__(self, model: str, url: str):
        self.model, self.url = model, url.rstrip("/")
        self.MODEL_ID = self.MODEL_ID_PREFIX + model
        self.owner = ""               # the owner's first name; MemoryManager sets it once it's known
        self._ready = False
        self._lock = threading.Lock()
        self._failed_at = 0.0

    @property
    def ready(self) -> bool:
        return self._ready

    def _post(self, texts: List[str]) -> np.ndarray:
        import json
        import urllib.request
        body = {"model": self.model, "input": texts, "keep_alive": self.KEEP_ALIVE,
                "options": {"num_thread": self.NUM_THREAD}}
        req = urllib.request.Request(f"{self.url}/api/embed", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.TIMEOUT) as r:
            vecs = np.array(json.loads(r.read())["embeddings"], dtype=np.float32)
        return vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)

    def ensure_loaded(self) -> bool:
        # Ollama starts with the owner's Windows session; the agent never launches it (a detached `ollama serve`
        # popped up a console window every few seconds on 2026-10-04)
        if self._ready:
            return True
        with self._lock:
            if self._ready:
                return True
            if self._failed_at and time.time() - self._failed_at < self.RETRY_SECONDS:
                return False
            try:
                self._post(["ready"])          # loads the model into Ollama
                self._ready, self._failed_at = True, 0.0
                logger.info(f"Memory search model ready ({self.model} via Ollama).")
                return True
            except Exception as e:
                self._failed_at = time.time()
                hint = f" (not pulled? `ollama pull {self.model}`)" if "404" in str(e) else ""
                logger.warning(f"Memory search model unavailable ({self.model}): {e}{hint}; keyword search for now.")
                return False

    def start_background_load(self) -> None:
        threading.Thread(target=self.ensure_loaded, name="MemoryEmbedderLoad", daemon=True).start()

    def embed(self, texts: List[str], query: bool = False, wait: bool = True) -> Optional[np.ndarray]:
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        if not self._ready and (not wait or not self.ensure_loaded()):
            return None
        prefix = ((self.QUERY_PREFIX_NAMED.format(name=self.owner) if self.owner else self.QUERY_PREFIX)
                  if query else "")
        try:
            out = [self._post([prefix + (t or "") for t in texts[i:i + self.BATCH]])
                   for i in range(0, len(texts), self.BATCH)]
            return np.vstack(out)
        except Exception as e:
            # Ollama went away: keyword search until ensure_loaded() reaches it again
            self._ready, self._failed_at = False, time.time()
            logger.warning(f"Memory search model stopped answering ({e}); keyword search for now.")
            return None


_shared: Optional[LocalEmbedder] = None
_shared_lock = threading.Lock()
_memory_shared: Optional[Any] = None


def get_embedder() -> LocalEmbedder:
    """The small in-process model (bge-small): tool routing and web_research. One per process."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = LocalEmbedder()
        return _shared


def get_memory_embedder() -> Any:
    """Memory search's model: MEMORY_EMBED_MODEL through Ollama (default qwen3-embedding:0.6b), or "bge" for the
    in-process bge-small. One per process."""
    global _memory_shared
    with _shared_lock:
        if _memory_shared is None:
            from core.config import get_config
            model = (get_config("MEMORY_EMBED_MODEL") or "qwen3-embedding:0.6b").strip()
            if model.lower() in ("bge", "bge-small", "local"):
                _memory_shared = None
            else:
                _memory_shared = OllamaEmbedder(model, get_config("OLLAMA_URL") or "http://127.0.0.1:11434")
    return _memory_shared or get_embedder()
