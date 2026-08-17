import json
from pathlib import Path
from typing import Any

from maintenance_agent.rag.metadata import (
    APPLICABILITY,
    CONTENT_PROVENANCE,
    EQUIPMENT_TYPE,
    FAULT_CODE_TO_TOPIC,
    REQUIRED_DOCUMENT_METADATA_FIELDS,
    TOPICS,
    validate_document_metadata,
)

SOURCE_DIR = Path("rag/corpus/sources")
DOCUMENT_IDS = {"DOC-01", "DOC-02", "DOC-03", "DOC-04", "DOC-05"}


def parse_source_fixture(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text()
    assert raw.startswith("---\n")
    _, frontmatter_text, body = raw.split("---\n", 2)

    frontmatter: dict[str, Any] = {}
    for line in frontmatter_text.strip().splitlines():
        key, raw_value = line.split(": ", 1)
        value: Any = raw_value
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("[") and value.endswith("]"):
            value = [
                item.strip().strip('"')
                for item in value.removeprefix("[").removesuffix("]").split(",")
                if item.strip()
            ]
        frontmatter[key] = value

    return frontmatter, body


def load_sources() -> dict[str, tuple[dict[str, Any], str]]:
    sources = {}
    for path in SOURCE_DIR.glob("DOC-*.md"):
        frontmatter, body = parse_source_fixture(path)
        sources[frontmatter["document_id"]] = (frontmatter, body)
    return sources


def test_task_1_rag_source_files_exist_with_complete_metadata() -> None:
    sources = load_sources()

    assert set(sources) == DOCUMENT_IDS
    assert {path.name for path in SOURCE_DIR.glob("DOC-*.md")} == {
        f"{document_id}.md" for document_id in DOCUMENT_IDS
    }

    for document_id, (frontmatter, body) in sources.items():
        metadata = validate_document_metadata(frontmatter)

        assert set(frontmatter) == REQUIRED_DOCUMENT_METADATA_FIELDS
        assert frontmatter["document_id"] == document_id
        assert metadata.document_id == document_id
        assert frontmatter["equipment_type"] == EQUIPMENT_TYPE
        assert frontmatter["applicability"] == APPLICABILITY
        assert frontmatter["content_provenance"] == CONTENT_PROVENANCE
        assert frontmatter["topic"] in TOPICS
        assert frontmatter["page"]
        assert frontmatter["source_url"].startswith("https://")
        assert set(frontmatter["linked_fault_codes"]) <= set(FAULT_CODE_TO_TOPIC)
        assert body.lstrip().startswith("# ")
        assert "## " in body
        assert "is a literal manual for CP-200" not in body
        assert "is a literal manual for CP-300" not in body
        assert "CP-200" not in body or "not" in body
        assert "CP-300" not in body or "not" in body


def test_task_1_manifest_lists_exactly_the_source_documents() -> None:
    with (SOURCE_DIR / "manifest.json").open() as manifest_file:
        manifest = json.load(manifest_file)

    sources = load_sources()
    entries = manifest["documents"]

    assert manifest["content_provenance"] == "authored_representative"
    assert {entry["document_id"] for entry in entries} == DOCUMENT_IDS
    assert len(entries) == 5

    for entry in entries:
        document_id = entry["document_id"]
        frontmatter = sources[document_id][0]
        assert entry["path"] == f"{document_id}.md"
        assert (SOURCE_DIR / entry["path"]).exists()
        for field in (
            "manufacturer",
            "source_product_family",
            "section",
            "page",
            "topic",
        ):
            assert entry[field] == frontmatter[field]


def test_task_1_source_content_supports_required_golden_scenario_evidence() -> None:
    sources = {document_id: body for document_id, (_, body) in load_sources().items()}

    expected_terms = {
        "DOC-01": [
            "mechanical seal",
            "lockout",
            "relieve pressure",
            "coupling",
            "leakage",
        ],
        "DOC-02": [
            "low discharge pressure",
            "low flow",
            "seal leakage",
            "no downstream blockage",
            "impeller",
        ],
        "DOC-03": [
            "excessive vibration",
            "coupling",
            "alignment",
            "prior coupling realignment",
            "does not prove",
        ],
        "DOC-04": [
            "bearing temperature",
            "lubricant",
            "recurring",
            "bearing replacement",
            "root-cause investigation",
        ],
        "DOC-05": [
            "low discharge pressure",
            "low flow",
            "no discharge-line blockage",
            "mechanical-seal leak",
            "hypothesis",
        ],
    }

    for document_id, terms in expected_terms.items():
        body = sources[document_id].lower()
        for term in terms:
            assert term.lower() in body
