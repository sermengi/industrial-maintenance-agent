from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.rag.corpus import load_corpus_chunks
from maintenance_agent.rag.embeddings import (
    EMBEDDING_DIMENSION,
    EmbeddingInputType,
    EmbeddingVector,
    to_pgvector_literal,
)
from maintenance_agent.rag.ingestion import (
    compute_content_hash,
    ingest_corpus_chunks,
    reconcile_corpus_chunks,
)


class FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> FakeMappings:
        return FakeMappings(self.rows)


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
    def __init__(self, existing_hashes: dict[str, str] | None = None) -> None:
        self.existing_hashes = existing_hashes or {}
        self.executions: list[dict[str, Any]] = []

    async def execute(
        self,
        statement: object,
        parameters: dict[str, Any] | None = None,
    ) -> FakeResult:
        self.executions.append({"statement": str(statement), "parameters": parameters})
        if "SELECT chunk_id, content_hash FROM rag_chunks" in str(statement):
            return FakeResult(
                [
                    {"chunk_id": chunk_id, "content_hash": content_hash}
                    for chunk_id, content_hash in self.existing_hashes.items()
                ]
            )
        return FakeResult([])


@pytest.mark.asyncio
async def test_task_4_ingestion_embeds_chunk_text_and_upserts_typed_rows() -> None:
    chunks = load_corpus_chunks(Path("rag/corpus/sources"))[:2]
    client = StubEmbeddingClient()
    session = RecordingSession()

    inserted_count = await ingest_corpus_chunks(session, chunks, embedding_client=client)

    assert inserted_count == 2
    assert client.calls == [([chunk.text for chunk in chunks], "document")]
    assert len(session.executions) == 3
    first_execution = session.executions[1]
    assert "INSERT INTO rag_chunks" in first_execution["statement"]
    assert "ON CONFLICT (chunk_id) DO UPDATE" in first_execution["statement"]
    assert "CAST(:embedding AS vector(512))" in first_execution["statement"]
    assert first_execution["parameters"]["chunk_id"] == chunks[0].chunk_id
    assert first_execution["parameters"]["document_id"] == chunks[0].document_id
    assert first_execution["parameters"]["content_hash"] == compute_content_hash(chunks[0].text)
    assert first_execution["parameters"]["linked_fault_codes"] == ["F101", "F103", "F104"]
    assert first_execution["parameters"]["embedding"] == to_pgvector_literal(
        [0.125] * EMBEDDING_DIMENSION
    )


def test_task_4_vector_literal_requires_locked_dimension() -> None:
    with pytest.raises(ValueError, match="512 dimensions"):
        to_pgvector_literal([0.0])


@pytest.mark.asyncio
async def test_task_6_second_unchanged_ingest_skips_embedding_calls() -> None:
    chunks = load_corpus_chunks(Path("rag/corpus/sources"))[:2]
    existing_hashes = {chunk.chunk_id: compute_content_hash(chunk.text) for chunk in chunks}
    client = StubEmbeddingClient()
    session = RecordingSession(existing_hashes)

    result = await reconcile_corpus_chunks(
        cast(AsyncSession, session),
        chunks,
        embedding_client=client,
    )

    assert result.desired_chunks == 2
    assert result.embedded_chunks == 0
    assert result.skipped_chunks == 2
    assert result.pruned_chunks == 0
    assert client.calls == []
    assert all(
        "UPDATE rag_chunks SET" in execution["statement"]
        for execution in session.executions[1:]
    )


@pytest.mark.asyncio
async def test_task_6_prunes_rows_missing_from_current_fixtures() -> None:
    chunks = load_corpus_chunks(Path("rag/corpus/sources"))[:1]
    existing_hashes = {
        chunks[0].chunk_id: compute_content_hash(chunks[0].text),
        "DOC-99-C1": "stale-hash",
    }
    client = StubEmbeddingClient()
    session = RecordingSession(existing_hashes)

    result = await reconcile_corpus_chunks(
        cast(AsyncSession, session),
        chunks,
        embedding_client=client,
    )

    assert result.pruned_chunks == 1
    delete_execution = next(
        execution
        for execution in session.executions
        if "DELETE FROM rag_chunks" in execution["statement"]
    )
    assert delete_execution["parameters"] == {"chunk_ids": ["DOC-99-C1"]}
    assert client.calls == []


@pytest.mark.asyncio
async def test_task_6_changed_text_reembeds_only_that_chunk() -> None:
    original_chunks = load_corpus_chunks(Path("rag/corpus/sources"))[:2]
    changed_chunk = replace(original_chunks[1], text=f"{original_chunks[1].text}\n\nNew detail.")
    chunks = [original_chunks[0], changed_chunk]
    existing_hashes = {
        original_chunks[0].chunk_id: compute_content_hash(original_chunks[0].text),
        original_chunks[1].chunk_id: compute_content_hash(original_chunks[1].text),
    }
    client = StubEmbeddingClient()
    session = RecordingSession(existing_hashes)

    result = await reconcile_corpus_chunks(
        cast(AsyncSession, session),
        chunks,
        embedding_client=client,
    )

    assert result.embedded_chunks == 1
    assert result.skipped_chunks == 1
    assert client.calls == [([changed_chunk.text], "document")]
    upsert_execution = next(
        execution
        for execution in session.executions
        if "INSERT INTO rag_chunks" in execution["statement"]
    )
    assert upsert_execution["parameters"]["chunk_id"] == changed_chunk.chunk_id
    assert upsert_execution["parameters"]["content_hash"] == compute_content_hash(
        changed_chunk.text
    )


@pytest.mark.asyncio
async def test_task_6_force_reembeds_every_chunk_regardless_of_hash_match() -> None:
    chunks = load_corpus_chunks(Path("rag/corpus/sources"))[:3]
    existing_hashes = {chunk.chunk_id: compute_content_hash(chunk.text) for chunk in chunks}
    client = StubEmbeddingClient()
    session = RecordingSession(existing_hashes)

    result = await reconcile_corpus_chunks(
        cast(AsyncSession, session),
        chunks,
        embedding_client=client,
        force=True,
    )

    assert result.embedded_chunks == 3
    assert result.skipped_chunks == 0
    assert client.calls == [([chunk.text for chunk in chunks], "document")]
