from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.rag.corpus import CorpusChunk
from maintenance_agent.rag.embeddings import (
    EmbeddingClient,
    embed,
    get_embedding_client,
    to_pgvector_literal,
)


@dataclass(frozen=True)
class RagIngestionResult:
    desired_chunks: int
    embedded_chunks: int
    skipped_chunks: int
    pruned_chunks: int


async def ingest_corpus_chunks(
    session: AsyncSession,
    chunks: Sequence[CorpusChunk],
    *,
    embedding_client: EmbeddingClient,
) -> int:
    if not chunks:
        return 0

    result = await reconcile_corpus_chunks(
        session,
        chunks,
        embedding_client=embedding_client,
        force=True,
    )
    return result.desired_chunks


async def ingest_loaded_corpus(
    session: AsyncSession,
    chunks: Sequence[CorpusChunk],
    *,
    force: bool = False,
    embedding_client: EmbeddingClient | None = None,
) -> RagIngestionResult:
    return await reconcile_corpus_chunks(
        session,
        chunks,
        embedding_client=embedding_client or get_embedding_client(),
        force=force,
    )


async def reconcile_corpus_chunks(
    session: AsyncSession,
    chunks: Sequence[CorpusChunk],
    *,
    embedding_client: EmbeddingClient,
    force: bool = False,
) -> RagIngestionResult:
    desired_hashes = {chunk.chunk_id: compute_content_hash(chunk.text) for chunk in chunks}
    existing_hashes = await _load_existing_content_hashes(session)
    stale_chunk_ids = sorted(set(existing_hashes) - set(desired_hashes))
    await _prune_stale_chunks(session, stale_chunk_ids)

    changed_chunks = [
        chunk
        for chunk in chunks
        if force or existing_hashes.get(chunk.chunk_id) != desired_hashes[chunk.chunk_id]
    ]
    changed_chunk_ids = {chunk.chunk_id for chunk in changed_chunks}
    unchanged_chunks = [chunk for chunk in chunks if chunk.chunk_id not in changed_chunk_ids]

    if changed_chunks:
        vectors = embed(
            [chunk.text for chunk in changed_chunks],
            client=embedding_client,
            input_type="document",
        )
        for chunk, vector in zip(changed_chunks, vectors, strict=True):
            await _upsert_chunk_with_embedding(
                session,
                chunk,
                content_hash=desired_hashes[chunk.chunk_id],
                embedding=to_pgvector_literal(vector),
            )

    for chunk in unchanged_chunks:
        await _refresh_chunk_metadata(
            session,
            chunk,
            content_hash=desired_hashes[chunk.chunk_id],
        )

    return RagIngestionResult(
        desired_chunks=len(chunks),
        embedded_chunks=len(changed_chunks),
        skipped_chunks=len(unchanged_chunks),
        pruned_chunks=len(stale_chunk_ids),
    )


def compute_content_hash(text_value: str) -> str:
    return sha256(text_value.encode("utf-8")).hexdigest()


async def _load_existing_content_hashes(session: AsyncSession) -> dict[str, str]:
    result = await session.execute(text("SELECT chunk_id, content_hash FROM rag_chunks"))
    return {row["chunk_id"]: row["content_hash"] for row in result.mappings().all()}


async def _prune_stale_chunks(session: AsyncSession, stale_chunk_ids: Sequence[str]) -> None:
    if not stale_chunk_ids:
        return
    await session.execute(
        text("DELETE FROM rag_chunks WHERE chunk_id IN :chunk_ids").bindparams(
            bindparam("chunk_ids", expanding=True)
        ),
        {"chunk_ids": list(stale_chunk_ids)},
    )


async def _upsert_chunk_with_embedding(
    session: AsyncSession,
    chunk: CorpusChunk,
    *,
    content_hash: str,
    embedding: str,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO rag_chunks (
                chunk_id,
                document_id,
                chunk_heading,
                text,
                content_hash,
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
                :content_hash,
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
                content_hash = EXCLUDED.content_hash,
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
        _chunk_parameters(chunk, content_hash=content_hash, embedding=embedding),
    )


async def _refresh_chunk_metadata(
    session: AsyncSession,
    chunk: CorpusChunk,
    *,
    content_hash: str,
) -> None:
    await session.execute(
        text(
            """
            UPDATE rag_chunks SET
                document_id = :document_id,
                chunk_heading = :chunk_heading,
                text = :text,
                content_hash = :content_hash,
                manufacturer = :manufacturer,
                source_product_family = :source_product_family,
                section = :section,
                page = :page,
                equipment_type = :equipment_type,
                applicability = :applicability,
                source_url = :source_url,
                content_provenance = :content_provenance,
                topic = :topic,
                linked_fault_codes = :linked_fault_codes
            WHERE chunk_id = :chunk_id
            """
        ),
        _chunk_parameters(chunk, content_hash=content_hash),
    )


def _chunk_parameters(
    chunk: CorpusChunk,
    *,
    content_hash: str,
    embedding: str | None = None,
) -> dict[str, str | list[str] | None]:
    parameters = {
        **chunk.metadata.to_dict(),
        "chunk_id": chunk.chunk_id,
        "chunk_heading": chunk.chunk_heading,
        "text": chunk.text,
        "content_hash": content_hash,
    }
    if embedding is not None:
        parameters["embedding"] = embedding
    return parameters
