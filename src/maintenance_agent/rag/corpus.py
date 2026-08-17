from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "rag" / "corpus" / "sources"
DOCUMENT_HEADING_PATTERN = re.compile(r"^# (.+)$", re.MULTILINE)
CHUNK_HEADING_PATTERN = re.compile(r"^## (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class SourceDocument:
    metadata: dict[str, Any]
    title: str
    preamble: str
    body: str
    path: Path

    @property
    def document_id(self) -> str:
        return str(self.metadata["document_id"])


@dataclass(frozen=True)
class CorpusChunk:
    chunk_id: str
    document_id: str
    chunk_heading: str | None
    text: str
    metadata: dict[str, Any]


def parse_frontmatter_value(raw_value: str) -> str | list[str]:
    value = raw_value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [
            item.strip().strip('"')
            for item in value.removeprefix("[").removesuffix("]").split(",")
            if item.strip()
        ]
    return value


def parse_source_document(path: Path) -> SourceDocument:
    raw = path.read_text()
    if not raw.startswith("---\n"):
        raise ValueError(f"{path} must start with YAML frontmatter")

    try:
        _, frontmatter_text, body = raw.split("---\n", 2)
    except ValueError as exc:
        raise ValueError(f"{path} has invalid frontmatter delimiters") from exc

    metadata: dict[str, Any] = {}
    for line in frontmatter_text.strip().splitlines():
        key, raw_value = line.split(": ", 1)
        metadata[key] = parse_frontmatter_value(raw_value)

    normalized_body = body.strip()
    title_match = DOCUMENT_HEADING_PATTERN.search(normalized_body)
    if title_match is None:
        raise ValueError(f"{path} must contain one top-level Markdown heading")

    title = title_match.group(1)
    after_title = normalized_body[title_match.end() :].lstrip()
    first_chunk_heading = CHUNK_HEADING_PATTERN.search(after_title)
    if first_chunk_heading is None:
        preamble = after_title
    else:
        preamble = after_title[: first_chunk_heading.start()].strip()

    return SourceDocument(
        metadata=metadata,
        title=title,
        preamble=preamble,
        body=normalized_body,
        path=path,
    )


def chunk_source_document(document: SourceDocument) -> list[CorpusChunk]:
    matches = list(CHUNK_HEADING_PATTERN.finditer(document.body))
    if not matches:
        return [
            CorpusChunk(
                chunk_id=f"{document.document_id}-C1",
                document_id=document.document_id,
                chunk_heading=None,
                text=document.body,
                metadata=document.metadata,
            )
        ]

    chunks: list[CorpusChunk] = []
    for index, match in enumerate(matches, start=1):
        next_start = matches[index].start() if index < len(matches) else len(document.body)
        section_text = document.body[match.start() : next_start].strip()
        chunk_text = f"# {document.title}\n\n{section_text}"
        chunks.append(
            CorpusChunk(
                chunk_id=f"{document.document_id}-C{index}",
                document_id=document.document_id,
                chunk_heading=match.group(1),
                text=chunk_text,
                metadata=document.metadata,
            )
        )

    return chunks


def load_source_documents(source_dir: Path = DEFAULT_SOURCE_DIR) -> list[SourceDocument]:
    manifest_path = source_dir / "manifest.json"
    with manifest_path.open() as manifest_file:
        manifest = json.load(manifest_file)

    documents = []
    for entry in manifest["documents"]:
        documents.append(parse_source_document(source_dir / entry["path"]))

    return documents


def load_corpus_chunks(source_dir: Path = DEFAULT_SOURCE_DIR) -> list[CorpusChunk]:
    chunks: list[CorpusChunk] = []
    for document in load_source_documents(source_dir):
        chunks.extend(chunk_source_document(document))
    return chunks
