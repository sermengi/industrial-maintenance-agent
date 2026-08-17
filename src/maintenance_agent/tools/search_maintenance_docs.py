from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.rag.embeddings import embed, to_pgvector_literal

TOP_K = 3
MIN_SIMILARITY_SCORE = 0.2


class DocSearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    section: str
    page: str
    topic: str
    manufacturer: str
    source_product_family: str
    applicability: str
    source_url: str
    content_provenance: str
    linked_fault_codes: list[str]
    evidence_text: str
    similarity_score: float


class SearchMaintenanceDocsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    results: list[DocSearchHit] = Field(default_factory=list)


async def search_maintenance_docs(
    query: str,
    session: AsyncSession,
) -> SearchMaintenanceDocsResult:
    normalized_query = query.strip()
    if not normalized_query:
        return SearchMaintenanceDocsResult(query=query)

    query_vector = embed([normalized_query], input_type="query")[0]
    result = await session.execute(
        text(
            """
            SELECT
                chunk_id,
                document_id,
                section,
                page,
                topic,
                manufacturer,
                source_product_family,
                applicability,
                source_url,
                content_provenance,
                linked_fault_codes,
                text AS evidence_text,
                1 - (embedding <=> CAST(:query_embedding AS vector(512))) AS similarity_score
            FROM rag_chunks
            WHERE 1 - (embedding <=> CAST(:query_embedding AS vector(512))) >= :min_similarity
            ORDER BY embedding <=> CAST(:query_embedding AS vector(512)), chunk_id
            LIMIT :top_k
            """
        ),
        {
            "query_embedding": to_pgvector_literal(query_vector),
            "min_similarity": MIN_SIMILARITY_SCORE,
            "top_k": TOP_K,
        },
    )

    return SearchMaintenanceDocsResult(
        query=query,
        results=[_row_to_hit(row) for row in result.mappings().all()],
    )


def _row_to_hit(row: Any) -> DocSearchHit:
    return DocSearchHit(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        section=row["section"],
        page=row["page"],
        topic=row["topic"],
        manufacturer=row["manufacturer"],
        source_product_family=row["source_product_family"],
        applicability=row["applicability"],
        source_url=row["source_url"],
        content_provenance=row["content_provenance"],
        linked_fault_codes=list(row["linked_fault_codes"]),
        evidence_text=row["evidence_text"],
        similarity_score=float(row["similarity_score"]),
    )
