from datetime import date
from typing import cast, get_type_hints

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import WorkOrderRecord
from maintenance_agent.orchestration.state import WorkOrderDraft
from maintenance_agent.tools.submit_work_order import (
    ConsequentialActionGuardError,
    submit_work_order,
)


@pytest.mark.asyncio
async def test_submit_work_order_inserts_submitted_approved_record_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _draft()
    session = _CommitRecordingSession()
    calls: list[tuple[AsyncSession, str, str, str, date]] = []

    async def fake_create_submitted(
        session: AsyncSession,
        *,
        asset_id: str,
        issue: str,
        priority: str,
        created_at: date,
    ) -> WorkOrderRecord:
        calls.append((session, asset_id, issue, priority, created_at))
        return WorkOrderRecord(
            work_order_id="WO-003",
            asset_id=asset_id,
            issue=issue,
            priority=priority,
            status="submitted",
            created_at=created_at,
            approved=True,
        )

    monkeypatch.setattr(
        "maintenance_agent.tools.submit_work_order.work_orders.create_submitted",
        fake_create_submitted,
    )

    result = await submit_work_order(
        draft,
        approval_status="approved",
        session=cast(AsyncSession, session),
        clock=lambda: date(2026, 8, 20),
    )

    assert result == WorkOrderRecord(
        work_order_id="WO-003",
        asset_id="PUMP-103",
        issue="Recurring bearing overheating",
        priority="high",
        status="submitted",
        created_at=date(2026, 8, 20),
        approved=True,
    )
    assert calls == [
        (
            cast(AsyncSession, session),
            "PUMP-103",
            "Recurring bearing overheating",
            "high",
            date(2026, 8, 20),
        )
    ]
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_submit_work_order_guard_blocks_insert_and_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CommitRecordingSession()

    async def fail_if_called(*args: object, **kwargs: object) -> WorkOrderRecord:
        raise AssertionError("create_submitted should not be called")

    monkeypatch.setattr(
        "maintenance_agent.tools.submit_work_order.work_orders.create_submitted",
        fail_if_called,
    )

    with pytest.raises(ConsequentialActionGuardError, match="approval_status='approved'"):
        await submit_work_order(
            _draft(),
            approval_status="pending_approval",
            session=cast(AsyncSession, session),
        )

    assert session.commit_count == 0


def test_submit_work_order_returns_work_order_record_type() -> None:
    assert get_type_hints(submit_work_order)["return"] is WorkOrderRecord


class _CommitRecordingSession:
    def __init__(self) -> None:
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1


def _draft() -> WorkOrderDraft:
    return WorkOrderDraft(
        draft_id="REQ-123",
        asset_id="PUMP-103",
        issue="Recurring bearing overheating",
        recommended_action="Investigate root cause.",
        priority="high",
        supporting_evidence=["FE-004"],
    )
