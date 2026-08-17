from pathlib import Path

from maintenance_agent.rag.corpus import (
    CorpusChunk,
    chunk_source_document,
    load_corpus_chunks,
    load_source_documents,
)

SOURCE_DIR = Path("rag/corpus/sources")


def test_task_2_chunks_are_deterministic_and_section_aware() -> None:
    chunks = load_corpus_chunks(SOURCE_DIR)

    assert [chunk.chunk_id for chunk in chunks] == [
        "DOC-01-C1",
        "DOC-01-C2",
        "DOC-01-C3",
        "DOC-02-C1",
        "DOC-02-C2",
        "DOC-02-C3",
        "DOC-03-C1",
        "DOC-03-C2",
        "DOC-03-C3",
        "DOC-04-C1",
        "DOC-04-C2",
        "DOC-04-C3",
        "DOC-05-C1",
        "DOC-05-C2",
    ]
    assert len(chunks) == 14


def test_task_2_chunk_boundaries_match_authored_subheadings() -> None:
    documents = load_source_documents(SOURCE_DIR)

    for document in documents:
        chunks = chunk_source_document(document)
        authored_subheadings = [
            line.removeprefix("## ")
            for line in document.body.splitlines()
            if line.startswith("## ")
        ]

        assert [chunk.chunk_heading for chunk in chunks] == authored_subheadings
        assert_chunk_ids_are_contiguous(chunks)

        for chunk in chunks:
            assert chunk.text.startswith(f"# {document.title}\n\n")
            assert f"## {chunk.chunk_heading}" in chunk.text
            assert chunk.text in document.body or chunk.text.split("\n\n", 1)[1] in document.body


def test_task_2_chunks_are_self_contained_with_document_metadata() -> None:
    chunks = load_corpus_chunks(SOURCE_DIR)

    for chunk in chunks:
        assert chunk.metadata["document_id"] == chunk.document_id
        assert chunk.metadata["applicability"] == "generic_reference"
        assert chunk.metadata["content_provenance"] == "authored_representative"
        assert chunk.text.startswith("# ")
        assert "\n\n## " in chunk.text
        assert chunk.chunk_heading is not None
        assert len(chunk.text.split()) >= 35


def assert_chunk_ids_are_contiguous(chunks: list[CorpusChunk]) -> None:
    for index, chunk in enumerate(chunks, start=1):
        assert chunk.chunk_id == f"{chunk.document_id}-C{index}"
