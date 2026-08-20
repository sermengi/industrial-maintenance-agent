from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import AssetRecord, FaultEventRecord
from maintenance_agent.orchestration.state import WorkOrderDraft
from maintenance_agent.tools.create_work_order_draft import create_work_order_draft
from maintenance_agent.tools.get_asset_status import ClassifiedReading
from maintenance_agent.tools.search_maintenance_docs import DocSearchHit


@pytest.mark.asyncio
async def test_create_work_order_draft_returns_validated_draft_without_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fault_repositories(monkeypatch, active_faults=[], faults_by_code={})

    draft = await create_work_order_draft(
        issue="Recurring bearing overheating",
        recommended_action="Investigate root cause before planning corrective maintenance.",
        priority="low",
        asset=_asset(),
        request_id="REQ-123",
        structured_evidence=[
            ClassifiedReading(
                source_id="TS-004",
                metric="bearing_temperature_c",
                value=Decimal("96.2"),
                unit="C",
                tier="critical",
                operating_limit_id="OL-002",
                rule_text="Bearing temperature above 90 C is critical.",
            )
        ],
        document_evidence=[_doc_hit("DOC-03")],
        session=cast(AsyncSession, object()),
    )

    assert draft == WorkOrderDraft(
        draft_id="REQ-123",
        asset_id="PUMP-103",
        issue="Recurring bearing overheating",
        recommended_action="Investigate root cause before planning corrective maintenance.",
        priority="low",
        supporting_evidence=["TS-004", "DOC-03"],
    )
    assert "status" not in WorkOrderDraft.model_fields


@pytest.mark.asyncio
async def test_recurring_active_fault_clamps_priority_to_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_fault = _fault("FE-004", datetime(2026, 8, 13, 16, 40, tzinfo=UTC), "active")
    _patch_fault_repositories(
        monkeypatch,
        active_faults=[active_fault],
        faults_by_code={
            "F102": [
                _fault("FE-002", datetime(2026, 1, 14, 10, 20, tzinfo=UTC), "resolved"),
                _fault("FE-003", datetime(2026, 4, 2, 14, 5, tzinfo=UTC), "resolved"),
                active_fault,
            ]
        },
    )

    draft = await create_work_order_draft(
        issue="Recurring bearing overheating",
        recommended_action="Investigate the recurring bearing overheating root cause.",
        priority="low",
        asset=_asset(),
        request_id="REQ-456",
        structured_evidence=[],
        document_evidence=[],
        session=cast(AsyncSession, object()),
    )

    assert draft.priority == "high"


@pytest.mark.asyncio
async def test_supporting_evidence_is_exact_state_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fault_repositories(monkeypatch, active_faults=[], faults_by_code={})

    draft = await create_work_order_draft(
        issue="Bearing overheating",
        recommended_action="Inspect bearing lubrication and alignment.",
        priority="high",
        asset=_asset(),
        request_id="REQ-789",
        structured_evidence=[
            ClassifiedReading(
                source_id="TS-004",
                metric="bearing_temperature_c",
                value=Decimal("96.2"),
                unit="C",
                tier="critical",
                operating_limit_id="OL-002",
                rule_text="Bearing temperature above 90 C is critical.",
            ),
            _fault("FE-004", datetime(2026, 8, 13, 16, 40, tzinfo=UTC), "active"),
        ],
        document_evidence=[_doc_hit("DOC-03"), _doc_hit("DOC-05")],
        session=cast(AsyncSession, object()),
    )

    assert draft.supporting_evidence == ["TS-004", "FE-004", "DOC-03", "DOC-05"]


def _patch_fault_repositories(
    monkeypatch: pytest.MonkeyPatch,
    *,
    active_faults: list[FaultEventRecord],
    faults_by_code: dict[str, list[FaultEventRecord]],
) -> None:
    async def fake_list_active_for_asset(
        _session: AsyncSession,
        asset_id: str,
    ) -> list[FaultEventRecord]:
        assert asset_id == "PUMP-103"
        return active_faults

    async def fake_list_by_asset_and_code(
        _session: AsyncSession,
        asset_id: str,
        fault_code: str,
    ) -> list[FaultEventRecord]:
        assert asset_id == "PUMP-103"
        return faults_by_code[fault_code]

    monkeypatch.setattr(
        "maintenance_agent.tools.create_work_order_draft.fault_events.list_active_for_asset",
        fake_list_active_for_asset,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.fault_recurrence.fault_events.list_by_asset_and_code",
        fake_list_by_asset_and_code,
    )


def _asset() -> AssetRecord:
    return AssetRecord(
        asset_id="PUMP-103",
        asset_type="centrifugal_pump",
        model="CP-200",
        location="Line 3",
        installation_date=date(2021, 6, 1),
        status="operational",
    )


def _fault(event_id: str, timestamp: datetime, status: str) -> FaultEventRecord:
    return FaultEventRecord(
        event_id=event_id,
        asset_id="PUMP-103",
        fault_code="F102",
        fault_name="HIGH_BEARING_TEMPERATURE",
        timestamp=timestamp,
        severity="high",
        status=status,
    )


def _doc_hit(document_id: str) -> DocSearchHit:
    return DocSearchHit(
        chunk_id=f"{document_id}-C1",
        document_id=document_id,
        section="Bearing overheating",
        page="1",
        topic="pump maintenance",
        manufacturer="ACME",
        source_product_family="CP",
        applicability="CP-200",
        source_url="https://example.test/doc",
        content_provenance="fixture",
        linked_fault_codes=["F102"],
        evidence_text="Inspect lubrication and alignment before replacing components.",
        similarity_score=0.9,
    )
