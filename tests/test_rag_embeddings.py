import pytest

from maintenance_agent.core.config import get_settings
from maintenance_agent.rag.embeddings import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    DeterministicEmbeddingClient,
    EmbeddingInputType,
    EmbeddingVector,
    embed,
    get_embedding_client,
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()


class StubEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], EmbeddingInputType]] = []

    def embed(
        self,
        texts: list[str],
        *,
        input_type: EmbeddingInputType,
    ) -> list[EmbeddingVector]:
        self.calls.append((texts, input_type))
        return [[float(index)] * EMBEDDING_DIMENSION for index, _ in enumerate(texts)]


def test_task_4_embed_uses_injected_client_without_api_key() -> None:
    client = StubEmbeddingClient()

    vectors = embed(["alpha", "beta"], client=client, input_type="document")

    assert client.calls == [(["alpha", "beta"], "document")]
    assert vectors == [[0.0] * EMBEDDING_DIMENSION, [1.0] * EMBEDDING_DIMENSION]


def test_task_4_real_embedding_backend_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("RAG_EMBEDDING_BACKEND", raising=False)

    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY"):
        get_embedding_client()


def test_task_6_mock_embedding_backend_uses_deterministic_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_BACKEND", "mock")

    client = get_embedding_client()

    assert isinstance(client, DeterministicEmbeddingClient)
    assert client.embed(["alpha"], input_type="document") == client.embed(
        ["alpha"],
        input_type="document",
    )


def test_task_4_embedding_model_dimension_is_locked_to_voyage_3_lite() -> None:
    assert EMBEDDING_MODEL == "voyage-3-lite"
    assert EMBEDDING_DIMENSION == 512


def test_task_4_embed_rejects_wrong_vector_dimensions() -> None:
    class BadClient:
        def embed(
            self,
            texts: list[str],
            *,
            input_type: EmbeddingInputType,
        ) -> list[EmbeddingVector]:
            return [[0.0]]

    with pytest.raises(ValueError, match="512-dimension"):
        embed(["alpha"], client=BadClient(), input_type="query")
