from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

type EquipmentType = Literal["centrifugal_pump"]
type Applicability = Literal["generic_reference"]
type ContentProvenance = Literal["authored_representative"]
type Topic = Literal[
    "HIGH_VIBRATION",
    "HIGH_BEARING_TEMPERATURE",
    "LOW_DISCHARGE_PRESSURE",
    "INSPECTION_PROCEDURE",
]

EQUIPMENT_TYPE: EquipmentType = "centrifugal_pump"
APPLICABILITY: Applicability = "generic_reference"
CONTENT_PROVENANCE: ContentProvenance = "authored_representative"
TOPICS: frozenset[str] = frozenset(
    {
        "HIGH_VIBRATION",
        "HIGH_BEARING_TEMPERATURE",
        "LOW_DISCHARGE_PRESSURE",
        "INSPECTION_PROCEDURE",
    }
)
FAULT_CODE_TO_TOPIC: Mapping[str, str] = {
    "F101": "HIGH_VIBRATION",
    "F102": "HIGH_BEARING_TEMPERATURE",
    "F103": "LOW_DISCHARGE_PRESSURE",
    "F104": "SEAL_LEAK_DETECTED",
}
REQUIRED_DOCUMENT_METADATA_FIELDS: frozenset[str] = frozenset(
    {
        "document_id",
        "manufacturer",
        "source_product_family",
        "section",
        "page",
        "equipment_type",
        "applicability",
        "source_url",
        "content_provenance",
        "topic",
        "linked_fault_codes",
    }
)


@dataclass(frozen=True)
class DocumentMetadata:
    document_id: str
    manufacturer: str
    source_product_family: str
    section: str
    page: str
    equipment_type: EquipmentType
    applicability: Applicability
    source_url: str
    content_provenance: ContentProvenance
    topic: Topic
    linked_fault_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, str | list[str]]:
        values = asdict(self)
        values["linked_fault_codes"] = list(self.linked_fault_codes)
        return cast(dict[str, str | list[str]], values)


@dataclass(frozen=True)
class ChunkMetadata:
    chunk_id: str
    chunk_heading: str | None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    document_id: str
    chunk_heading: str | None
    text: str
    document_metadata: DocumentMetadata

    def metadata_dict(self) -> dict[str, str | list[str] | None]:
        return {
            **self.document_metadata.to_dict(),
            "chunk_id": self.chunk_id,
            "chunk_heading": self.chunk_heading,
        }


def validate_document_metadata(raw_metadata: Mapping[str, Any]) -> DocumentMetadata:
    fields = set(raw_metadata)
    if fields != REQUIRED_DOCUMENT_METADATA_FIELDS:
        missing = sorted(REQUIRED_DOCUMENT_METADATA_FIELDS - fields)
        extra = sorted(fields - REQUIRED_DOCUMENT_METADATA_FIELDS)
        raise ValueError(f"invalid document metadata fields: missing={missing}, extra={extra}")

    linked_fault_codes = raw_metadata["linked_fault_codes"]
    if not isinstance(linked_fault_codes, list) or not all(
        isinstance(code, str) for code in linked_fault_codes
    ):
        raise ValueError("linked_fault_codes must be a list of strings")

    document = DocumentMetadata(
        document_id=_required_str(raw_metadata, "document_id"),
        manufacturer=_required_str(raw_metadata, "manufacturer"),
        source_product_family=_required_str(raw_metadata, "source_product_family"),
        section=_required_str(raw_metadata, "section"),
        page=_required_str(raw_metadata, "page"),
        equipment_type=_literal(
            raw_metadata,
            "equipment_type",
            {EQUIPMENT_TYPE},
            "equipment_type",
        ),
        applicability=_literal(
            raw_metadata,
            "applicability",
            {APPLICABILITY},
            "applicability",
        ),
        source_url=_required_str(raw_metadata, "source_url"),
        content_provenance=_literal(
            raw_metadata,
            "content_provenance",
            {CONTENT_PROVENANCE},
            "content_provenance",
        ),
        topic=_literal(raw_metadata, "topic", TOPICS, "topic"),
        linked_fault_codes=tuple(linked_fault_codes),
    )

    invalid_fault_codes = sorted(set(document.linked_fault_codes) - set(FAULT_CODE_TO_TOPIC))
    if invalid_fault_codes:
        raise ValueError(f"linked_fault_codes contains unknown fault codes: {invalid_fault_codes}")

    if document.topic != "INSPECTION_PROCEDURE":
        linked_topics = {FAULT_CODE_TO_TOPIC[code] for code in document.linked_fault_codes}
        if document.topic not in linked_topics:
            raise ValueError("non-procedural topic must be represented in linked_fault_codes")

    return document


def _required_str(raw_metadata: Mapping[str, Any], key: str) -> str:
    value = raw_metadata[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a populated string")
    return value


def _literal[T: str](
    raw_metadata: Mapping[str, Any],
    key: str,
    allowed_values: set[T] | frozenset[str],
    field_name: str,
) -> T:
    value = raw_metadata[key]
    if not isinstance(value, str) or value not in allowed_values:
        raise ValueError(f"{field_name} must be one of {sorted(allowed_values)}")
    return cast(T, value)
