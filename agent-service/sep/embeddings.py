"""Embedding provider (local Ollama by default).

nomic-embed-text is trained with task prefixes: documents and queries are
embedded with different prefixes, which measurably improves retrieval.
"""
from __future__ import annotations

from functools import lru_cache

import httpx
import numpy as np

from .config import get_settings


class EmbeddingError(RuntimeError):
    pass


class OllamaEmbeddings:
    def __init__(self, base_url: str, model: str, dim: int):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self._prefixed = "nomic" in model

    def _embed(self, texts: list[str]) -> list[np.ndarray]:
        try:
            r = httpx.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": texts}, timeout=120)
        except httpx.HTTPError as e:
            raise EmbeddingError(f"Ollama not reachable at {self.base_url}: {e}") from e
        if r.status_code != 200:
            raise EmbeddingError(f"Ollama error {r.status_code}: {r.text[:300]}")
        vectors = [np.asarray(v, dtype=np.float32) for v in r.json()["embeddings"]]
        for v in vectors:
            if v.shape[0] != self.dim:
                raise EmbeddingError(f"Expected dim {self.dim}, got {v.shape[0]}")
        return vectors

    def embed_documents(self, texts: list[str], batch_size: int = 32) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            if self._prefixed:
                batch = [f"search_document: {t}" for t in batch]
            out.extend(self._embed(batch))
        return out

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([f"search_query: {text}" if self._prefixed else text])[0]

    def pull_model(self) -> None:
        """Make sure the model is available (used by the setup script)."""
        r = httpx.post(f"{self.base_url}/api/pull", json={"model": self.model, "stream": False}, timeout=900)
        if r.status_code != 200:
            raise EmbeddingError(f"Could not pull {self.model}: {r.text[:300]}")


@lru_cache
def get_embeddings() -> OllamaEmbeddings:
    s = get_settings()
    if s.embedding_provider != "ollama":
        raise EmbeddingError(f"Unsupported embedding provider {s.embedding_provider}")
    return OllamaEmbeddings(s.ollama_base_url, s.embedding_model, s.embedding_dim)
