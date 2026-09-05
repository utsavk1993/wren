"""Turning text into vectors.

The provider sits behind an interface so the knowledge base can be embedded
without an external service when that matters, and so a change of model is a
configuration change rather than a rewrite. The vector width is part of the
database schema, so switching models means re-ingesting everything.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

OPENAI_MODEL = "text-embedding-3-small"
OPENAI_DIMENSIONS = 1536

# Runs in-process with no API key and no network call. Chosen as the default so
# the knowledge base can be built and searched by anyone who clones this,
# and so retrieval quality can be measured without spending anything.
LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
LOCAL_DIMENSIONS = 384

# Embedding the whole knowledge base is one job, so requests are batched rather
# than sent per chunk.
BATCH_SIZE = 64


class Embedder(Protocol):
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model or os.getenv("EMBEDDINGS_MODEL", OPENAI_MODEL)
        self.dimensions = OPENAI_DIMENSIONS
        self._api_key = api_key or os.environ["OPENAI_API_KEY"]
        self._http = http or httpx.AsyncClient(timeout=30.0)
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]
            response = await self._http.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model, "input": batch},
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"embedding request failed: HTTP {response.status_code} "
                    f"{response.text[:200]}"
                )
            payload = response.json()
            # The API is documented to preserve order, but the index is present
            # and a silent misalignment here would attach every chunk to the
            # wrong vector, so it is used rather than trusted.
            ordered = sorted(payload["data"], key=lambda d: d["index"])
            vectors.extend(item["embedding"] for item in ordered)
        return vectors


class LocalEmbedder:
    """Embeds in-process, with no external service involved.

    Slower to start, because the model is loaded on first use, but there is no
    per-call latency to a third party and nothing leaves the machine.
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("EMBEDDINGS_MODEL", LOCAL_MODEL)
        self.dimensions = LOCAL_DIMENSIONS
        self._model = None

    def _loaded(self):
        if self._model is None:
            from fastembed import TextEmbedding

            log.info("loading local embedding model %s", self.model)
            self._model = TextEmbedding(model_name=self.model)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # The library is synchronous and CPU-bound. Handing it to a thread keeps
        # it from blocking everything else on the event loop, which matters
        # during a call even though ingestion runs on its own.
        import asyncio

        def run() -> list[list[float]]:
            return [vector.tolist() for vector in self._loaded().embed(texts)]

        return await asyncio.to_thread(run)


def get_embedder() -> Embedder:
    provider = os.getenv("EMBEDDINGS_PROVIDER", "local").lower()
    if provider == "local":
        return LocalEmbedder()
    if provider == "openai":
        return OpenAIEmbedder()
    raise ValueError(f"unknown embeddings provider: {provider!r}")
