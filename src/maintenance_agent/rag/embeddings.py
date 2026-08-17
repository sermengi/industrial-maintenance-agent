from __future__ import annotations

import json
from collections.abc import Sequence
from hashlib import sha256
from typing import Literal, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from maintenance_agent.core.config import get_settings

EMBEDDING_MODEL = "voyage-3-lite"
EMBEDDING_DIMENSION = 512
VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"
EmbeddingInputType = Literal["document", "query"]
EmbeddingVector = list[float]


class EmbeddingClient(Protocol):
    def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> list[EmbeddingVector]:
        pass


class VoyageEmbeddingClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = EMBEDDING_MODEL,
        api_url: str = VOYAGE_EMBEDDINGS_URL,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.api_url = api_url

    def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> list[EmbeddingVector]:
        if not texts:
            return []

        payload = json.dumps(
            {
                "input": list(texts),
                "model": self.model,
                "input_type": input_type,
            }
        ).encode("utf-8")
        request = Request(
            self.api_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=30) as response:
                raw_response = response.read()
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Voyage embedding request failed: {exc.code} {error_body}") from exc

        response_data = json.loads(raw_response)
        embeddings = [item["embedding"] for item in response_data["data"]]
        _validate_embedding_response(embeddings, len(texts))
        return embeddings


class DeterministicEmbeddingClient:
    def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> list[EmbeddingVector]:
        return [_deterministic_vector(text, input_type=input_type) for text in texts]


def embed(
    texts: Sequence[str],
    *,
    client: EmbeddingClient | None = None,
    input_type: EmbeddingInputType = "document",
) -> list[EmbeddingVector]:
    embedding_client = client if client is not None else get_embedding_client()
    vectors = embedding_client.embed(texts, input_type=input_type)
    _validate_embedding_response(vectors, len(texts))
    return vectors


def get_embedding_client() -> EmbeddingClient:
    settings = get_settings()
    if settings.rag_embedding_backend == "mock":
        return DeterministicEmbeddingClient()
    if settings.rag_embedding_backend != "voyage":
        raise RuntimeError("RAG_EMBEDDING_BACKEND must be either 'voyage' or 'mock'.")
    if not settings.voyage_api_key:
        raise RuntimeError("VOYAGE_API_KEY is required for the real embedding backend.")
    return VoyageEmbeddingClient(settings.voyage_api_key)


def _validate_embedding_response(vectors: list[EmbeddingVector], expected_count: int) -> None:
    if len(vectors) != expected_count:
        raise ValueError(f"expected {expected_count} embeddings, received {len(vectors)}")
    invalid_dimensions = [len(vector) for vector in vectors if len(vector) != EMBEDDING_DIMENSION]
    if invalid_dimensions:
        raise ValueError(
            f"expected {EMBEDDING_DIMENSION}-dimension embeddings, "
            f"received dimensions {invalid_dimensions}"
        )


def to_pgvector_literal(vector: Sequence[float]) -> str:
    if len(vector) != EMBEDDING_DIMENSION:
        raise ValueError(f"embedding must have {EMBEDDING_DIMENSION} dimensions")
    return "[" + ",".join(str(float(value)) for value in vector) + "]"


def _deterministic_vector(text: str, *, input_type: EmbeddingInputType) -> EmbeddingVector:
    seed = f"{input_type}:{text}".encode()
    digest = sha256(seed).digest()
    values: list[float] = []
    while len(values) < EMBEDDING_DIMENSION:
        for byte in digest:
            values.append((byte / 127.5) - 1.0)
            if len(values) == EMBEDDING_DIMENSION:
                break
        digest = sha256(digest).digest()
    return values
