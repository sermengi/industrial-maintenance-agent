from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.rag.embeddings import EMBEDDING_DIMENSION, EmbeddingInputType
from maintenance_agent.tools import search_maintenance_docs as tool_module
from maintenance_agent.tools.search_maintenance_docs import (
    MIN_SIMILARITY_SCORE,
    TOP_K,
    SearchMaintenanceDocsResult,
    search_maintenance_docs,
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


class RecordingSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executions: list[dict[str, Any]] = []

    async def execute(self, statement: object, parameters: dict[str, Any]) -> FakeResult:
        self.executions.append({"statement": str(statement), "parameters": parameters})
        return FakeResult(self.rows)


def fake_row(
    *,
    chunk_id: str,
    document_id: str,
    topic: str,
    similarity_score: float,
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "section": "7.11 The pump vibrates and generates too much noise",
        "page": "28",
        "topic": topic,
        "manufacturer": "Xylem",
        "source_product_family": "Series 1710",
        "applicability": "generic_reference",
        "source_url": "https://example.test/manual.pdf",
        "content_provenance": "authored_representative",
        "linked_fault_codes": ["F101"],
        "evidence_text": "Evidence text",
        "similarity_score": similarity_score,
    }


@pytest.mark.asyncio
async def test_task_5_search_embeds_query_and_returns_full_source_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embed_calls: list[tuple[list[str], EmbeddingInputType]] = []

    def fake_embed(texts: list[str], *, input_type: EmbeddingInputType) -> list[list[float]]:
        embed_calls.append((texts, input_type))
        return [[0.25] * EMBEDDING_DIMENSION]

    monkeypatch.setattr(tool_module, "embed", fake_embed)
    session = RecordingSession(
        [
            fake_row(
                chunk_id="DOC-03-C1",
                document_id="DOC-03",
                topic="HIGH_VIBRATION",
                similarity_score=0.91,
            )
        ]
    )

    result = await search_maintenance_docs(
        " excessive pump vibration ",
        cast(AsyncSession, session),
    )

    assert isinstance(result, SearchMaintenanceDocsResult)
    assert result.query == " excessive pump vibration "
    assert embed_calls == [(["excessive pump vibration"], "query")]
    assert len(result.results) == 1
    hit = result.results[0]
    assert hit.chunk_id == "DOC-03-C1"
    assert hit.document_id == "DOC-03"
    assert hit.topic == "HIGH_VIBRATION"
    assert hit.applicability == "generic_reference"
    assert hit.content_provenance == "authored_representative"
    assert hit.linked_fault_codes == ["F101"]
    assert hit.evidence_text == "Evidence text"
    assert hit.similarity_score == 0.91


@pytest.mark.asyncio
async def test_task_5_search_uses_pure_semantic_pgvector_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tool_module,
        "embed",
        lambda texts, input_type: [[0.5] * EMBEDDING_DIMENSION],
    )
    session = RecordingSession([])

    result = await search_maintenance_docs("bearing overheating", cast(AsyncSession, session))

    assert result.results == []
    execution = session.executions[0]
    statement = execution["statement"]
    assert "ORDER BY embedding <=> CAST(:query_embedding AS vector(512)), chunk_id" in statement
    assert (
        "WHERE 1 - (embedding <=> CAST(:query_embedding AS vector(512))) >= :min_similarity"
        in statement
    )
    assert "linked_fault_codes" in statement
    assert "linked_fault_codes =" not in statement
    assert execution["parameters"]["top_k"] == TOP_K
    assert execution["parameters"]["min_similarity"] == MIN_SIMILARITY_SCORE


@pytest.mark.asyncio
async def test_task_5_blank_query_returns_empty_result_without_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_embed(_texts: list[str], *, input_type: EmbeddingInputType) -> list[list[float]]:
        raise AssertionError("blank query should not be embedded")

    monkeypatch.setattr(tool_module, "embed", failing_embed)
    session = RecordingSession([])

    result = await search_maintenance_docs("   ", cast(AsyncSession, session))

    assert result == SearchMaintenanceDocsResult(query="   ")
    assert session.executions == []


@pytest.mark.asyncio
async def test_task_5_stable_rows_return_stable_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tool_module,
        "embed",
        lambda texts, input_type: [[0.75] * EMBEDDING_DIMENSION],
    )
    rows = [
        fake_row(
            chunk_id="DOC-04-C1",
            document_id="DOC-04",
            topic="HIGH_BEARING_TEMPERATURE",
            similarity_score=0.93,
        ),
        fake_row(
            chunk_id="DOC-01-C2",
            document_id="DOC-01",
            topic="INSPECTION_PROCEDURE",
            similarity_score=0.82,
        ),
    ]
    session = RecordingSession(rows)

    first = await search_maintenance_docs(
        "bearing temperature inspection",
        cast(AsyncSession, session),
    )
    second = await search_maintenance_docs(
        "bearing temperature inspection",
        cast(AsyncSession, session),
    )

    assert [hit.chunk_id for hit in first.results] == ["DOC-04-C1", "DOC-01-C2"]
    assert first == second
