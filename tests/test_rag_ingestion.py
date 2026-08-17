from pathlib import Path
from typing import Any

import pytest

from maintenance_agent.rag.corpus import load_corpus_chunks
from maintenance_agent.rag.embeddings import (
    EMBEDDING_DIMENSION,
    EmbeddingInputType,
    EmbeddingVector,
)
from maintenance_agent.rag.ingestion import _vector_literal, ingest_corpus_chunks


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
        return [[0.125] * EMBEDDING_DIMENSION for _ in texts]


class RecordingSession:
    def __init__(self) -> None:
        self.executions: list[dict[str, Any]] = []

    async def execute(self, statement: object, parameters: dict[str, Any]) -> None:
        self.executions.append({"statement": str(statement), "parameters": parameters})


@pytest.mark.asyncio
async def test_task_4_ingestion_embeds_chunk_text_and_upserts_typed_rows() -> None:
    chunks = load_corpus_chunks(Path("rag/corpus/sources"))[:2]
    client = StubEmbeddingClient()
    session = RecordingSession()

    inserted_count = await ingest_corpus_chunks(session, chunks, embedding_client=client)

    assert inserted_count == 2
    assert client.calls == [([chunk.text for chunk in chunks], "document")]
    assert len(session.executions) == 2
    first_execution = session.executions[0]
    assert "INSERT INTO rag_chunks" in first_execution["statement"]
    assert "ON CONFLICT (chunk_id) DO UPDATE" in first_execution["statement"]
    assert "CAST(:embedding AS vector(512))" in first_execution["statement"]
    assert first_execution["parameters"]["chunk_id"] == chunks[0].chunk_id
    assert first_execution["parameters"]["document_id"] == chunks[0].document_id
    assert first_execution["parameters"]["linked_fault_codes"] == ["F101", "F103", "F104"]
    assert first_execution["parameters"]["embedding"] == _vector_literal(
        [0.125] * EMBEDDING_DIMENSION
    )


def test_task_4_vector_literal_requires_locked_dimension() -> None:
    with pytest.raises(ValueError, match="512 dimensions"):
        _vector_literal([0.0])
