from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.rag.corpus import CorpusChunk
from maintenance_agent.rag.embeddings import EMBEDDING_DIMENSION, EmbeddingClient, embed


async def ingest_corpus_chunks(
    session: AsyncSession,
    chunks: Sequence[CorpusChunk],
    *,
    embedding_client: EmbeddingClient,
) -> int:
    if not chunks:
        return 0

    vectors = embed(
        [chunk.text for chunk in chunks],
        client=embedding_client,
        input_type="document",
    )
    for chunk, vector in zip(chunks, vectors, strict=True):
        await session.execute(
            text(
                """
                INSERT INTO rag_chunks (
                    chunk_id,
                    document_id,
                    chunk_heading,
                    text,
                    manufacturer,
                    source_product_family,
                    section,
                    page,
                    equipment_type,
                    applicability,
                    source_url,
                    content_provenance,
                    topic,
                    linked_fault_codes,
                    embedding
                )
                VALUES (
                    :chunk_id,
                    :document_id,
                    :chunk_heading,
                    :text,
                    :manufacturer,
                    :source_product_family,
                    :section,
                    :page,
                    :equipment_type,
                    :applicability,
                    :source_url,
                    :content_provenance,
                    :topic,
                    :linked_fault_codes,
                    CAST(:embedding AS vector(512))
                )
                ON CONFLICT (chunk_id) DO UPDATE SET
                    document_id = EXCLUDED.document_id,
                    chunk_heading = EXCLUDED.chunk_heading,
                    text = EXCLUDED.text,
                    manufacturer = EXCLUDED.manufacturer,
                    source_product_family = EXCLUDED.source_product_family,
                    section = EXCLUDED.section,
                    page = EXCLUDED.page,
                    equipment_type = EXCLUDED.equipment_type,
                    applicability = EXCLUDED.applicability,
                    source_url = EXCLUDED.source_url,
                    content_provenance = EXCLUDED.content_provenance,
                    topic = EXCLUDED.topic,
                    linked_fault_codes = EXCLUDED.linked_fault_codes,
                    embedding = EXCLUDED.embedding
                """
            ),
            {
                **chunk.metadata.to_dict(),
                "chunk_id": chunk.chunk_id,
                "chunk_heading": chunk.chunk_heading,
                "text": chunk.text,
                "embedding": _vector_literal(vector),
            },
        )

    return len(chunks)


def _vector_literal(vector: Sequence[float]) -> str:
    if len(vector) != EMBEDDING_DIMENSION:
        raise ValueError(f"embedding must have {EMBEDDING_DIMENSION} dimensions")
    return "[" + ",".join(str(float(value)) for value in vector) + "]"
